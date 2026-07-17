"""
agents/debugger.py — MATLAB Code Fixer (AutoGen AssistantAgent)

Receives the failing source code + MATLAB error output and uses an AG2
AssistantAgent to generate a corrected version of the .m file.

Design for small models:
  - Only the error + code are passed (no full pipeline history)
  - Context budget: ~800 tokens for error + code, 800 tokens for fix
  - Hard exits on toolbox errors (cannot be fixed by the model)
"""
import logging
import re
from pathlib import Path
from typing import Any

from autogen import AssistantAgent, UserProxyAgent

from config import DEBUGGER_SYSTEM_PROMPT, LLM_CONFIG, SANDBOX_DIR
from agents.matlab_executor import MatlabExecutor

logger = logging.getLogger("matlab_agent.debugger")

TOOLBOX_UNFIXABLE_SIGNAL = "TOOLBOX_ERROR_UNFIXABLE"

# Suppress AutoGen internal verbose logging
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


def fix_code(
    plan: dict[str, Any],
    current_code: str,
    error_output: str,
) -> tuple[bool, Path, str]:
    """
    Ask the AutoGen DebuggerAgent to fix MATLAB code based on the error output.

    Returns:
        (fixable: bool, matlab_file_path, corrected_code)
        fixable=False means a toolbox error was detected — halt the pipeline.

    Raises:
        RuntimeError — propagated from LLM failures.
    """
    file_name = plan["file_name"]
    logger.info("Debugging %s.m — error length: %d chars", file_name, len(error_output))
    logger.info("Error output passed to debugger:\n%s", error_output)

    # Check for toolbox errors before even calling the LLM
    if MatlabExecutor.is_toolbox_error(error_output):
        logger.error(
            "Toolbox function detected in error — cannot be fixed without licensed MATLAB toolbox."
        )
        return False, SANDBOX_DIR / f"{file_name}.m", current_code

    # Trim error and code to avoid context overflow on small models
    trimmed_error = error_output[-1500:] if len(error_output) > 1500 else error_output
    trimmed_code  = current_code[:2000]  if len(current_code)  > 2000 else current_code

    user_prompt = (
        f"Fix this MATLAB function '{file_name}.m'.\n\n"
        f"--- CURRENT CODE ---\n"
        f"{trimmed_code}\n\n"
        f"--- MATLAB ERROR ---\n"
        f"{trimmed_error}\n\n"
        f"Output ONLY the corrected MATLAB code. "
        f"First line: function ... = {file_name}(...)\n"
        f"If the error requires a licensed toolbox to fix, output exactly: {TOOLBOX_UNFIXABLE_SIGNAL}"
    )

    # Create a fresh debugger agent per call
    debugger = AssistantAgent(
        name="DebuggerAgent",
        system_message=DEBUGGER_SYSTEM_PROMPT,
        llm_config=LLM_CONFIG,
        max_consecutive_auto_reply=1,
        human_input_mode="NEVER",
        code_execution_config=False,
    )

    proxy = UserProxyAgent(
        name="DebuggerProxy",
        human_input_mode="NEVER",
        max_consecutive_auto_reply=0,
        llm_config=False,
        code_execution_config=False,
        is_termination_msg=lambda _: True,
    )

    try:
        result = _quiet_chat(proxy, debugger, user_prompt)
        raw = _extract_last_reply(result)
    except Exception as exc:
        msg = str(exc).lower()
        if "timeout" in msg or "timed out" in msg:
            raise TimeoutError(f"Debugger LLM call timed out: {exc}")
        raise RuntimeError(f"Debugger LLM call failed: {exc}") from exc

    # Check for the unfixable signal
    if TOOLBOX_UNFIXABLE_SIGNAL in raw:
        logger.error("Debugger signaled TOOLBOX_ERROR_UNFIXABLE — halting pipeline.")
        return False, SANDBOX_DIR / f"{file_name}.m", current_code

    fixed_code = _extract_code(raw)

    # Validate it's actually MATLAB and not Python
    if _is_python_code(fixed_code):
        logger.error(
            "Debugger returned Python code instead of MATLAB — halting pipeline. "
            "This is unfixable without manual intervention."
        )
        return False, SANDBOX_DIR / f"{file_name}.m", current_code

    # Write fixed code to sandbox
    out_path = SANDBOX_DIR / f"{file_name}.m"
    out_path.write_text(fixed_code, encoding="utf-8")
    logger.info("Wrote fixed code to: %s", out_path)

    return True, out_path, fixed_code


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_last_reply(result) -> str:
    """Extract the last assistant message content from an AutoGen chat result."""
    history = getattr(result, "chat_history", None)
    if not history:
        raise RuntimeError("DebuggerAgent returned no response.")

    for msg in reversed(history):
        content = msg.get("content", "")
        if content and msg.get("name", "") not in ("DebuggerProxy",):
            return content

    raise RuntimeError("DebuggerAgent chat history contained no usable reply.")


def _extract_code(raw: str) -> str:
    """Strip markdown fences from LLM output."""
    raw = re.sub(r"```matlab\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"```m\s*",      "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"```\s*",       "", raw)
    return raw.strip()


def _is_python_code(code: str) -> bool:
    """Detect Python syntax in what should be MATLAB code."""
    python_patterns = [r"^def\s+\w+", r"^import\s+\w+", r"^from\s+\w+\s+import", r"^class\s+\w+"]
    for pattern in python_patterns:
        if re.search(pattern, code, flags=re.MULTILINE):
            return True
    return False
