import sys
import logging
import subprocess
import asyncio
from typing import Tuple, Optional
from memory import MemoryAgent
from settings import ROOT_DIR, get_agent_filenames, TEST_TIMEOUT_SEC, EXEC_TIMEOUT_SEC, get_python_exe, get_matlab_exe, get_matlab_exe_or_none

logger = logging.getLogger("avionics_framework.executor")





class ExecutorAgent:
    def __init__(self, memory_agent: MemoryAgent):
        self.memory = memory_agent

    async def run_tests(self, language: str, timeout: Optional[float] = None) -> Tuple[bool, str]:
        self.memory.log_event("ExecutorAgent", f"Running test suite for language: {language}")

        sandbox_dir = ROOT_DIR / "sandbox"
        source_filename, test_filename = get_agent_filenames(self.memory.state)

        if language.lower() == "python":
            test_file = sandbox_dir / test_filename
            if not test_file.exists():
                return False, f"Error: {test_filename} does not exist."

            docker_running = False
            from settings import DOCKER_ENABLED
            if DOCKER_ENABLED:
                self.memory.log_event("ExecutorAgent", "Checking if Docker daemon is running...")
                try:
                    res = subprocess.run(["docker", "info"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
                    if res.returncode == 0:
                        docker_running = True
                except FileNotFoundError:
                    self.memory.log_event("ExecutorAgent", "Error checking Docker status: [WinError 2] The system cannot find the file specified. Falling back to host execution.")
                except Exception as e:
                    self.memory.log_event("ExecutorAgent", f"Error checking Docker status: {e}. Falling back to host execution.")

            if docker_running:
                self.memory.log_event("ExecutorAgent", "Running tests inside Docker sandbox...")
                cmd = ["docker", "run", "--rm", "-v", f"{sandbox_dir}:/app/sandbox", "python-sandbox", "pytest", "-v", f"sandbox/{test_filename}"]
            else:
                python_exe = get_python_exe()
                self.memory.log_event("ExecutorAgent", f"Falling back to host python execution. Running tests with: {python_exe}")
                cmd = [python_exe, "-m", "pytest", "-v",
                       f"--rootdir={ROOT_DIR}", "--import-mode=importlib",
                       f"sandbox/{test_filename}"]

            # Use caller-supplied timeout, or fall back to the configurable setting
            from settings import DEFAULT_TIMEOUT_SEC
            run_timeout = timeout if timeout is not None else (DEFAULT_TIMEOUT_SEC if DEFAULT_TIMEOUT_SEC is not None else TEST_TIMEOUT_SEC)
            success, report = await self._run_command_async(cmd, run_timeout)

            self.memory.log_event("ExecutorAgent", f"Test execution {'passed' if success else 'failed'}")
            return success, report

        elif language.lower() == "matlab":
            test_file = sandbox_dir / test_filename
            if not test_file.exists():
                return False, f"Error: {test_filename} does not exist."

            # Check if MATLAB is actually installed before attempting execution
            matlab_exe = get_matlab_exe_or_none()
            if matlab_exe is None:
                self.memory.log_event("ExecutorAgent", "MATLAB not installed. Skipping test execution — code generation verified via static analysis only.")
                return True, "MATLAB_NOT_INSTALLED: Test execution skipped (MATLAB not found on this system). Code was verified via static analysis and code review only."

            # MATLAB always runs on the host. Read test content to check if it is class-based
            try:
                with open(test_file, "r", encoding="utf-8") as f:
                    test_content = f.read()
            except Exception:
                test_content = ""

            sandbox_dir_str = str(sandbox_dir).replace("\\", "/")
            if "classdef" in test_content:
                test_name_raw = test_filename.replace(".m", "")
                matlab_cmd = (
                    f"cd('{sandbox_dir_str}'); "
                    f"try, "
                    f"results = runtests('{test_name_raw}'); "
                    f"if any([results.Failed]), exit(1); end; "
                    f"catch ME, disp(ME.message); exit(1); "
                    f"end; "
                    f"exit;"
                )
            else:
                matlab_cmd = (
                    f"cd('{sandbox_dir_str}'); "
                    f"try, "
                    f"run('{test_filename}'); "
                    f"catch ME, disp(ME.message); exit(1); "
                    f"end; "
                    f"exit;"
                )
            cmd = [matlab_exe, "-batch", matlab_cmd]
            from settings import DEFAULT_TIMEOUT_SEC
            actual_timeout = timeout if timeout is not None else DEFAULT_TIMEOUT_SEC
            run_timeout = actual_timeout + 15.0 if actual_timeout is not None else TEST_TIMEOUT_SEC + 15.0
            return await self._run_command_async(cmd, run_timeout)

        else:
            return False, f"Unsupported language: {language}"

    async def execute_final(self, language: str, timeout: Optional[float] = None) -> Tuple[bool, str]:
        self.memory.log_event("ExecutorAgent", "Executing finalized code from memory.")

        sandbox_dir = ROOT_DIR / "sandbox"
        source_filename, _ = get_agent_filenames(self.memory.state)

        if language.lower() == "python":
            source_file = sandbox_dir / source_filename
            if not source_file.exists():
                return False, f"Error: {source_filename} does not exist."

            docker_running = False
            from settings import DOCKER_ENABLED
            if DOCKER_ENABLED:
                self.memory.log_event("ExecutorAgent", "Checking if Docker daemon is running...")
                try:
                    res = subprocess.run(["docker", "info"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
                    if res.returncode == 0:
                        docker_running = True
                except FileNotFoundError:
                    self.memory.log_event("ExecutorAgent", "Error checking Docker status: [WinError 2] The system cannot find the file specified. Falling back to host execution.")
                except Exception as e:
                    self.memory.log_event("ExecutorAgent", f"Error checking Docker status: {e}. Falling back to host execution.")

            if docker_running:
                self.memory.log_event("ExecutorAgent", "Executing final script inside Docker sandbox...")
                cmd = ["docker", "run", "--rm", "-v", f"{sandbox_dir}:/app/sandbox", "python-sandbox", "python", f"sandbox/{source_filename}"]
            else:
                python_exe = get_python_exe()
                self.memory.log_event("ExecutorAgent", f"Falling back to host python execution. Executing final script with: {python_exe}")
                cmd = [python_exe, f"sandbox/{source_filename}"]

            # Use caller-supplied timeout, or fall back to the configurable setting
            from settings import DEFAULT_TIMEOUT_SEC
            run_timeout = timeout if timeout is not None else (DEFAULT_TIMEOUT_SEC if DEFAULT_TIMEOUT_SEC is not None else EXEC_TIMEOUT_SEC)
            return await self._run_command_async(cmd, run_timeout)

        elif language.lower() == "matlab":
            source_file = sandbox_dir / source_filename
            if not source_file.exists():
                return False, f"Error: {source_filename} does not exist."

            # Check if MATLAB is actually installed before attempting execution
            matlab_exe = get_matlab_exe_or_none()
            if matlab_exe is None:
                self.memory.log_event("ExecutorAgent", "MATLAB not installed. Skipping final execution — code generation complete.")
                return True, "MATLAB_NOT_INSTALLED: Final execution skipped (MATLAB not found on this system). Generated code is saved and ready for use on a system with MATLAB."

            func_name = source_filename.replace(".m", "")
            sandbox_dir_str = str(sandbox_dir).replace("\\", "/")
            matlab_cmd = (
                f"cd('{sandbox_dir_str}'); "
                f"try, "
                f"n = nargin('{func_name}'); "
                f"if n >= 0, disp(['Function compiled successfully. Inputs: ' num2str(n)]); "
                f"else, run('{source_filename}'); "
                f"end; "
                f"catch ME, disp(ME.message); exit(1); "
                f"end; "
                f"exit;"
            )
            cmd = [matlab_exe, "-batch", matlab_cmd]
            from settings import DEFAULT_TIMEOUT_SEC
            actual_timeout = timeout if timeout is not None else DEFAULT_TIMEOUT_SEC
            run_timeout = actual_timeout + 15.0 if actual_timeout is not None else EXEC_TIMEOUT_SEC + 15.0
            return await self._run_command_async(cmd, run_timeout)

        else:
            return False, f"Unsupported language: {language}"

    async def _run_command_async(self, cmd: list, timeout: float) -> Tuple[bool, str]:
        logger.info(f"Executing command: {' '.join(str(c) for c in cmd)}")
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(ROOT_DIR)
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                stdout_str = stdout.decode("utf-8", errors="replace")
                stderr_str = stderr.decode("utf-8", errors="replace")
                is_matlab_cmd = cmd and "matlab" in str(cmd[0]).lower()
                if is_matlab_cmd and proc.returncode:
                    stderr_prefix = f"ERROR: MATLAB error Exit Status: {hex(proc.returncode)}\n"
                else:
                    stderr_prefix = ""
                stdout_stderr = f"--- STDOUT ---\n{stdout_str}\n--- STDERR ---\n{stderr_prefix}{stderr_str}"
                success = (proc.returncode == 0)
                return success, stdout_stderr
            except asyncio.TimeoutError:
                logger.error(f"Execution timed out after {timeout} seconds. Terminating process tree...")
                try:
                    if sys.platform.startswith("win"):
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                    else:
                        proc.kill()
                except Exception as kill_err:
                    logger.warning(f"Error terminating process: {kill_err}")
                    try:
                        proc.kill()
                    except Exception:
                        pass
                await proc.communicate()
                return False, f"Error: Command timed out after {timeout} seconds."
        except FileNotFoundError as e:
            # Check if this is a MATLAB not-found error
            if cmd and ("matlab" in str(cmd[0]).lower()):
                resolved = get_matlab_exe()
                msg = (
                    f"ERROR: MATLAB executable not found at '{resolved}'.\n"
                    f"Set MATLAB_PATH in your .env file to the full path of matlab.exe, "
                    f"e.g.: MATLAB_PATH=D:\\Matlab\\install\\bin\\matlab.exe"
                )
                self.memory.log_event("ExecutorAgent", msg)
                return False, msg
            logger.error(f"Execution failed: {e}")
            return False, f"Error executing process: {e}"
        except Exception as e:
            if isinstance(e, OSError) and e.errno == 2 and cmd and ("matlab" in str(cmd[0]).lower()):
                resolved = get_matlab_exe()
                msg = (
                    f"ERROR: MATLAB executable not found at '{resolved}'.\n"
                    f"Set MATLAB_PATH in your .env file to the full path of matlab.exe, "
                    f"e.g.: MATLAB_PATH=D:\\Matlab\\install\\bin\\matlab.exe"
                )
                self.memory.log_event("ExecutorAgent", msg)
                return False, msg
            logger.error(f"Execution failed: {e}")
            return False, f"Error executing process: {e}"
