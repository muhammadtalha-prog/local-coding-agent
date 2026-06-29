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
)

logger = logging.getLogger("avionics_framework.llm")

# ---------------------------------------------------------------------------
# Global fallback state
# ---------------------------------------------------------------------------
_primary_failed = False       # True = primary (Groq/Gemini) hard-failed, skip it
_hf_failed = False            # True = HF hard-failed, skip it too
_primary_cooldown_until: float = 0.0   # epoch: rate-limit cooldown for primary
_hf_cooldown_until: float = 0.0        # epoch: rate-limit cooldown for HF

_RATE_LIMIT_COOLDOWN = 45.0   # seconds before retrying primary after 429
_INTER_CALL_DELAY = 1.5       # seconds between calls to pace free-tier usage


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
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def query_groq_with_retry(prompt: str, system_instruction: str = "", model_name: str = "") -> str:
    """Groq with up to 3 retries. Rate-limit retries use longer backoff (6, 12, 24s)."""
    max_attempts = 3
    last_exc: Exception = RuntimeError("No attempts made")
    for attempt in range(1, max_attempts + 1):
        try:
            return query_groq(prompt, system_instruction, model_name)
        except RuntimeError as e:
            is_rate_limit = "429" in str(e) or "rate limit" in str(e).lower()
            last_exc = e
            logger.warning(f"Groq attempt {attempt}/{max_attempts} failed: {e}")
            if attempt < max_attempts:
                sleep = (6 * (2 ** (attempt - 1))) if is_rate_limit else (2 ** attempt)
                logger.info(f"Retrying Groq in {sleep}s...")
                time.sleep(sleep)
            else:
                raise last_exc
        except Exception as e:
            last_exc = e
            logger.warning(f"Groq attempt {attempt}/{max_attempts} unexpected error: {e}")
            if attempt >= max_attempts:
                raise
            time.sleep(2 ** attempt)
    raise last_exc


# ---------------------------------------------------------------------------
# Provider: Hugging Face Inference API (OpenAI-compatible chat endpoint)
# ---------------------------------------------------------------------------
def query_huggingface(prompt: str, system_instruction: str = "", model_name: str = "") -> str:
    """
    Query Hugging Face Inference API using the unified OpenAI-compatible
    https://router.huggingface.co/v1/chat/completions endpoint.
    """
    if not HF_API_TOKEN:
        raise ValueError("HF_API_TOKEN not configured.")
    model = model_name if model_name else HF_MODEL

    chat_url = "https://router.huggingface.co/v1/chat/completions"
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})
    headers = {"Authorization": f"Bearer {HF_API_TOKEN}", "Content-Type": "application/json"}
    chat_payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 2048,
        "temperature": 0.1,
    }
    logger.info(f"Querying HuggingFace (chat) model: {model}")
    response = requests.post(chat_url, json=chat_payload, headers=headers, timeout=120)  # type: ignore[arg-type]
    if response.status_code == 429:
        raise RuntimeError("HuggingFace rate limit hit (429).")
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict) and "choices" in data:
        return data["choices"][0]["message"]["content"].strip()
    raise RuntimeError(f"Unexpected HuggingFace response format: {data}")


def query_huggingface_with_retry(prompt: str, system_instruction: str = "", model_name: str = "") -> str:
    """HF with up to 3 retries."""
    max_attempts = 3
    last_exc: Exception = RuntimeError("No attempts made")
    for attempt in range(1, max_attempts + 1):
        try:
            return query_huggingface(prompt, system_instruction, model_name)
        except RuntimeError as e:
            is_rate_limit = "429" in str(e) or "rate limit" in str(e).lower()
            last_exc = e
            logger.warning(f"HuggingFace attempt {attempt}/{max_attempts} failed: {e}")
            if attempt < max_attempts:
                sleep = (10 * attempt) if is_rate_limit else (2 ** attempt)
                logger.info(f"Retrying HuggingFace in {sleep}s...")
                time.sleep(sleep)
            else:
                raise last_exc
        except Exception as e:
            last_exc = e
            logger.warning(f"HuggingFace attempt {attempt}/{max_attempts} error: {e}")
            if attempt >= max_attempts:
                raise
            time.sleep(2 ** attempt)
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
# Main routing function
# ---------------------------------------------------------------------------
def query_llm(prompt: str, system_instruction: str = "", model_name: str = "") -> str:
    """
    1. HuggingFace — primary/first attempt.
    2. Groq        — secondary fallback.
    3. Gemini      — tertiary fallback.

    Rate-limit 429s → timed cooldown (not permanent switch).
    Hard errors (auth, network) → permanent skip for this session.
    """
    global _primary_failed, _hf_failed, _primary_cooldown_until, _hf_cooldown_until

    # Small delay to pace free-tier API usage
    time.sleep(_INTER_CALL_DELAY)

    now = time.time()
    primary = LLM_PROVIDER.lower()

    # Determine fallback routing hierarchy
    if primary == "huggingface":
        priority_list = ["huggingface", "groq", "gemini"]
    elif primary == "groq":
        priority_list = ["groq", "huggingface", "gemini"]
    elif primary == "gemini":
        priority_list = ["gemini", "huggingface", "groq"]
    else:
        priority_list = ["huggingface", "groq", "gemini"]

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
                is_rate_limit = "429" in str(e) or "rate limit" in str(e).lower()
                if is_rate_limit:
                    logger.warning(
                        f"HuggingFace rate-limited. Cooling down for {_RATE_LIMIT_COOLDOWN}s. Fallback to Groq."
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
                is_rate_limit = "429" in str(e) or "rate limit" in str(e).lower()
                if is_rate_limit:
                    logger.warning(
                        f"Groq rate-limited. Cooling down for {_RATE_LIMIT_COOLDOWN}s."
                    )
                    _primary_cooldown_until = now + _RATE_LIMIT_COOLDOWN
                else:
                    logger.error(f"Groq hard failure: {e}. Skipping for session.")
                    _primary_failed = True
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

    raise RuntimeError("All configured cloud LLM providers (HuggingFace/Groq/Gemini) failed or rate-limited.")


# ---------------------------------------------------------------------------
# Async wrapper
# ---------------------------------------------------------------------------
async def async_query_llm(prompt: str, system_instruction: str = "", model_name: str = "") -> str:
    """Asynchronously query the LLM (runs in a thread pool to not block the event loop)."""
    return await asyncio.to_thread(query_llm, prompt, system_instruction, model_name)


# Startup status log
_provider = LLM_PROVIDER.lower()
if _provider == "groq":
    if GROQ_API_KEY:
        logger.info("Groq API configured and available (primary provider).")
    else:
        logger.warning("Groq API key not set. Check GROQ_API_KEY in .env.")

if HF_API_TOKEN:
    logger.info(f"HuggingFace API configured (fallback provider). Model: {HF_MODEL}")
else:
    logger.warning("HF_API_TOKEN not set — HuggingFace fallback unavailable.")
