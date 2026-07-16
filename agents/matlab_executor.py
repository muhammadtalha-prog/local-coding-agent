"""
agents/matlab_executor.py — Runs MATLAB code in a subprocess and returns results.

Designed for 8GB RAM systems:
  - Strict timeout prevents hanging on slow MATLAB startup
  - Windows taskkill terminates the full process tree on timeout
  - No Docker overhead
"""
import re
import subprocess
import sys
import logging
from pathlib import Path

from config import MATLAB_EXE, SANDBOX_DIR, MATLAB_EXEC_TIMEOUT_SEC, MATLAB_NO_JVM

logger = logging.getLogger("matlab_agent.executor")

# Characters allowed in a MATLAB test_call expression (allowlist sanitization)
_SAFE_TEST_CALL_RE = re.compile(r"^[a-zA-Z0-9_.,; @\[\]{}()'+\-*/^%~=<>!&|:\\\n\r\t ]+$")


class MatlabExecutor:
    """
    Executes a MATLAB .m file inside the sandbox directory using `matlab -batch`.
    Returns (success: bool, output: str).
    """

    def __init__(
        self,
        timeout: float = MATLAB_EXEC_TIMEOUT_SEC,
        work_dir: Path = SANDBOX_DIR,
    ) -> None:
        self.timeout = timeout
        self.work_dir = work_dir

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute_file(self, matlab_file: Path) -> tuple[bool, str]:
        """
        Synchronously execute a MATLAB .m file.
        Returns (success, combined_stdout_stderr).
        """
        if MATLAB_EXE is None:
            msg = (
                "MATLAB executable not found on this system.\n"
                "Set MATLAB_PATH in your .env file, e.g.:\n"
                "  MATLAB_PATH=D:\\Matlab\\install\\bin\\matlab.exe\n"
                "Generated code has been saved but could not be executed."
            )
            logger.warning(msg)
            return True, f"MATLAB_NOT_INSTALLED: {msg}"

        if not matlab_file.exists():
            return False, f"ERROR: File not found: {matlab_file}"

        # Build the -batch command string
        # We cd into the sandbox so relative addpath('.') works
        safe_dir = str(self.work_dir).replace("\\", "/")
        func_name = matlab_file.stem  # filename without .m

        # Try as a function first; fall back to run() for scripts
        matlab_cmd = (
            f"cd('{safe_dir}'); "
            f"try, "
            f"  n = nargin('{func_name}'); "
            f"  if n == 0, {func_name}(); "
            f"  else, disp(['Function OK — requires ' num2str(n) ' input(s)']); "
            f"  end; "
            f"catch ME, disp(ME.message); exit(1); "
            f"end; "
            f"exit(0);"
        )

        cmd = [MATLAB_EXE]
        if MATLAB_NO_JVM:
            cmd.append("-nojvm")
        cmd.extend(["-batch", matlab_cmd])
        logger.info("Running: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout,
                cwd=str(self.work_dir),
            )
            stdout = result.stdout.decode("utf-8", errors="replace")
            stderr = result.stderr.decode("utf-8", errors="replace")
            combined = f"--- STDOUT ---\n{stdout}\n--- STDERR ---\n{stderr}".strip()
            success = result.returncode == 0
            return success, combined

        except subprocess.TimeoutExpired:
            self._kill_matlab()
            return False, f"ERROR: MATLAB timed out after {self.timeout}s."

        except FileNotFoundError:
            return False, (
                f"ERROR: MATLAB executable not found at '{MATLAB_EXE}'.\n"
                "Set MATLAB_PATH in .env to the correct path."
            )

        except Exception as exc:
            return False, f"ERROR: Unexpected error running MATLAB: {exc}"

    def run_test_call(self, func_name: str, test_call: str) -> tuple[bool, str]:
        """
        Execute a single MATLAB test expression (e.g. 'disp(my_func(1,2))').
        Used to verify the generated function produces output without crashing.
        """
        if MATLAB_EXE is None:
            return True, "MATLAB_NOT_INSTALLED: Skipping test call verification."

        # Security: sanitize test_call to prevent command injection before
        # embedding it directly into the MATLAB -batch string
        if not _SAFE_TEST_CALL_RE.match(test_call):
            logger.warning("test_call contains unsafe characters — rejecting: %s", test_call)
            return False, "ERROR: test_call contains unsafe characters and was rejected."

        safe_dir = str(self.work_dir).replace("\\", "/")
        matlab_cmd = (
            f"cd('{safe_dir}'); "
            f"try, "
            f"  {test_call}; "
            f"catch ME, disp(ME.message); exit(1); "
            f"end; "
            f"exit(0);"
        )

        cmd = [MATLAB_EXE]
        if MATLAB_NO_JVM:
            cmd.append("-nojvm")
        cmd.extend(["-batch", matlab_cmd])
        logger.info("Test call: %s", test_call)

        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout,
                cwd=str(self.work_dir),
            )
            stdout = result.stdout.decode("utf-8", errors="replace")
            stderr = result.stderr.decode("utf-8", errors="replace")
            combined = f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}".strip()
            return result.returncode == 0, combined

        except subprocess.TimeoutExpired:
            self._kill_matlab()
            return False, f"ERROR: MATLAB test call timed out after {self.timeout}s."

        except Exception as exc:
            return False, f"ERROR: {exc}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _kill_matlab() -> None:
        """Kill any lingering MATLAB processes (Windows-specific)."""
        if sys.platform.startswith("win"):
            try:
                subprocess.run(
                    ["taskkill", "/F", "/IM", "matlab.exe"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            except Exception:
                pass

    @staticmethod
    def is_toolbox_error(error_output: str) -> bool:
        """
        Returns True if the error indicates a missing licensed MATLAB toolbox.
        These errors are unfixable by the debugger and should halt the pipeline.
        """
        toolbox_funcs = {
            "butter", "filtfilt", "freqz", "designfilt",
            "tf", "lsim", "ss", "bode", "nyquist", "step",
            "pidtune", "sisotool", "place", "acker",
        }
        lower = error_output.lower()
        for fn in toolbox_funcs:
            # Match standard undefined function or variable errors for the given function name
            if f"'{fn}'" in lower and (
                "undefined function" in lower
                or "undefined variable" in lower
                or "not found" in lower
            ):
                return True
        return False
