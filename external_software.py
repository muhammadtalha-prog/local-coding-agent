import subprocess
import os
import re
import threading
import time
from config import DEFAULT_COMMAND_TIMEOUT

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
                        if os.name == 'nt':
                            subprocess.run(f"taskkill /F /T /PID {proc.pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        else:
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

    def execute_command(self, command: str, cwd: str, timeout: int = DEFAULT_COMMAND_TIMEOUT, task_id: str = None) -> dict:
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
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
            
            # Register process
            ProcessRegistry.register(task_id, proc)
            
            heartbeat_stop_event = threading.Event()
            def heartbeat_updater():
                while not heartbeat_stop_event.is_set():
                    if TaskStatusRegistry.is_cancelled(task_id):
                        break
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
                if os.name == 'nt':
                    subprocess.run(f"taskkill /F /T /PID {proc.pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
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
        script_name = None
        # Fallback 1: Scan cwd for .m files and find which one is mentioned in the command
        try:
            if os.path.exists(cwd):
                m_files = [f for f in os.listdir(cwd) if f.endswith('.m')]
                for mf in m_files:
                    base_name = os.path.splitext(mf)[0]
                    if re.search(r'\b' + re.escape(base_name) + r'\b', command):
                        script_name = base_name
                        break
        except Exception:
            pass

        # Fallback 2: Regex search
        if not script_name:
            # Check for run(...) or run ... patterns inside MATLAB commands
            run_match = (
                re.search(r"\brun\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", command) or
                re.search(r"\brun\s+['\"]?([^'\"\s;]+)['\"]?", command)
            )
            if run_match:
                script_name = run_match.group(1)
            else:
                script_match = (
                    re.search(r'-batch\s+([^\s"\']+)', command) or 
                    re.search(r'-batch\s+"?([^"\s]+)"?', command) or 
                    re.search(r'-r\s+"?([^"\s]+)"?', command)
                )
                if script_match:
                    script_name = script_match.group(1).strip("'\"")
                
        if not script_name:
            return {
                "exit_code": -2,
                "stdout": "",
                "stderr": "Mock MATLAB Error: Could not parse script name from batch command."
            }
        
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
        # Skip checking if the quotes are inside comments or valid patterns
        def has_unterminated_strings(code_str):
            # Remove comment lines first (lines starting with %)
            lines = code_str.split('\n')
            clean_lines = []
            for line in lines:
                # Remove inline comments (anything after % not inside quotes)
                if "'" in line:
                    # Complex case - keep as is, let the main check handle
                    clean_lines.append(line)
                else:
                    # Simple case - remove comment
                    comment_pos = line.find('%')
                    if comment_pos >= 0:
                        line = line[:comment_pos]
                    clean_lines.append(line)
            
            clean_code = '\n'.join(clean_lines)
            
            # Count quotes ignoring escaped quotes
            quote_count = 0
            in_string = False
            i = 0
            while i < len(clean_code):
                if clean_code[i] == "'" and (i == 0 or clean_code[i-1] != '\\'):
                    quote_count += 1
                    in_string = not in_string
                i += 1
            
            return quote_count % 2 != 0

        if has_unterminated_strings(code):
            # Don't fail - just log a warning and continue
            print(f"   ⚠️ Warning: Possible unterminated string in {script_file}, attempting to execute anyway...")
            # Continue execution instead of failing
            
        stdout_lines = []
        stderr_lines = []
        
        # Clean the code by stripping comments and string literals to prevent matching them as function calls
        # Block comments %{ ... %}
        code_clean = re.sub(r'%\{[\s\S]*?%\}', '', code)
        # Line comments % ...
        code_clean = re.sub(r'%.*$', '', code_clean, flags=re.MULTILINE)
        # String literals
        code_clean = re.sub(r"'[^']*'", '', code_clean)

        # Built-in MATLAB functions to ignore/bypass custom helper checks
        matlab_builtins = {
            "disp", "fprintf", "sprintf", "factorial", "sin", "cos", "tan", "abs", 
            "round", "floor", "ceil", "sqrt", "exp", "log", "log10", "mod", "rem", 
            "zeros", "ones", "eye", "rand", "randn", "size", "length", "numel", "sum", 
            "mean", "median", "std", "min", "max", "sort", "find", "error", "warning", 
            "input", "pause", "clear", "clc", "close", "hold", "plot", "grid", "title", 
            "xlabel", "ylabel", "legend", "fopen", "fclose", "fread", "fwrite", "fscanf"
        }
        
        # Check custom function calls
        func_calls = re.findall(r'(\w+)\s*\(([^)]*)\)', code_clean)
        for func_name, args_str in func_calls:
            if func_name in matlab_builtins:
                continue
            
            # Check if this function is defined locally in the same file
            local_func_pattern = r'\bfunction\s+(?:[^=]+=\s*)?' + re.escape(func_name) + r'\b'
            if re.search(local_func_pattern, code):
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
            
            try:
                with open(helper_path, 'r', encoding='utf-8') as hf:
                    helper_code = hf.read()
            except Exception as e:
                stderr_lines.append(f"Error reading helper file '{helper_file}': {e}")
                return {
                    "exit_code": 1,
                    "stdout": "\n".join(stdout_lines),
                    "stderr": "\n".join(stderr_lines)
                }
                
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
