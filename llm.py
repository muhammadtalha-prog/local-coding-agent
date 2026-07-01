import logging
import asyncio
import threading
import concurrent.futures
import time
import os
import re
import litellm
from litellm import completion

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

# Disable LiteLLM telemetry and drop unsupported parameters
litellm.telemetry = False
litellm.drop_params = True
litellm.use_local_model_cost_map = True

# Cooldown and failure states to mimic reset_provider_state behavior
_provider_failed = {}          # provider -> bool (hard failed)
_provider_cooldown_until = {}  # provider -> float (epoch time)
_RATE_LIMIT_COOLDOWN = 60.0
_INTER_CALL_DELAY = 2.0


def reset_provider_state() -> None:
    """
    Reset all per-task LLM provider failure/cooldown state.

    Call this at the START of every orchestrate() run so that each new task
    re-attempts the full fallback chain.
    """
    global _provider_failed, _provider_cooldown_until
    _provider_failed.clear()
    _provider_cooldown_until.clear()
    logger.info("LLM provider state reset: LiteLLM fallback chain will be re-attempted from scratch.")


def _strip_think_blocks(text: str) -> str:
    """Strip DeepSeek-R1 style <think> blocks from response content."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _is_permanent_error(error_msg: str, error_obj: Exception) -> bool:
    """
    Check if the error is permanent (e.g. authentication, permission, invalid model)
    indicating that retrying this provider will never succeed.
    """
    error_msg = error_msg.lower()
    
    # Check common permanent error strings
    permanent_keywords = [
        "403", "401", "permission", "authorization", "authenticate", 
        "api key is not configured", "invalid_api_key", "invalid api key", 
        "unauthorized", "credentials", "bad token", "invalid token",
        "sufficient permissions"
    ]
    
    for kw in permanent_keywords:
        if kw in error_msg:
            return True
            
    # Also inspect litellm/openai specific exception classes if available
    err_class = type(error_obj).__name__
    if "AuthenticationError" in err_class or "PermissionDeniedError" in err_class:
        return True
        
    return False


def _parse_cooldown_seconds(error_msg: str) -> float:
    """
    Parse cooldown duration from error message.
    Looks for patterns like:
      - "try again in 5m37.5s"
      - "try again in 20s"
      - "try again in 1h2m3s"
      - "please wait X seconds"
      - "rate limit exceeded... reset in Ys"
    """
    error_msg = error_msg.lower()
    
    # 1. Match patterns like "try again in 5m37.5s", "try again in 20m8.7s" or "try again in 20s"
    # Match optionally hours, minutes, seconds
    match = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", error_msg)
    if match:
        hours = float(match.group(1)) if match.group(1) else 0.0
        minutes = float(match.group(2)) if match.group(2) else 0.0
        seconds = float(match.group(3)) if match.group(3) else 0.0
        total_sec = hours * 3600.0 + minutes * 60.0 + seconds
        if total_sec > 0:
            return total_sec
            
    # 2. Match patterns like "please wait X seconds" or "please wait Xs"
    match = re.search(r"please wait ([\d.]+)\s*s(?:econd)?s?", error_msg)
    if match:
        return float(match.group(1))
        
    # 3. Match patterns like "reset in Xs" or "reset in X minutes" or "reset in X hours"
    match = re.search(r"reset in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", error_msg)
    if match:
        hours = float(match.group(1)) if match.group(1) else 0.0
        minutes = float(match.group(2)) if match.group(2) else 0.0
        seconds = float(match.group(3)) if match.group(3) else 0.0
        total_sec = hours * 3600.0 + minutes * 60.0 + seconds
        if total_sec > 0:
            return total_sec
            
    return 60.0 # Default fallback


def query_llm(prompt: str, system_instruction: str = "", model_name: str = "") -> str:
    """
    Query the LLM using LiteLLM's unified completion API with fallback lists.
    Resolves the fallback sequence and connection parameters dynamically.
    """
    global _provider_failed, _provider_cooldown_until

    # Small delay to pace free-tier API usage
    time.sleep(_INTER_CALL_DELAY)

    now = time.time()

    # 1. Parse model_name override if provided (e.g. groq/llama-3.3-70b-versatile or ollama/qwen2.5-coder:3b)
    model_override = ""
    primary_provider = None
    if model_name:
        if "/" in model_name:
            primary_provider, model_override = model_name.split("/", 1)
            primary_provider = primary_provider.lower()
            if primary_provider in ("local", "ollama"):
                primary_provider = "local"
            elif primary_provider in ("huggingface", "hf"):
                primary_provider = "huggingface"
        else:
            model_override = model_name

    if not primary_provider:
        primary_provider = LLM_PROVIDER.lower()
        if primary_provider in ("local", "ollama"):
            primary_provider = "local"
        elif primary_provider in ("huggingface", "hf"):
            primary_provider = "huggingface"

    # 2. Determine priorities list of providers
    if primary_provider == "vllm":
        priority_list = ["vllm", "local", "groq", "huggingface", "gemini"]
    elif primary_provider == "local":
        priority_list = ["local", "vllm", "groq", "huggingface", "gemini"]
    elif primary_provider == "groq":
        priority_list = ["groq", "huggingface", "local", "vllm", "gemini"]
    elif primary_provider == "huggingface":
        priority_list = ["huggingface", "groq", "local", "vllm", "gemini"]
    elif primary_provider == "gemini":
        priority_list = ["gemini", "groq", "huggingface", "local", "vllm"]
    else:
        priority_list = ["groq", "huggingface", "local", "vllm", "gemini"]

    # Remove 'local' if disabled
    if not LOCAL_LLM_ENABLED and "local" in priority_list:
        priority_list.remove("local")

    # Helper function to build model params for LiteLLM
    def get_provider_config(prov: str, name_override: str = "") -> dict | None:
        if prov == "groq":
            if not GROQ_API_KEY:
                return None
            return {
                "model": f"groq/{name_override if name_override else GROQ_MODEL}",
                "api_key": GROQ_API_KEY,
            }
        elif prov == "huggingface":
            if not HF_API_TOKEN:
                return None
            # LiteLLM router endpoint (OpenAI-compatible)
            return {
                "model": f"openai/{name_override if name_override else HF_MODEL}",
                "api_base": "https://router.huggingface.co/v1",
                "api_key": HF_API_TOKEN,
            }
        elif prov == "gemini":
            if not GEMINI_API_KEY:
                return None
            return {
                "model": f"gemini/{name_override if name_override else GEMINI_MODEL}",
                "api_key": GEMINI_API_KEY,
            }
        elif prov == "local":
            if not LOCAL_LLM_ENABLED:
                return None
            return {
                "model": f"ollama_chat/{name_override if name_override else LOCAL_LLM_MODEL}",
                "api_base": LOCAL_LLM_API_BASE,
            }
        elif prov == "vllm":
            return {
                "model": f"openai/{name_override if name_override else VLLM_MODEL}",
                "api_base": VLLM_API_BASE,
                "api_key": "none",
            }
        return None

    # 3. Filter available configurations based on health/cooldown
    available_configs = []
    for prov in priority_list:
        if _provider_failed.get(prov, False):
            continue
        if now < _provider_cooldown_until.get(prov, 0.0):
            continue
        # Use name override if this provider is the primary provider requested
        name_override = model_override if prov == primary_provider else ""
        cfg = get_provider_config(prov, name_override)
        if cfg:
            cfg["_provider"] = prov
            available_configs.append(cfg)

    # Fallback: if all configured providers are temporarily cooling down/failed, try them anyway
    if not available_configs:
        logger.warning("All LLM providers are on cooldown or marked as failed. Resetting filters for a retry.")
        for prov in priority_list:
            name_override = model_override if prov == primary_provider else ""
            cfg = get_provider_config(prov, name_override)
            if cfg:
                cfg["_provider"] = prov
                available_configs.append(cfg)

    if not available_configs:
        raise RuntimeError("No configured LLM providers are available.")

    # Format OpenAI-style messages list
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    logger.info(
        f"Querying LLM with provider chain: "
        f"{[c['model'] for c in available_configs]}"
    )

    # 3. Sequential fallback: try each provider in order
    last_error = None
    for idx, cfg in enumerate(available_configs):
        prov = cfg["_provider"]
        model = cfg["model"]
        api_key = cfg.get("api_key")
        api_base = cfg.get("api_base")

        try:
            logger.info(f"Attempting provider [{idx+1}/{len(available_configs)}]: {prov} ({model})")
            provider_timeout = LOCAL_LLM_TIMEOUT if prov == "local" else DEFAULT_LLM_TIMEOUT
            kwargs = {
                "model": model,
                "messages": messages,
                "temperature": 0.1,
                "timeout": provider_timeout,
            }
            if prov == "local":
                kwargs["max_tokens"] = 2048
            if api_key:
                kwargs["api_key"] = api_key
            if api_base:
                kwargs["api_base"] = api_base

            response = completion(**kwargs)

            raw_text = response.choices[0].message.content or ""
            logger.info(f"LLM query succeeded using provider: {prov}")



            return _strip_think_blocks(raw_text)

        except Exception as e:
            last_error = e
            error_msg = str(e)
            logger.warning(f"Provider {prov} ({model}) failed: {e}")
            
            # Check if this is a permanent authorization / key / permission error
            if _is_permanent_error(error_msg, e):
                _provider_failed[prov] = True
                logger.error(f"Permanent authentication/permission error detected for provider {prov}. Skipping this provider for the rest of this run.")
            else:
                # Calculate dynamic cooldown based on the rate limit message if possible
                cooldown_sec = _parse_cooldown_seconds(error_msg)
                _provider_cooldown_until[prov] = time.time() + cooldown_sec
                logger.warning(f"Temporary failure for provider {prov}. Put on cooldown for {cooldown_sec:.1f}s.")
            
            # Add a small delay before trying the next provider
            time.sleep(1.0)
            continue

    # All providers exhausted
    logger.error(f"All configured LLM providers failed. Last error: {last_error}")
    if available_configs:
        _provider_failed[available_configs[0]["_provider"]] = True
    raise RuntimeError(f"All LLM providers failed. Last error: {last_error}")


def _run_in_daemon_thread(fn, *args) -> "concurrent.futures.Future":
    """
    Run a blocking function on a daemon thread, bridged to a concurrent.futures.Future.

    Why this exists: asyncio.to_thread() uses a standard (non-daemon)
    ThreadPoolExecutor under the hood. If the awaiting asyncio.wait_for()
    times out, the coroutine gives up waiting — but the underlying thread
    keeps running the blocking call to completion regardless (Python cannot
    forcibly kill a thread). Because that thread is non-daemon, the whole
    process will not fully exit until it finishes, which for a slow local
    model can mean the CLI (and your CPU/RAM usage) silently keeps running
    for tens of minutes after already reporting failure. Using a daemon
    thread means that if we give up on it, the process can still exit
    immediately and the OS reclaims the resources right away.
    """
    future: concurrent.futures.Future = concurrent.futures.Future()

    def _wrapper():
        try:
            result = fn(*args)
            if not future.cancelled():
                future.set_result(result)
        except BaseException as e:  # noqa: BLE001
            if not future.cancelled():
                future.set_exception(e)

    t = threading.Thread(target=_wrapper, daemon=True, name="llm-call-daemon")
    t.start()
    return future


async def async_query_llm(prompt: str, system_instruction: str = "", model_name: str = "", call_timeout: float = 90.0) -> str:
    """Asynchronously query the LLM (runs on a daemon thread).

    Timeout responsibility is delegated to the TimerAgent and global pipeline
    timeouts in cli.py. If the pipeline times out globally, this daemon thread
    will be abandoned and will not block the CLI process from exiting.
    """
    future = _run_in_daemon_thread(query_llm, prompt, system_instruction, model_name)
    return await asyncio.wrap_future(future)
