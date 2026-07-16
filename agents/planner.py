"""
agents/planner.py — MATLAB Plan Generator (AutoGen AssistantAgent)

Uses an AG2 AssistantAgent backed by local Ollama to generate a structured
JSON plan from the user task description. Runs a single 1-turn chat per call
to keep RAM usage minimal on 8GB systems.
"""
import json
import logging
import re
from typing import Any

from autogen import AssistantAgent, UserProxyAgent

from config import LLM_CONFIG, PLANNER_SYSTEM_PROMPT

logger = logging.getLogger("matlab_agent.planner")

# Minimal required keys in the plan
REQUIRED_KEYS = {"file_name", "description", "inputs", "outputs", "components", "test_call"}

# Suppress AutoGen internal verbose logging — we use Rich for UI
logging.getLogger("autogen").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("openai").setLevel(logging.ERROR)


def _quiet_chat(proxy, agent, message: str):
    """
    Run a 1-turn AutoGen chat while suppressing AutoGen's own stdout output
    (e.g. 'TERMINATING RUN' and cost warnings that bypass Python logging).
    """
    import io, sys
    _buf = sys.stdout
    sys.stdout = io.StringIO()
    try:
        result = proxy.initiate_chat(agent, message=message, max_turns=1, silent=True)
    finally:
        sys.stdout = _buf
    return result


def generate_plan(task_description: str) -> dict[str, Any]:
    """
    Call the Ollama LLM via an AutoGen AssistantAgent to create a MATLAB
    function plan from the user's task description.

    Returns:
        dict — structured plan matching PLANNER_SYSTEM_PROMPT schema.

    Raises:
        ValueError — if the LLM response cannot be parsed into a valid plan.
        RuntimeError — if the LLM call fails.
    """
    logger.info("Generating plan for: %s", task_description[:100])

    # Create a fresh planner agent for each call (no state leakage between runs)
    planner = AssistantAgent(
        name="PlannerAgent",
        system_message=PLANNER_SYSTEM_PROMPT,
        llm_config=LLM_CONFIG,
        max_consecutive_auto_reply=1,
        human_input_mode="NEVER",
        code_execution_config=False,  # Never execute code
    )

    # UserProxyAgent acts as the "user" that triggers the planner
    proxy = UserProxyAgent(
        name="PlannerProxy",
        human_input_mode="NEVER",
        max_consecutive_auto_reply=0,   # Send once, don't loop
        llm_config=False,
        code_execution_config=False,
        is_termination_msg=lambda _: True,  # Stop after first reply
    )

    user_prompt = (
        f"Generate a MATLAB function plan for the following task:\n\n"
        f"{task_description}\n\n"
        f"Return ONLY the JSON object. No explanation, no markdown."
    )

    try:
        result = _quiet_chat(proxy, planner, user_prompt)
        raw = _extract_last_reply(result)
    except Exception as exc:
        msg = str(exc).lower()
        if "timeout" in msg or "timed out" in msg:
            raise TimeoutError(f"Planner LLM call timed out: {exc}")
        raise RuntimeError(f"Planner LLM call failed: {exc}") from exc

    plan = _parse_json(raw)
    _validate_plan(plan)

    # Normalize file_name: strip .m extension if LLM added it
    plan["file_name"] = plan["file_name"].replace(".m", "").strip()

    logger.info("Plan generated: file_name=%s", plan["file_name"])
    return plan


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_last_reply(result) -> str:
    """Extract the last assistant message content from an AutoGen chat result."""
    history = getattr(result, "chat_history", None)
    if not history:
        raise RuntimeError("PlannerAgent returned no response.")

    # Walk history in reverse to find the last non-empty assistant reply
    for msg in reversed(history):
        content = msg.get("content", "")
        if content and msg.get("role") in ("assistant", "user"):
            # Skip the proxy's own messages (role='user', name='PlannerProxy')
            if msg.get("name", "") == "PlannerProxy":
                continue
            return content

    raise RuntimeError("PlannerAgent chat history contained no usable reply.")


def _parse_json(text: str) -> dict[str, Any]:
    """
    Extract and parse JSON from raw LLM output.
    Handles markdown fences and minor JSON formatting errors.
    """
    # Strip markdown fences
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    text = text.strip()

    # Try to isolate the JSON object
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]

    # First attempt: standard json.loads
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Second attempt: json-repair (handles minor LLM formatting issues)
    try:
        from json_repair import repair_json  # type: ignore
        repaired = repair_json(text)
        return json.loads(repaired)
    except Exception:
        pass

    raise ValueError(
        f"Could not parse LLM output as JSON.\n"
        f"Raw output (first 500 chars):\n{text[:500]}"
    )


def _validate_plan(plan: dict[str, Any]) -> None:
    """Raise ValueError if any required key is missing or malformed."""
    missing = REQUIRED_KEYS - set(plan.keys())
    if missing:
        raise ValueError(f"Plan is missing required keys: {missing}\nPlan: {plan}")

    if not isinstance(plan.get("file_name"), str) or not plan["file_name"]:
        raise ValueError("Plan 'file_name' must be a non-empty string.")

    if not isinstance(plan.get("components"), list) or not plan["components"]:
        raise ValueError("Plan 'components' must be a non-empty list.")

    if not isinstance(plan.get("test_call"), str) or not plan["test_call"]:
        raise ValueError("Plan 'test_call' must be a non-empty MATLAB expression string.")
