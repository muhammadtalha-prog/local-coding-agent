import logging
import time
from typing import Tuple, List
from memory import MemoryAgent
import settings

logger = logging.getLogger("avionics_framework.timer")


class TimerAgent:
    def __init__(self, memory_agent: MemoryAgent):
        self.memory = memory_agent
        self._pipeline_start: float = time.monotonic()

    def reset_pipeline_clock(self) -> None:
        """Reset the pipeline start time (call at the start of each orchestrate() run)."""
        self._pipeline_start = time.monotonic()

    def check_pipeline_timeout(self) -> Tuple[bool, str]:
        """
        Check whether the global pipeline wall-clock timeout has been exceeded.
        Returns:
            (timed_out: bool, message: str)
        """
        elapsed = time.monotonic() - self._pipeline_start
        limit = settings.PIPELINE_TIMEOUT_SEC
        if elapsed >= limit:
            msg = (
                f"Pipeline exceeded global timeout of {limit}s "
                f"(elapsed: {elapsed:.0f}s). Session state saved — "
                "rerun with the same --session flag to resume."
            )
            self.memory.log_event("TimerAgent", msg)
            return True, msg
        remaining = limit - elapsed
        self.memory.log_event(
            "TimerAgent",
            f"Pipeline clock OK — {elapsed:.0f}s elapsed, {remaining:.0f}s remaining."
        )
        return False, f"{remaining:.0f}s remaining in pipeline budget"

    def verify_loop_safety(self, current_error: str) -> Tuple[bool, str]:
        """
        Verifies if we can proceed with debugging or if we are stuck/exceeded bounds.

        Return values use prefixes so head.py can route them correctly:
          - ``INSTALL:<pkg>``  — attempt pip install of missing package
          - ``TOOLBOX:<msg>``  — licensed toolbox function unavailable; halt cleanly
          - ``False, <msg>``   — generic environment or loop-limit halt
          - ``True, <msg>``    — safe to continue debugging
        """
        max_debug_loops = settings.MAX_DEBUG_LOOPS
        loop_count = self.memory.state.get("loop_count", 0)
        self.memory.log_event(
            "TimerAgent",
            f"Evaluating debug iteration quality. Current loop count: {loop_count}/{max_debug_loops}"
        )

        lower_err = current_error.lower()

        # Active Lessons Learned on fatal error
        if "local function name must be different from the script name" in lower_err:
            self.memory.save_lesson({
                "mistake": "Local function name conflict",
                "correction": "MATLAB main function name must match filename and no local function can share this name."
            })
        if "stub: llm generated python code" in lower_err:
            self.memory.save_lesson({
                "mistake": "Python syntax leaked into MATLAB code",
                "correction": "Ensure MATLAB file ends with function declaration matching filename and contains pure MATLAB syntax (no def, import, or class)."
            })

        # --- Missing Python module → try auto-install ---
        import re
        match = re.search(
            r"ModuleNotFoundError:\s*No\s*module\s*named\s*['\"]([^'\"]+)['\"]",
            current_error
        )
        if match:
            missing_module = match.group(1)
            if missing_module != "sandbox" and not missing_module.startswith("sandbox."):
                msg = f"Missing python package: {missing_module}. Attempting automatic installation..."
                self.memory.log_event("TimerAgent", msg)
                return False, f"INSTALL:{missing_module}"

        # --- MATLAB licensed-toolbox functions → clean halt (not a planner escalation) ---
        toolbox_indicators = [
            "undefined function 'butter'",
            "undefined function 'filtfilt'",
            "undefined function 'freqz'",
            "undefined function 'designfilt'",
            "undefined function 'tf'",
            "undefined function 'lsim'",
            "undefined function 'ss'",
            "undefined function 'bode'",
            "undefined function 'nyquist'",
            "undefined function 'step'",
        ]
        for indicator in toolbox_indicators:
            if indicator in lower_err:
                msg = (
                    f"TOOLBOX:{indicator!r} requires a licensed MATLAB toolbox that is "
                    "not available in this environment. The code must be rewritten using "
                    "only base MATLAB without any licensed toolbox functions."
                )
                self.memory.log_event("TimerAgent", msg)
                return False, msg

        # --- MATLAB code generation errors → escalate to planner for full regeneration ---
        matlab_regen_indicators = [
            "invalid text character",           # non-ASCII chars in .m file
            "stub: llm generated python code",  # our Python-in-MATLAB stub
            "auto-generated stub",              # our stub header
            "unsupported symbol",               # MATLAB parse error for bad chars
        ]
        for indicator in matlab_regen_indicators:
            if indicator in lower_err:
                msg = (
                    f"MATLAB code generation error detected: '{indicator}'. "
                    "The LLM generated invalid or wrong-language code. Escalating to Planner for full regeneration."
                )
                self.memory.log_event("TimerAgent", msg)
                return False, msg

        # --- Generic environment errors the debugger cannot fix ---
        env_error_indicators = [
            "cannot find the file specified",
            "no module named",
            "command not found",
            "matlab not found",
            "unrecognized as the name of a cmdlet",
            "is not recognized as an internal or external command",
            # NOTE: bare "undefined function" stays here as last-resort catch-all
            # but is only hit if none of the specific toolbox_indicators above matched
            "undefined function",
        ]
        for indicator in env_error_indicators:
            if indicator in lower_err:
                msg = (
                    f"Environment error detected: '{indicator}'. "
                    "The debugger cannot fix system/environment issues. Halting loop."
                )
                self.memory.log_event("TimerAgent", msg)
                return False, msg

        # --- Loop count limit ---
        if loop_count >= max_debug_loops:
            msg = (
                f"Maximum debugging limit ({max_debug_loops}) reached. "
                "Halting execution to prevent run-away resource loop."
            )
            self.memory.log_event("TimerAgent", msg)
            return False, msg

        # --- Stagnation check: identical consecutive errors ---
        logs = self.memory.state.get("logs", [])
        error_logs: List[str] = [
            entry["message"]
            for entry in logs
            if "Execution error trace" in entry["message"]
        ]
        if len(error_logs) >= 2:
            if self._normalize_error(error_logs[-1]) == self._normalize_error(error_logs[-2]):
                msg = (
                    "Stagnant error signature detected (consecutive identical failures). "
                    "The coding agent is failing to make progress. Halting loop."
                )
                self.memory.log_event("TimerAgent", msg)
                return False, msg

        self.memory.log_event("TimerAgent", "Quality check passed. Proceeding with debugging sequence.")
        return True, "Safe to continue"

    def _normalize_error(self, error_text: str) -> str:
        """Strip paths, line numbers, and whitespace to expose the core error pattern."""
        import re
        normalized = error_text.lower()
        normalized = re.sub(r'file "[^"]+"', 'file ""', normalized)
        normalized = re.sub(r'line \d+', 'line 0', normalized)
        normalized = "".join(normalized.split())
        return normalized
