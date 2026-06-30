import logging
import requests
import asyncio
import time

from settings import (
    DEFAULT_LLM_TIMEOUT,
    GROQ_API_KEY, GROQ_MODEL,
    GEMINI_API_KEY, GEMINI_MODEL,
    HF_API_TOKEN, HF_MODEL,
    LLM_PROVIDER,
    LOCAL_LLM_ENABLED, LOCAL_LLM_API_BASE, LOCAL_LLM_MODEL, LOCAL_LLM_TIMEOUT,
    VLLM_API_BASE, VLLM_MODEL,
)

logger = logging.getLogger("avionics_framework.llm")

# ---------------------------------------------------------------------------
# Global fallback state
# ---------------------------------------------------------------------------
_primary_failed = False       # True = primary (Groq) hard-failed, skip it
_hf_failed = False            # True = HF hard-failed, skip it too
_primary_cooldown_until: float = 0.0   # epoch: rate-limit cooldown for primary
_hf_cooldown_until: float = 0.0        # epoch: rate-limit cooldown for HF

_RATE_LIMIT_COOLDOWN = 60.0   # seconds before retrying primary after 429
_INTER_CALL_DELAY = 2.0       # seconds between calls to pace free-tier usage (Groq free = ~30 RPM)


def reset_provider_state() -> None:
    """
    Reset all per-task LLM provider failure/cooldown state.

    Call this at the START of every orchestrate() run so that each new task
    re-attempts the full fallback chain:
        Groq  →  HuggingFace  →  Local Ollama

    This ensures a 402/quota error or rate-limit from a previous task does NOT
    permanently poison the provider for the rest of the process lifetime.
    Within a single task, 402 billing errors still skip retries (fail-fast),
    but the slate is wiped clean at the next task boundary.
    """
    global _primary_failed, _hf_failed, _primary_cooldown_until, _hf_cooldown_until
    _primary_failed = False
    _hf_failed = False
    _primary_cooldown_until = 0.0
    _hf_cooldown_until = 0.0
    logger.info("LLM provider state reset: Groq → HuggingFace → Local chain will be re-attempted from scratch.")


# ---------------------------------------------------------------------------
# Provider: Groq
# ---------------------------------------------------------------------------
def query_groq(prompt: str, system_instruction: str = "", model_name: str = "") -> str:
    """Single-shot Groq query. Raises RuntimeError on 429 so caller can handle cooldown."""
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY not configured.")
    url = "https://api.groq.com/openai/v1/chat/completions"
    model = model_name if model_name else GROQ_MODEL
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "temperature": 0.1}
    logger.info(f"Querying Groq model: {model}")
    response = requests.post(url, json=payload, headers=headers, timeout=DEFAULT_LLM_TIMEOUT)  # type: ignore[arg-type]
    if response.status_code == 429:
        raise RuntimeError("Groq rate limit hit (429).")
    if response.status_code == 402:
        raise RuntimeError("Groq quota/billing error (402 Payment Required). Check your Groq plan.")
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def query_groq_with_retry(prompt: str, system_instruction: str = "", model_name: str = "") -> str:
    """Groq with up to 4 attempts. Retries transient connection/timeout/rate-limit issues."""
    max_attempts = 4
    last_exc: Exception = RuntimeError("No attempts made")
    for attempt in range(1, max_attempts + 1):
        try:
            return query_groq(prompt, system_instruction, model_name)
        except RuntimeError as e:
            err_str = str(e)
            is_billing = "402" in err_str or "payment required" in err_str.lower()
            last_exc = e
            logger.warning(f"Groq attempt {attempt}/{max_attempts} failed: {e}")
            if is_billing:
                # Quota/billing limit is permanent for this run
                raise last_exc
            if attempt < max_attempts:
                # Rate limit (429) — wait progressively longer
                sleep = (10 * attempt) if "429" in err_str else 3
                logger.info(f"Retrying Groq in {sleep}s...")
                time.sleep(sleep)
            else:
                raise last_exc
        except requests.exceptions.HTTPError as e:
            last_exc = e
            status_code = e.response.status_code if e.response is not None else 0
            logger.warning(f"Groq attempt {attempt}/{max_attempts} HTTP error {status_code}: {e}")
            if status_code in [401, 403, 404]:
                # Auth/Missing Resource are fatal, do not retry
                raise last_exc
            if attempt < max_attempts:
                time.sleep(2)
            else:
                raise last_exc
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_exc = e
            logger.warning(f"Groq attempt {attempt}/{max_attempts} network/timeout error: {e}")
            if attempt < max_attempts:
                time.sleep(2)
            else:
                raise last_exc
        except Exception as e:
            last_exc = e
            logger.warning(f"Groq attempt {attempt}/{max_attempts} unexpected error: {e}")
            if attempt < max_attempts:
                time.sleep(2)
            else:
                raise last_exc
    raise last_exc


# ---------------------------------------------------------------------------
# Provider: Hugging Face Inference API (OpenAI-compatible chat endpoint)
# ---------------------------------------------------------------------------
def query_huggingface(prompt: str, system_instruction: str = "", model_name: str = "") -> str:
    """
    Query Hugging Face Inference API using the free serverless endpoint.
    Uses the per-model OpenAI-compatible URL at api-inference.huggingface.co.
    Falls back to the router endpoint if the free endpoint is unavailable.
    """
    if not HF_API_TOKEN:
        raise ValueError("HF_API_TOKEN not configured.")
    model = model_name if model_name else HF_MODEL

    # HuggingFace router endpoint (OpenAI-compatible)
    chat_url = "https://router.huggingface.co/v1/chat/completions"
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})
    headers = {"Authorization": f"Bearer {HF_API_TOKEN}", "Content-Type": "application/json"}
    chat_payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 4096,
        "temperature": 0.1,
    }
    logger.info(f"Querying HuggingFace (chat) model: {model}")
    response = requests.post(chat_url, json=chat_payload, headers=headers, timeout=120)  # type: ignore[arg-type]
    if response.status_code == 429:
        raise RuntimeError("HuggingFace rate limit hit (429).")
    if response.status_code == 402:
        raise RuntimeError("HuggingFace quota/billing error (402 Payment Required). Free tier quota exhausted — check https://huggingface.co/settings/billing")
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict) and "choices" in data:
        return data["choices"][0]["message"]["content"].strip()
    raise RuntimeError(f"Unexpected HuggingFace response format: {data}")


def query_huggingface_with_retry(prompt: str, system_instruction: str = "", model_name: str = "") -> str:
    """HF with up to 3 attempts. Retries transient issues."""
    max_attempts = 3
    last_exc: Exception = RuntimeError("No attempts made")
    for attempt in range(1, max_attempts + 1):
        try:
            return query_huggingface(prompt, system_instruction, model_name)
        except RuntimeError as e:
            err_str = str(e)
            is_billing = "402" in err_str or "payment required" in err_str.lower()
            last_exc = e
            logger.warning(f"HuggingFace attempt {attempt}/{max_attempts} failed: {e}")
            if is_billing:
                raise last_exc
            if attempt < max_attempts:
                sleep = 4 if "429" in err_str else 2
                logger.info(f"Retrying HuggingFace in {sleep}s...")
                time.sleep(sleep)
            else:
                raise last_exc
        except requests.exceptions.HTTPError as e:
            last_exc = e
            status_code = e.response.status_code if e.response is not None else 0
            logger.warning(f"HuggingFace attempt {attempt}/{max_attempts} HTTP error {status_code}: {e}")
            if status_code in [401, 403, 404]:
                raise last_exc
            if attempt < max_attempts:
                time.sleep(2)
            else:
                raise last_exc
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_exc = e
            logger.warning(f"HuggingFace attempt {attempt}/{max_attempts} network/timeout error: {e}")
            if attempt < max_attempts:
                time.sleep(2)
            else:
                raise last_exc
        except Exception as e:
            last_exc = e
            logger.warning(f"HuggingFace attempt {attempt}/{max_attempts} unexpected error: {e}")
            if attempt < max_attempts:
                time.sleep(2)
            else:
                raise last_exc
    raise last_exc


# ---------------------------------------------------------------------------
# Provider: Gemini
# ---------------------------------------------------------------------------
def query_gemini(prompt: str, system_instruction: str = "", model_name: str = "") -> str:
    """Query Gemini API using the google-genai SDK."""
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not configured.")
    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError:
        raise RuntimeError("google-genai package not installed. Run: pip install google-genai")
    model = model_name if model_name else GEMINI_MODEL
    logger.info(f"Querying Gemini model: {model}")
    client = genai.Client(api_key=GEMINI_API_KEY)
    config = genai_types.GenerateContentConfig(
        temperature=0.1,
        system_instruction=system_instruction if system_instruction else None,
    )
    response = client.models.generate_content(model=model, contents=prompt, config=config)
    return (response.text or "").strip()


# ---------------------------------------------------------------------------
# Helper: Strip DeepSeek-R1 <think> reasoning blocks
# ---------------------------------------------------------------------------
def _strip_think_blocks(text: str) -> str:
    import re
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


# ---------------------------------------------------------------------------
# Provider: Local Ollama (Last Resort Fallback)
# ---------------------------------------------------------------------------
def query_local(prompt: str, system_instruction: str = "", model_name: str = "") -> str:
    """
    Query local Ollama model via OpenAI-compatible REST endpoint.
    Only triggered if all cloud services fail.
    """
    if not LOCAL_LLM_ENABLED:
        raise ValueError("Local LLM fallback is disabled.")
        
    model = model_name if model_name else LOCAL_LLM_MODEL
    url = f"{LOCAL_LLM_API_BASE}/v1/chat/completions"
    
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})
    
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "options": {
            "num_ctx": 4096,  # Cap context to prevent CPU swapping
        },
        "stream": False
    }
    
    logger.info(f"Querying local model: {model}")
    try:
        response = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=LOCAL_LLM_TIMEOUT
        )
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"].strip()
        return _strip_think_blocks(raw)
    except Exception as e:
        raise RuntimeError(f"Local Ollama query failed: {e}")


# ---------------------------------------------------------------------------
# Provider: Local vLLM Server
# ---------------------------------------------------------------------------
def query_vllm(prompt: str, system_instruction: str = "", model_name: str = "") -> str:
    """
    Query local vLLM API server via OpenAI-compatible REST endpoint.
    """
    url = f"{VLLM_API_BASE}/chat/completions"
    model = model_name if model_name else VLLM_MODEL
    
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})
    
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
    }
    
    logger.info(f"Querying local vLLM model: {model} at {VLLM_API_BASE}")
    try:
        response = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=DEFAULT_LLM_TIMEOUT
        )
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"].strip()
        return _strip_think_blocks(raw)
    except Exception as e:
        raise RuntimeError(f"Local vLLM query failed: {e}")


# ---------------------------------------------------------------------------
# Main routing function
# ---------------------------------------------------------------------------
def query_llm(prompt: str, system_instruction: str = "", model_name: str = "") -> str:
    """
    1. vLLM        — local high-performance server (if primary).
    2. Groq        — primary cloud alternative.
    3. HuggingFace — secondary fallback.
    4. Local/Ollama — tertiary fallback.
    5. Gemini      — optional last fallback.

    Rate-limit 429s → timed cooldown (not permanent switch).
    Hard errors (auth, network) → permanent skip for this session.
    """
    global _primary_failed, _hf_failed, _primary_cooldown_until, _hf_cooldown_until

    # Small delay to pace free-tier API usage
    time.sleep(_INTER_CALL_DELAY)

    now = time.time()
    primary = LLM_PROVIDER.lower()

    # Determine fallback routing hierarchy
    if primary == "vllm":
        priority_list = ["vllm", "local", "groq", "huggingface", "gemini"]
    elif primary in ("local", "ollama"):
        priority_list = ["local", "vllm", "groq", "huggingface", "gemini"]
    elif primary == "groq":
        priority_list = ["groq", "huggingface", "local", "vllm", "gemini"]
    elif primary == "huggingface":
        priority_list = ["huggingface", "groq", "local", "vllm", "gemini"]
    elif primary == "gemini":
        priority_list = ["gemini", "groq", "huggingface", "local", "vllm"]
    else:
        priority_list = ["groq", "huggingface", "local", "vllm", "gemini"]

    # Remove 'local' from the list if it is disabled, so it is never attempted
    if not LOCAL_LLM_ENABLED and "local" in priority_list:
        priority_list.remove("local")

    for provider in priority_list:
        if provider == "huggingface":
            if not HF_API_TOKEN or _hf_failed:
                continue
            if now < _hf_cooldown_until:
                remaining = _hf_cooldown_until - now
                logger.info(f"HuggingFace cooldown active ({remaining:.0f}s left). Trying next provider.")
                continue
            try:
                result = query_huggingface_with_retry(prompt, system_instruction, model_name)
                logger.info("HuggingFace query succeeded.")
                return result
            except RuntimeError as e:
                err_str = str(e)
                is_rate_limit = "429" in err_str or "rate limit" in err_str.lower()
                is_billing = "402" in err_str or "payment required" in err_str.lower()
                if is_billing:
                    logger.warning(
                        "HuggingFace billing/quota error (402): free tier exhausted. "
                        "Skipping HuggingFace for this session. Visit https://huggingface.co/settings/billing"
                    )
                    _hf_failed = True
                elif is_rate_limit:
                    logger.warning(
                        f"HuggingFace rate-limited. Cooling down for {_RATE_LIMIT_COOLDOWN}s."
                    )
                    _hf_cooldown_until = now + _RATE_LIMIT_COOLDOWN
                else:
                    logger.error(f"HuggingFace hard failure: {e}. Skipping for session.")
                    _hf_failed = True
            except Exception as e:
                logger.error(f"HuggingFace unexpected failure: {e}. Skipping for session.")
                _hf_failed = True

        elif provider == "groq":
            if not GROQ_API_KEY or _primary_failed:
                continue
            if now < _primary_cooldown_until:
                remaining = _primary_cooldown_until - now
                logger.info(f"Groq cooldown active ({remaining:.0f}s left). Trying next provider.")
                continue
            try:
                result = query_groq_with_retry(prompt, system_instruction, model_name)
                logger.info("Groq query succeeded.")
                return result
            except RuntimeError as e:
                err_str = str(e)
                is_rate_limit = "429" in err_str or "rate limit" in err_str.lower()
                is_billing = "402" in err_str or "payment required" in err_str.lower()
                if is_billing:
                    logger.warning("Groq billing error (402). Skipping Groq for session.")
                    _primary_failed = True
                elif is_rate_limit:
                    logger.warning(f"Groq rate-limited. Cooling down for {_RATE_LIMIT_COOLDOWN}s.")
                    _primary_cooldown_until = now + _RATE_LIMIT_COOLDOWN
                else:
                    logger.error(f"Groq hard failure: {e}. Skipping for session.")
                    _primary_failed = True
            except requests.exceptions.HTTPError as e:
                status_code = e.response.status_code if e.response is not None else 0
                if status_code in [401, 403, 404]:
                    logger.error(f"Groq fatal HTTP error {status_code}: {e}. Skipping for session.")
                    _primary_failed = True
                else:
                    logger.warning(f"Groq transient HTTP error {status_code}: {e}. Falling back to next provider.")
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                logger.warning(f"Groq transient network error: {e}. Falling back to next provider without disabling.")
            except Exception as e:
                logger.error(f"Groq unexpected failure: {e}. Skipping for session.")
                _primary_failed = True

        elif provider == "gemini":
            if not GEMINI_API_KEY:
                continue
            try:
                result = query_gemini(prompt, system_instruction, model_name)
                logger.info("Gemini query succeeded.")
                return result
            except Exception as e:
                logger.error(f"Gemini unexpected failure: {e}.")

        elif provider == "local":
            try:
                result = query_local(prompt, system_instruction, model_name)
                logger.info("Local Ollama fallback query succeeded.")
                return result
            except Exception as e:
                logger.error(f"Local Ollama unexpected failure: {e}")

        elif provider == "vllm":
            try:
                result = query_vllm(prompt, system_instruction, model_name)
                logger.info("Local vLLM fallback query succeeded.")
                return result
            except Exception as e:
                logger.error(f"Local vLLM unexpected failure: {e}")

    raise RuntimeError("All configured LLM providers (HuggingFace/Groq/Gemini/Local/vLLM) failed or rate-limited.")


# ---------------------------------------------------------------------------
# Async wrapper
# ---------------------------------------------------------------------------
async def async_query_llm(prompt: str, system_instruction: str = "", model_name: str = "", call_timeout: float = 90.0) -> str:
    """Asynchronously query the LLM (runs in a thread pool with a hard timeout ceiling)."""
    # Use generous local timeout if local LLM fallback is enabled
    timeout = max(call_timeout, LOCAL_LLM_TIMEOUT) if LOCAL_LLM_ENABLED else call_timeout
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(query_llm, prompt, system_instruction, model_name),
            timeout=timeout
        )
    except asyncio.TimeoutError:
        raise RuntimeError(f"LLM call exceeded {timeout}s hard timeout across all providers.")


# Startup status log
_provider = LLM_PROVIDER.lower()
if _provider == "groq":
    if GROQ_API_KEY:
        logger.info("Groq API configured and available (primary provider).")
    else:
        logger.warning("Groq API key not set. Check GROQ_API_KEY in .env.")
elif _provider == "vllm":
    logger.info(f"Local vLLM configured and available (primary provider). Model: {VLLM_MODEL} at {VLLM_API_BASE}")

if HF_API_TOKEN:
    logger.info(f"HuggingFace API configured (fallback). Model: {HF_MODEL}")
else:
    logger.warning("HF_API_TOKEN not set — HuggingFace fallback unavailable.")

if LOCAL_LLM_ENABLED:
    logger.info(f"Local Ollama configured (fallback). Model: {LOCAL_LLM_MODEL} at {LOCAL_LLM_API_BASE}")
else:
    logger.info("Local Ollama fallback disabled (LOCAL_LLM_ENABLED=False).")
