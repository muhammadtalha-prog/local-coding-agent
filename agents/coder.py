"""
agents/coder.py — MATLAB Code Generator

Takes a validated plan dict and generates a complete, executable MATLAB
.m function file. Writes it directly to the sandbox directory.

Key constraints for small (1.5-3B) models:
  - Prompt includes ONLY what the model needs: plan summary + rules
  - No full code history in context
  - Strict output: raw MATLAB code only
"""
import logging
import re
from pathlib import Path
from typing import Any

from config import CODER_SYSTEM_PROMPT, SANDBOX_DIR
from agents.llm_client import call_llm

logger = logging.getLogger("matlab_agent.coder")


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
    raw = call_llm(user_prompt, system_prompt=CODER_SYSTEM_PROMPT)
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
    """
    Extract raw MATLAB code from LLM response.
    Strips markdown fences and leading/trailing whitespace.
    """
    # Remove markdown code fences
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

    # Verify the function header is present
    expected_func = f"function"
    if not code.lstrip().lower().startswith(expected_func):
        logger.warning(
            "Generated code for %s.m does not start with 'function'. "
            "It may be a script — that is acceptable for simple tasks.",
            file_name,
        )


def _fix_test_call(plan: dict[str, Any], code: str) -> None:
    """
    Parse the actual MATLAB function signature from the generated code and
    correct plan['test_call'] so the argument count matches exactly.

    Fixes the common LLM mistake of generating a test_call with fewer
    arguments than the function actually requires.
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

    # Get the real input parameter names from the signature
    raw_params = sig_match.group(1).strip()
    if not raw_params:
        # No inputs — test_call should be: disp(func_name())
        plan["test_call"] = f"disp({func_name}())"
        logger.info("Fixed test_call: no inputs detected -> %s", plan["test_call"])
        return

    actual_params = [p.strip() for p in raw_params.split(",") if p.strip()]
    n_actual = len(actual_params)

    # Check how many args the current test_call passes
    tc = plan.get("test_call", "")
    tc_match = re.search(rf"{re.escape(func_name)}\s*\(([^)]*)\)", tc)
    if tc_match:
        tc_args_raw = tc_match.group(1).strip()
        tc_args = [a.strip() for a in tc_args_raw.split(",") if a.strip()] if tc_args_raw else []
        n_tc = len(tc_args)
    else:
        n_tc = 0

    if n_tc == n_actual:
        return  # Already correct

    # Rebuild test_call with numeric placeholders (1, 2, 3 ...) for missing args
    # Reuse existing args where possible, pad with incrementing numbers
    existing = tc_args if tc_match else []
    new_args = list(existing[:n_actual])  # keep what's there
    for i in range(len(new_args), n_actual):
        new_args.append(str(i + 1))  # pad with 1, 2, 3...

    plan["test_call"] = f"disp({func_name}({', '.join(new_args)}))"
    logger.info(
        "Fixed test_call: %d->%d args -> %s", n_tc, n_actual, plan["test_call"]
    )
