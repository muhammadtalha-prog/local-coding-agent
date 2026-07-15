"""
agents/debugger.py — MATLAB Code Fixer

Receives the failing source code + MATLAB error output and generates
a corrected version of the .m file.

Design for small models:
  - Only the error + code are passed (no full pipeline history)
  - Context budget: ~800 tokens for error + code, 800 tokens for fix
  - Hard exits on toolbox errors (cannot be fixed by the model)
"""
import logging
import re
from pathlib import Path
from typing import Any

from config import DEBUGGER_SYSTEM_PROMPT, SANDBOX_DIR
from agents.llm_client import call_llm
from agents.matlab_executor import MatlabExecutor

logger = logging.getLogger("matlab_agent.debugger")

TOOLBOX_UNFIXABLE_SIGNAL = "TOOLBOX_ERROR_UNFIXABLE"


def fix_code(
    plan: dict[str, Any],
    current_code: str,
    error_output: str,
) -> tuple[bool, Path, str]:
    """
    Ask the LLM to fix MATLAB code based on the error output.

    Returns:
        (fixable: bool, matlab_file_path, corrected_code)
        fixable=False means a toolbox error was detected — halt the pipeline.

    Raises:
        RuntimeError — propagated from LLM failures.
    """
    file_name = plan["file_name"]
    logger.info("Debugging %s.m — error length: %d chars", file_name, len(error_output))

    # Check for toolbox errors before even calling the LLM
    if MatlabExecutor.is_toolbox_error(error_output):
        logger.error(
            "Toolbox function detected in error — cannot be fixed without licensed MATLAB toolbox."
        )
        return False, SANDBOX_DIR / f"{file_name}.m", current_code

    # Trim error output to avoid context overflow (last 1500 chars is usually enough)
    trimmed_error = error_output[-1500:] if len(error_output) > 1500 else error_output

    # Trim code similarly (first 2000 chars captures function signature + most logic)
    trimmed_code = current_code[:2000] if len(current_code) > 2000 else current_code

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

    raw = call_llm(user_prompt, system_prompt=DEBUGGER_SYSTEM_PROMPT)

    # Check for the unfixable signal
    if TOOLBOX_UNFIXABLE_SIGNAL in raw:
        logger.error("Debugger signaled TOOLBOX_ERROR_UNFIXABLE — halting pipeline.")
        return False, SANDBOX_DIR / f"{file_name}.m", current_code

    # Extract and clean the code
    fixed_code = _extract_code(raw)

    # Validate it's actually MATLAB and not Python
    if _is_python_code(fixed_code):
        logger.warning("Debugger returned Python code — keeping original and flagging error.")
        return True, SANDBOX_DIR / f"{file_name}.m", current_code  # Let executor catch it again

    # Write fixed code to sandbox
    out_path = SANDBOX_DIR / f"{file_name}.m"
    out_path.write_text(fixed_code, encoding="utf-8")
    logger.info("Wrote fixed code to: %s", out_path)

    return True, out_path, fixed_code


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
