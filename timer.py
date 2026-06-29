import logging
from typing import Tuple, List
from memory import MemoryAgent
import settings

logger = logging.getLogger("avionics_framework.timer")

class TimerAgent:
    def __init__(self, memory_agent: MemoryAgent):
        self.memory = memory_agent

    def verify_loop_safety(self, current_error: str) -> Tuple[bool, str]:
        """
        Verifies if we can proceed with debugging or if we are stuck/exceeded bounds.
        Returns:
            Tuple[bool, str]: (should_continue, explanation_message)
        """
        # Read dynamically so --max-loops CLI override takes effect
        max_debug_loops = settings.MAX_DEBUG_LOOPS
        loop_count = self.memory.state.get("loop_count", 0)
        self.memory.log_event("TimerAgent", f"Evaluating debug iteration quality. Current loop count: {loop_count}/{max_debug_loops}")

        # Check for missing python module imports
        import re
        match = re.search(r"ModuleNotFoundError:\s*No\s*module\s*named\s*['\"]([^'\"]+)['\"]", current_error)
        if match:
            missing_module = match.group(1)
            if missing_module != "sandbox" and not missing_module.startswith("sandbox."):
                msg = f"Missing python package: {missing_module}. Attempting automatic installation..."
                self.memory.log_event("TimerAgent", msg)
                return False, f"INSTALL:{missing_module}"

        # Check for environment-related errors that the debugger cannot fix
        env_error_indicators = [
            "cannot find the file specified",
            "no module named",
            "command not found",
            "matlab not found",
            "unrecognized as the name of a cmdlet",
            "is not recognized as an internal or external command",
        ]

        # MATLAB-specific code generation errors — escalate to planner instead of looping
        matlab_regen_indicators = [
            "invalid text character",           # non-ASCII chars in .m file
            "stub: llm generated python code",  # our Python-in-MATLAB stub
            "auto-generated stub",              # our stub header
            "unsupported symbol",               # MATLAB parse error for bad chars
        ]

        lower_err = current_error.lower()
        for indicator in matlab_regen_indicators:
            if indicator in lower_err:
                msg = (
                    f"MATLAB code generation error detected: '{indicator}'. "
                    "The LLM generated invalid or wrong-language code. Escalating to Planner for full regeneration."
                )
                self.memory.log_event("TimerAgent", msg)
                return False, msg

        for indicator in env_error_indicators:
            if indicator in lower_err:
                msg = f"Environment error detected: '{indicator}'. The debugger cannot fix system/environment issues. Halting loop."
                self.memory.log_event("TimerAgent", msg)
                return False, msg


        # Check limit
        if loop_count >= max_debug_loops:
            msg = f"Maximum debugging limit ({max_debug_loops}) reached. Halting execution to prevent run-away resource loop."
            self.memory.log_event("TimerAgent", msg)
            return False, msg

        # Check error progression (are we stuck in an identical error pattern?)
        logs = self.memory.state.get("logs", [])
        error_logs: List[str] = []
        for entry in logs:
            if "Execution error trace" in entry["message"]:
                error_logs.append(entry["message"])

        # If the last two execution errors are identical (excluding timestamps or randomized data), we might be stuck
        if len(error_logs) >= 2:
            last_err = error_logs[-1]
            prev_err = error_logs[-2]
            
            # Simple clean/normalize to compare
            if self._normalize_error(last_err) == self._normalize_error(prev_err):
                msg = "Stagnant error signature detected (consecutive identical failures). The coding agent is failing to make progress. Halting loop."
                self.memory.log_event("TimerAgent", msg)
                return False, msg

        self.memory.log_event("TimerAgent", "Quality check passed. Proceeding with debugging sequence.")
        return True, "Safe to continue"

    def _normalize_error(self, error_text: str) -> str:
        # Strip directories, timestamps, and whitespace to find core error pattern
        normalized = error_text.lower()
        # Remove paths
        import re
        normalized = re.sub(r'file "[^"]+"', 'file ""', normalized)
        normalized = re.sub(r'line \d+', 'line 0', normalized)
        normalized = "".join(normalized.split())
        return normalized
