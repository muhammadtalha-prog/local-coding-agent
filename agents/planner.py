"""
agents/planner.py — MATLAB Plan Generator

Takes a user task description and produces a structured JSON plan
describing the MATLAB function to generate.

Optimized for small models:
  - Short, unambiguous system prompt
  - JSON-only output enforced
  - json-repair used for minor LLM formatting mistakes
"""
import json
import logging
import re
from typing import Any

from config import PLANNER_SYSTEM_PROMPT
from agents.llm_client import call_llm

logger = logging.getLogger("matlab_agent.planner")

# Minimal required keys in the plan
REQUIRED_KEYS = {"file_name", "description", "inputs", "outputs", "components", "test_call"}


def generate_plan(task_description: str) -> dict[str, Any]:
    """
    Call the LLM to create a MATLAB function plan from the user's task description.

    Returns:
        dict — structured plan matching PLANNER_SYSTEM_PROMPT schema.

    Raises:
        ValueError — if the LLM response cannot be parsed into a valid plan.
        RuntimeError — propagated from LLM call failures.
    """
    logger.info("Generating plan for: %s", task_description[:100])

    user_prompt = (
        f"Generate a MATLAB function plan for the following task:\n\n"
        f"{task_description}\n\n"
        f"Return ONLY the JSON object. No explanation, no markdown."
    )

    raw = call_llm(user_prompt, system_prompt=PLANNER_SYSTEM_PROMPT)
    plan = _parse_json(raw)
    _validate_plan(plan)

    # Normalize file_name: strip .m extension if LLM added it
    plan["file_name"] = plan["file_name"].replace(".m", "").strip()

    logger.info("Plan generated: file_name=%s", plan["file_name"])
    return plan


def _parse_json(text: str) -> dict[str, Any]:
    """
    Extract and parse JSON from raw LLM output.
    Handles:
      - Markdown code fences (```json ... ```)
      - Leading/trailing whitespace
      - Minor JSON formatting errors (via json-repair if available)
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
