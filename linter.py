import logging
from pathlib import Path
from typing import Tuple
from memory import MemoryAgent
from settings import ROOT_DIR, LINT_TIMEOUT_SEC, get_python_exe

logger = logging.getLogger("avionics_framework.linter")





class LinterAgent:
    def __init__(self, memory_agent: MemoryAgent):
        self.memory = memory_agent

    async def _run_command(self, cmd: list, timeout: float = LINT_TIMEOUT_SEC) -> Tuple[int, str, str]:
        import asyncio
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(ROOT_DIR)
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                await proc.communicate()
                return -1, "", f"Lint command timed out after {timeout}s. Consider increasing LINT_TIMEOUT_SEC in .env."
            return (
                proc.returncode if proc.returncode is not None else -1,
                stdout.decode("utf-8", errors="replace"),
                stderr.decode("utf-8", errors="replace")
            )
        except Exception as e:
            return -1, "", str(e)

    async def lint(self, filename: str, language: str) -> Tuple[bool, str]:
        self.memory.log_event("LinterAgent", f"Starting static analysis for {filename}")

        sandbox_dir = ROOT_DIR / "sandbox"
        filepath = sandbox_dir / filename
        if not filepath.exists():
            return False, f"Error: File {filename} not found in sandbox."

        if language.lower() == "python":
            return await self._lint_python(filename, filepath)
        elif language.lower() == "matlab":
            return await self._lint_matlab(filepath)
        else:
            return False, f"Unsupported language: {language}"

    async def _lint_python(self, filename: str, filepath: Path) -> Tuple[bool, str]:
        logs = []
        all_passed = True

        docker_running = False
        import subprocess
        from settings import DOCKER_ENABLED
        if DOCKER_ENABLED:
            self.memory.log_event("LinterAgent", "Linter checking if Docker daemon is running...")
            try:
                res = subprocess.run(["docker", "info"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
                if res.returncode == 0:
                    docker_running = True
            except FileNotFoundError:
                self.memory.log_event("LinterAgent", "Error checking Docker status: [WinError 2] The system cannot find the file specified. Falling back to host execution.")
            except Exception as e:
                self.memory.log_event("LinterAgent", f"Error checking Docker status: {e}. Falling back to host execution.")

        if docker_running:
            self.memory.log_event("LinterAgent", "Running static analysis inside Docker sandbox...")
            ruff_cmd = ["docker", "run", "--rm", "-v", f"{ROOT_DIR}/sandbox:/app/sandbox", "python-sandbox", "ruff", "check", f"sandbox/{filename}"]
            mypy_cmd = ["docker", "run", "--rm", "-v", f"{ROOT_DIR}/sandbox:/app/sandbox", "python-sandbox", "mypy", f"sandbox/{filename}", "--ignore-missing-imports", "--explicit-package-bases"]
        else:
            python_exe = get_python_exe()
            self.memory.log_event("LinterAgent", f"Falling back to host python execution. Running static analysis with: {python_exe}")
            ruff_cmd = [python_exe, "-m", "ruff", "check", f"sandbox/{filename}"]
            mypy_cmd = [python_exe, "-m", "mypy", f"sandbox/{filename}", "--ignore-missing-imports", "--explicit-package-bases"]

        # 1. Run Ruff
        ret_code, stdout, stderr = await self._run_command(ruff_cmd)
        if ret_code != 0:
            all_passed = False
            logs.append("--- Ruff Code Quality Issues ---\n" + stdout + stderr)
        else:
            logs.append("Ruff check: PASSED")

        # 2. Run Mypy
        ret_code, stdout, stderr = await self._run_command(mypy_cmd)
        if ret_code != 0:
            all_passed = False
            logs.append("--- Mypy Type Checking Issues ---\n" + stdout + stderr)
        else:
            logs.append("Mypy check: PASSED")

        report = "\n".join(logs)
        status_msg = "PASSED all lint checks" if all_passed else "FAILED lint checks"
        self.memory.log_event("LinterAgent", f"Python static analysis complete: {status_msg}")
        return all_passed, report

    async def _lint_matlab(self, filepath: Path) -> Tuple[bool, str]:
        import asyncio
        logger.info("Checking for MATLAB lint tools...")
        from settings import get_matlab_exe_or_none
        matlab_exe = get_matlab_exe_or_none()
        if matlab_exe is None:
            self.memory.log_event("LinterAgent", "MATLAB command not found or not in PATH. Skipping mlint checks.")
            return True, "MATLAB static analysis skipped: MATLAB command not found in local system PATH."
        cmd = [matlab_exe, "-batch", f"mlint('{str(filepath)}')"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(ROOT_DIR)
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=LINT_TIMEOUT_SEC)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                await proc.communicate()
                self.memory.log_event("LinterAgent", "MATLAB mlint timed out. Skipping.")
                return True, "MATLAB mlint skipped: timed out."
            stdout_str = stdout.decode("utf-8", errors="replace")
            ret_code = proc.returncode if proc.returncode is not None else -1
            if ret_code == 0 and "L " in stdout_str:
                self.memory.log_event("LinterAgent", f"MATLAB mlint found suggestions:\n{stdout_str}")
                return True, f"MATLAB mlint Warnings (Non-fatal):\n{stdout_str}"
            self.memory.log_event("LinterAgent", "MATLAB static analysis complete/passed")
            return True, "MATLAB mlint: PASSED"
        except Exception:
            self.memory.log_event("LinterAgent", "MATLAB command not found or not in PATH. Skipping mlint checks.")
            return True, "MATLAB static analysis skipped: MATLAB command not found in local system PATH."
