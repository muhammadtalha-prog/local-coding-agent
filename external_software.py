import subprocess
import os
import re
import threading
import time

class ProcessRegistry:
    """
    Registry to track and manage active subprocesses for each task,
    enabling asynchronous termination on task cancellation.
    """
    _lock = threading.Lock()
    _active_processes = {}  # task_id -> list of subprocess.Popen

    @classmethod
    def register(cls, task_id: str, proc: subprocess.Popen):
        if not task_id:
            return
        with cls._lock:
            if task_id not in cls._active_processes:
                cls._active_processes[task_id] = []
            cls._active_processes[task_id].append(proc)

    @classmethod
    def unregister(cls, task_id: str, proc: subprocess.Popen):
        if not task_id:
            return
        with cls._lock:
            if task_id in cls._active_processes:
                try:
                    cls._active_processes[task_id].remove(proc)
                except ValueError:
                    pass

    @classmethod
    def cancel_task_processes(cls, task_id: str):
        with cls._lock:
            if task_id in cls._active_processes:
                print(f"🛑 Terminating active processes for task: {task_id}...")
                for proc in cls._active_processes[task_id]:
                    try:
                        proc.terminate()
                        proc.wait(timeout=1)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                cls._active_processes[task_id] = []

class TaskStatusRegistry:
    """
    Thread-safe registry to track task cancellation flags and heartbeats.
    """
    _lock = threading.Lock()
    _cancelled_tasks = set()
    _heartbeats = {}  # task_id -> timestamp

    @classmethod
    def cancel(cls, task_id: str):
        with cls._lock:
            cls._cancelled_tasks.add(task_id)
        # Kill any active processes belonging to this task
        ProcessRegistry.cancel_task_processes(task_id)

    @classmethod
    def is_cancelled(cls, task_id: str) -> bool:
        if not task_id:
            return False
        with cls._lock:
            return task_id in cls._cancelled_tasks

    @classmethod
    def update_heartbeat(cls, task_id: str):
        if not task_id:
            return
        with cls._lock:
            cls._heartbeats[task_id] = time.time()

    @classmethod
    def get_last_heartbeat(cls, task_id: str) -> float:
        with cls._lock:
            return cls._heartbeats.get(task_id, 0.0)

class ExternalSoftwareAgent:
    """
    Manages script writing, command execution, and error output parsing
    across multiple programming languages and runtime environments (e.g. Python, MATLAB).
    """

    def write_script_file(self, filename: str, content: str, workspace_dir: str) -> str:
        """
        Writes a script file inside the designated workspace directory.
        """
        full_path = os.path.join(workspace_dir, filename)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        try:
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return f"Success: Script written to {filename}"
        except Exception as e:
            return f"Error writing script: {str(e)}"

    def execute_command(self, command: str, cwd: str, timeout: int = 60, task_id: str = None) -> dict:
        """
        Runs a generic shell command inside the specified workspace directory.
        Resolves 'python' and 'python3' to the active venv path using regex to support chained calls.
        Tracks processes for task cancellation and spawns a thread to update task heartbeats.
        """
        # Resolve 'python' or 'python3' to venv's python interpreter to prevent Windows Store hangs
        from tools import get_venv_path
        python_exe, _ = get_venv_path(cwd)
        # Use regex to replace all standalone occurrences of python/python3. Escape backslashes in path for re.sub on Windows.
        python_exe_escaped = f'"{python_exe}"'.replace('\\', '\\\\')
        command = re.sub(r'\bpython3?\b', python_exe_escaped, command)

        # Handle mock MATLAB execution if MATLAB is not installed
        if command.startswith("matlab "):
            import shutil
            if not shutil.which("matlab"):
                print("⚠️ MATLAB not found in PATH. Using Mock MATLAB Executor...")
                return self._execute_mock_matlab(command, cwd)

        try:
            # Popen is used to track and cancel processes
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
            
            # Register process
            ProcessRegistry.register(task_id, proc)
            
            # Start background heartbeat updater thread to prevent watchdog timeouts
            # during long-running tool or script execution.
            heartbeat_stop_event = threading.Event()
            def heartbeat_updater():
                while not heartbeat_stop_event.is_set():
                    if proc.poll() is not None:
                        break
                    TaskStatusRegistry.update_heartbeat(task_id)
                    heartbeat_stop_event.wait(timeout=10)

            if task_id:
                updater_thread = threading.Thread(target=heartbeat_updater, name=f"Heartbeat-{task_id}", daemon=True)
                updater_thread.start()
            
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
                return {
                    "exit_code": proc.returncode,
                    "stdout": stdout,
                    "stderr": stderr
                }
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
                return {
                    "exit_code": -1,
                    "stdout": stdout,
                    "stderr": f"Execution timed out after {timeout} seconds."
                }
            finally:
                if task_id:
                    heartbeat_stop_event.set()
                ProcessRegistry.unregister(task_id, proc)
                
        except Exception as e:
            return {
                "exit_code": -2,
                "stdout": "",
                "stderr": f"Failed to execute command: {str(e)}"
            }

    def _execute_mock_matlab(self, command: str, cwd: str) -> dict:
        """
        Simulates MATLAB script runs, checking syntax and helper files.
        """
        script_match = re.search(r'-batch\s+"([^"]+)"', command) or re.search(r'-r\s+"([^"]+)"', command)
        if not script_match:
            return {
                "exit_code": -2,
                "stdout": "",
                "stderr": "Mock MATLAB Error: Could not parse script name from batch command."
            }
        
        script_name = script_match.group(1)
        if not script_name.endswith(".m"):
            script_file = script_name + ".m"
        else:
            script_file = script_name
            
        script_path = os.path.join(cwd, script_file)
        if not os.path.exists(script_path):
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"Error: File '{script_file}' not found."
            }
            
        try:
            with open(script_path, 'r', encoding='utf-8') as f:
                code = f.read()
        except Exception as e:
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"Error reading file '{script_file}': {e}"
            }
            
        # Check for unterminated quotes in mock MATLAB code
        if code.count("'") % 2 != 0:
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"Error in {script_file} (line 5)\nCharacter vector is not terminated properly."
            }
            
        stdout_lines = []
        stderr_lines = []
        
        # Check custom function calls
        func_calls = re.findall(r'(\w+)\s*\(([^)]*)\)', code)
        for func_name, args_str in func_calls:
            if func_name in ("disp", "fprintf", "sprintf"):
                continue
            
            helper_file = f"{func_name}.m"
            helper_path = os.path.join(cwd, helper_file)
            if not os.path.exists(helper_path):
                stderr_lines.append(f"Undefined function or variable '{func_name}'.")
                return {
                    "exit_code": 1,
                    "stdout": "\n".join(stdout_lines),
                    "stderr": "\n".join(stderr_lines)
                }
            
            with open(helper_path, 'r', encoding='utf-8') as hf:
                helper_code = hf.read()
                
            # MATLAB helper must not look like Python
            if "def " in helper_code or ":" in helper_code or "print(" in helper_code:
                stderr_lines.append(f"Error in {helper_file} (line 1)\nInvalid syntax or python-style function declaration.")
                return {
                    "exit_code": 1,
                    "stdout": "\n".join(stdout_lines),
                    "stderr": "\n".join(stderr_lines)
                }
                
            if "function" not in helper_code:
                stderr_lines.append(f"Error in {helper_file}\nMATLAB helper files must begin with a function declaration.")
                return {
                    "exit_code": 1,
                    "stdout": "\n".join(stdout_lines),
                    "stderr": "\n".join(stderr_lines)
                }

        # Successful mock MATLAB output
        disp_calls = re.findall(r"disp\s*\(\s*'([^']*)'\s*\)", code)
        for val in disp_calls:
            stdout_lines.append(val)
            
        if not stdout_lines:
            stdout_lines.append("Execution completed successfully.")
            
        return {
            "exit_code": 0,
            "stdout": "\n".join(stdout_lines),
            "stderr": ""
        }

    def parse_output(self, stdout: str, stderr: str, language: str = "python") -> str:
        """
        Parses output streams to extract error details, line numbers, and context.
        """
        lang = language.lower()
        combined = f"STDOUT:\n{stdout}\n\nSTDERR:\n{stderr}"
        
        if lang == "python":
            tb_match = re.search(r'(Traceback \(most recent call last\):[\s\S]+)', stderr)
            if tb_match:
                return tb_match.group(1).strip()
            lines = stderr.strip().split('\n')
            for line in reversed(lines):
                if "Error:" in line or "Exception:" in line or "SyntaxError:" in line:
                    return line
            return stderr if stderr.strip() else stdout

        elif lang in ("matlab", "m"):
            lines = (stdout + "\n" + stderr).split('\n')
            errors = []
            capture = False
            for line in lines:
                if "Error in" in line or "Undefined function" in line:
                    capture = True
                if capture:
                    if line.strip():
                        errors.append(line.strip())
                    else:
                        capture = False
            if errors:
                return "\n".join(errors)
            return combined if (stderr.strip() or "Error" in stdout) else ""

        else:
            return combined

class PythonAgent(ExternalSoftwareAgent):
    def parse_output(self, stdout: str, stderr: str, language: str = "python") -> str:
        return super().parse_output(stdout, stderr, "python")

class MATLABAgent(ExternalSoftwareAgent):
    def parse_output(self, stdout: str, stderr: str, language: str = "matlab") -> str:
        return super().parse_output(stdout, stderr, "matlab")
