"""
agents/llm_client.py — Thin, direct OpenAI-compatible client for Ollama.

Replaces the 349-line llm.py + LiteLLM stack with a minimal requests-based
call. No fallback cascade — we target one local model. Fast and simple.

For 8GB RAM: keeps a single persistent session to avoid SSL handshake overhead.
"""
import logging
import time
import json
import requests
from typing import Iterator

from config import (
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    LLM_MAX_TOKENS,
    LLM_TEMPERATURE,
    LLM_TIMEOUT_SEC,
)

logger = logging.getLogger("matlab_agent.llm")

# One persistent session for connection reuse (lower latency per call)
_session = requests.Session()
_session.headers.update({"Content-Type": "application/json"})


def call_llm(
    user_prompt: str,
    system_prompt: str = "",
    model: str = OLLAMA_MODEL,
    max_tokens: int = LLM_MAX_TOKENS,
    temperature: float = LLM_TEMPERATURE,
    timeout: float = LLM_TIMEOUT_SEC,
) -> str:
    """
    Send a prompt to the local Ollama model via OpenAI-compatible /chat/completions.
    Returns the raw text response.

    Raises:
        RuntimeError — if the server is unreachable or returns an error.
        TimeoutError — if the call exceeds `timeout` seconds.
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }

    url = f"{OLLAMA_BASE_URL}/chat/completions"
    start = time.monotonic()

    try:
        resp = _session.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"Cannot connect to Ollama at {OLLAMA_BASE_URL}.\n"
            "Make sure Ollama is running: `ollama serve`\n"
            f"And the model is pulled: `ollama pull {model}`"
        )
    except requests.exceptions.Timeout:
        elapsed = time.monotonic() - start
        raise TimeoutError(
            f"LLM call timed out after {elapsed:.0f}s "
            f"(limit: {timeout}s). "
            "Consider a smaller model or increasing LLM_TIMEOUT_SEC in .env."
        )
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(f"Ollama API error: {e}\nResponse: {resp.text[:500]}")

    data = resp.json()
    elapsed = time.monotonic() - start

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected Ollama response format: {data}") from e

    # Strip DeepSeek-style <think>...</think> blocks if present
    import re
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

    logger.info("LLM call completed in %.1fs (%d chars)", elapsed, len(content))
    return content


def check_ollama_health(model: str = OLLAMA_MODEL) -> tuple[bool, str]:
    """
    Verify Ollama is reachable and the target model is available.
    Returns (ok: bool, message: str).
    """
    try:
        # Use the Ollama native /api/tags endpoint (always available)
        tags_url = OLLAMA_BASE_URL.replace("/v1", "") + "/api/tags"
        resp = _session.get(tags_url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        available = [m["name"] for m in data.get("models", [])]

        # Normalize: strip tag suffix for comparison (qwen2.5-coder:3b → qwen2.5-coder:3b)
        model_base = model.split(":")[0].lower()
        found = any(model_base in m.lower() for m in available)

        if not found:
            return False, (
                f"Model '{model}' not found in Ollama. Available: {available}\n"
                f"Pull it with: ollama pull {model}"
            )
        return True, f"Ollama OK — model '{model}' is available."

    except requests.exceptions.ConnectionError:
        return False, (
            f"Ollama is not running at {OLLAMA_BASE_URL}.\n"
            "Start it with: ollama serve"
        )
    except Exception as e:
        return False, f"Ollama health check failed: {e}"
