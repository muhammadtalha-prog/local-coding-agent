"""
agents/coder.py — MATLAB Code Generator (AutoGen AssistantAgent)

Uses an AG2 AssistantAgent backed by local Ollama to generate a complete,
executable MATLAB .m function file from a validated plan dict.
Writes the result directly to the sandbox directory.

Key constraints for small (1.5–3B) models:
  - Prompt includes ONLY what the model needs: plan summary + rules
  - Single 1-turn chat per call to keep RAM usage low
  - Strict output: raw MATLAB code only
"""
import logging
import re
from pathlib import Path
from typing import Any

from autogen import AssistantAgent, UserProxyAgent

from config import CODER_SYSTEM_PROMPT, LLM_CONFIG, SANDBOX_DIR

logger = logging.getLogger("matlab_agent.coder")

# Suppress AutoGen internal verbose logging — we use Rich for UI
logging.getLogger("autogen").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("openai").setLevel(logging.ERROR)


def _quiet_chat(proxy, agent, message: str):
    """1-turn chat with AutoGen stdout suppressed (no TERMINATING RUN noise)."""
    import io, sys
    _buf = sys.stdout
    sys.stdout = io.StringIO()
    try:
        result = proxy.initiate_chat(agent, message=message, max_turns=1, silent=True)
    finally:
        sys.stdout = _buf
    return result


def generate_code(plan: dict[str, Any]) -> tuple[Path, str]:
    """
    Generate MATLAB source code from a plan and write it to sandbox/.

    Returns:
        (matlab_file_path, source_code_str)

    Raises:
        ValueError — if generated code looks like Python (syntax contamination)
        RuntimeError — propagated from LLM failures
    """
    file_name = plan["file_name"]
    logger.info("Generating MATLAB code for: %s.m", file_name)

    user_prompt = _build_prompt(plan)

    # Create a fresh coder agent per call
    coder = AssistantAgent(
        name="CoderAgent",
        system_message=CODER_SYSTEM_PROMPT,
        llm_config=LLM_CONFIG,
        max_consecutive_auto_reply=1,
        human_input_mode="NEVER",
        code_execution_config=False,
    )

    proxy = UserProxyAgent(
        name="CoderProxy",
        human_input_mode="NEVER",
        max_consecutive_auto_reply=0,
        llm_config=False,
        code_execution_config=False,
        is_termination_msg=lambda _: True,
    )

    try:
        result = _quiet_chat(proxy, coder, user_prompt)
        raw = _extract_last_reply(result)
    except Exception as exc:
        msg = str(exc).lower()
        if "timeout" in msg or "timed out" in msg:
            raise TimeoutError(f"Coder LLM call timed out: {exc}")
        raise RuntimeError(f"Coder LLM call failed: {exc}") from exc

    code = _extract_code(raw)

    # Sanity check: reject Python code masquerading as MATLAB
    _validate_matlab_syntax(code, file_name)

    # Auto-correct plan's test_call to match actual generated signature
    _fix_test_call(plan, code)

    # Write to sandbox
    out_path = SANDBOX_DIR / f"{file_name}.m"
    out_path.write_text(code, encoding="utf-8")
    logger.info("Wrote MATLAB code to: %s", out_path)

    return out_path, code


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_last_reply(result) -> str:
    """Extract the last assistant message content from an AutoGen chat result."""
    history = getattr(result, "chat_history", None)
    if not history:
        raise RuntimeError("CoderAgent returned no response.")

    for msg in reversed(history):
        content = msg.get("content", "")
        if content and msg.get("name", "") not in ("CoderProxy",):
            return content

    raise RuntimeError("CoderAgent chat history contained no usable reply.")


def _build_prompt(plan: dict[str, Any]) -> str:
    """
    Build a compact code generation prompt from the plan.
    Kept short to preserve context budget for small models.
    """
    inputs_str = ", ".join(
        f"{i['name']} ({i['type']}): {i['description']}"
        for i in plan.get("inputs", [])
    )
    outputs_str = ", ".join(
        f"{o['name']} ({o['type']}): {o['description']}"
        for o in plan.get("outputs", [])
    )
    components_str = "\n".join(
        f"- {c['name']}: {c['description']} — Logic: {c.get('logic', 'see description')}"
        for c in plan.get("components", [])
    )

    return (
        f"Generate a MATLAB function file named '{plan['file_name']}.m'.\n\n"
        f"Description: {plan['description']}\n"
        f"Inputs: {inputs_str or 'none'}\n"
        f"Outputs: {outputs_str or 'none'}\n"
        f"Components:\n{components_str}\n\n"
        f"Output ONLY the raw MATLAB code. First line must be:\n"
        f"function {plan['outputs'][0]['name'] if plan.get('outputs') else 'result'} "
        f"= {plan['file_name']}({', '.join(i['name'] for i in plan.get('inputs', []))})"
    )


def _extract_code(raw: str) -> str:
    """Extract raw MATLAB code from LLM response — strips markdown fences."""
    raw = re.sub(r"```matlab\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"```m\s*",      "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"```\s*",       "", raw)
    return raw.strip()


def _validate_matlab_syntax(code: str, file_name: str) -> None:
    """
    Detect if the LLM accidentally generated Python instead of MATLAB.
    Raises ValueError with a clear message.
    """
    python_signals = [
        (r"^def\s+\w+\s*\(", "Python function definition (def)"),
        (r"^import\s+\w+",    "Python import statement"),
        (r"^from\s+\w+\s+import", "Python from-import statement"),
        (r"^class\s+\w+",     "Python class definition"),
    ]
    for pattern, description in python_signals:
        if re.search(pattern, code, flags=re.MULTILINE):
            raise ValueError(
                f"LLM generated Python code instead of MATLAB ({description}).\n"
                f"File: {file_name}.m\n"
                f"First 200 chars:\n{code[:200]}"
            )

    if not code.lstrip().lower().startswith("function"):
        logger.warning(
            "Generated code for %s.m does not start with 'function'. "
            "It may be a script — that is acceptable for simple tasks.",
            file_name,
        )


def _fix_test_call(plan: dict[str, Any], code: str) -> None:
    """
    Parse the actual MATLAB function signature from the generated code and
    correct plan['test_call'] so the argument count and syntax match exactly.

    Prevents LLM syntax mistakes (like mismatched brackets/parentheses) from
    crashing the test verification step.
    """
    func_name = plan["file_name"]

    # Extract the function signature: function out = name(a, b, c, ...)
    sig_match = re.search(
        rf"function\s+.*?=\s*{re.escape(func_name)}\s*\(([^)]*)\)",
        code,
        flags=re.IGNORECASE,
    )
    if not sig_match:
        return  # Can't parse — leave test_call as-is

    raw_params = sig_match.group(1).strip()
    if not raw_params:
        plan["test_call"] = f"disp({func_name}())"
        logger.info("Fixed test_call: no inputs detected -> %s", plan["test_call"])
        return

    actual_params = [p.strip() for p in raw_params.split(",") if p.strip()]
    n_actual = len(actual_params)

    # Parse the existing test_call args with bracket/quote awareness
    tc = plan.get("test_call", "")
    func_start = tc.find(f"{func_name}(")
    if func_start != -1:
        args_start = func_start + len(func_name) + 1
        paren_level = 1
        tc_args_raw = ""
        for idx in range(args_start, len(tc)):
            char = tc[idx]
            if char == "(":
                paren_level += 1
            elif char == ")":
                paren_level -= 1
            if paren_level == 0:
                tc_args_raw = tc[args_start:idx]
                break
        else:
            tc_args_raw = tc[args_start:]

        # Parse args without splitting inside brackets/braces/quotes
        tc_args, current_arg = [], []
        bracket_level = paren_level = brace_level = 0
        in_quote = False
        for char in tc_args_raw.strip():
            if char == "'" and not in_quote:
                in_quote = True
            elif char == "'" and in_quote:
                in_quote = False
            elif not in_quote:
                if char == "[": bracket_level += 1
                elif char == "]": bracket_level -= 1
                elif char == "(": paren_level += 1
                elif char == ")": paren_level -= 1
                elif char == "{": brace_level += 1
                elif char == "}": brace_level -= 1

            if (char == "," and bracket_level == 0
                    and paren_level == 0 and brace_level == 0 and not in_quote):
                tc_args.append("".join(current_arg).strip())
                current_arg = []
            else:
                current_arg.append(char)
        if current_arg:
            tc_args.append("".join(current_arg).strip())
        tc_args = [a for a in tc_args if a]
        n_tc = len(tc_args)
    else:
        tc_args, n_tc = [], 0

    if n_tc == n_actual:
        return  # Already correct

    # Rebuild with type-aware defaults for missing args
    new_args = list(tc_args[:n_actual])
    for i in range(len(new_args), n_actual):
        input_desc = ""
        inputs = plan.get("inputs", [])
        if i < len(inputs):
            input_desc = (inputs[i].get("type", "") + " " + inputs[i].get("description", "")).lower()

        if "string" in input_desc or "char" in input_desc:
            fallback = "'test'"
        elif "matrix" in input_desc or "vector" in input_desc or "array" in input_desc:
            fallback = "[1, 2]"
        else:
            fallback = "1"
        new_args.append(fallback)

    plan["test_call"] = f"disp({func_name}({', '.join(new_args)}))"
    logger.info("Fixed test_call: %d->%d args -> %s", n_tc, n_actual, plan["test_call"])
