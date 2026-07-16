"""
agents/llm_client.py — Ollama health check utility.

The LLM call logic has been moved to AutoGen (AG2) AssistantAgents.
This module retains only the health-check so main.py can verify
Ollama is reachable before starting the pipeline.
"""
import logging
import requests

from config import OLLAMA_BASE_URL, OLLAMA_MODEL

logger = logging.getLogger("matlab_agent.llm")

# One persistent session for connection reuse
_session = requests.Session()
_session.headers.update({"Content-Type": "application/json"})


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
