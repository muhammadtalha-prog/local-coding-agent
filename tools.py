import os
import subprocess
import sys
from config import WORKSPACE_DIR

def get_venv_path(workspace_dir=None):
    """
    Returns the path to the virtual environment python executable.
    Tries to reuse the project's root virtual environment if it exists
    to prevent performance overhead and device lag. Otherwise, creates one.
    """
    # 1. Try to find and reuse a root virtual environment
    project_root = os.path.abspath(os.path.dirname(__file__))
    root_venv_dir = os.path.join(project_root, "venv")
    if os.name == 'nt': # Windows
        root_python_exe = os.path.join(root_venv_dir, "Scripts", "python.exe")
        root_pip_exe = os.path.join(root_venv_dir, "Scripts", "pip.exe")
    else: # Unix/Mac
        root_python_exe = os.path.join(root_venv_dir, "bin", "python")
        root_pip_exe = os.path.join(root_venv_dir, "bin", "pip")

    if os.path.exists(root_python_exe):
        return root_python_exe, root_pip_exe

    # 2. Fallback to local workspace venv creation
    if workspace_dir is None:
        workspace_dir = WORKSPACE_DIR
        
    venv_dir = os.path.join(workspace_dir, "venv")
    
    # Determine executable path based on OS
    if os.name == 'nt': # Windows
        python_exe = os.path.join(venv_dir, "Scripts", "python.exe")
        pip_exe = os.path.join(venv_dir, "Scripts", "pip.exe")
    else: # Unix/Mac
        python_exe = os.path.join(venv_dir, "bin", "python")
        pip_exe = os.path.join(venv_dir, "bin", "pip")

    # Initialize venv if python executable is not present
    if not os.path.exists(python_exe):
        print(f"📦 Virtual environment not found or incomplete. Creating venv inside {workspace_dir}...")
        try:
            if os.path.exists(venv_dir):
                import shutil
                shutil.rmtree(venv_dir, ignore_errors=True)
            subprocess.run([sys.executable, "-m", "venv", venv_dir], check=True)
            print("✅ Virtual environment created successfully.")
        except Exception as e:
            print(f"❌ Failed to create virtual environment: {e}")
            return sys.executable, "pip" # Fallback to global python and pip

    return python_exe, pip_exe

def list_workspace_files(workspace_dir=None):
    """
    Lists all python, matlab, and config files in the workspace directory.
    """
    if workspace_dir is None:
        workspace_dir = WORKSPACE_DIR
        
    if not os.path.exists(workspace_dir):
        return []
    
    files = []
    for root, _, filenames in os.walk(workspace_dir):
        # Exclude virtual environment directory
        if "venv" in root.split(os.sep) or ".git" in root.split(os.sep):
            continue
        for filename in filenames:
            if filename.endswith(".py") or filename == "requirements.txt" or filename.endswith(".md") or filename.endswith(".m"):
                rel_path = os.path.relpath(os.path.join(root, filename), workspace_dir)
                files.append(rel_path)
    return files

def read_code_file(relative_path, workspace_dir=None):
    """
    Reads the content of a file in the workspace directory.
    """
    if workspace_dir is None:
        workspace_dir = WORKSPACE_DIR
        
    full_path = os.path.join(workspace_dir, relative_path)
    if not os.path.exists(full_path):
        return f"Error: File {relative_path} does not exist."
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {str(e)}"

def write_code_file(relative_path, content, workspace_dir=None):
    """
    Writes content to a file in the workspace directory (creates if doesn't exist).
    """
    if workspace_dir is None:
        workspace_dir = WORKSPACE_DIR
        
    full_path = os.path.join(workspace_dir, relative_path)
    # Ensure parent folders exist
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    try:
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Success: File {relative_path} written."
    except Exception as e:
        return f"Error writing file: {str(e)}"

def install_workspace_requirements(workspace_dir=None):
    """
    Checks if a requirements.txt file exists in the workspace and runs pip install inside the venv.
    """
    if workspace_dir is None:
        workspace_dir = WORKSPACE_DIR
        
    req_path = os.path.join(workspace_dir, "requirements.txt")
    if not os.path.exists(req_path):
        return "No requirements.txt found in workspace. Skipping dependency install."

    _, pip_exe = get_venv_path(workspace_dir)
    print("Installing dependencies from requirements.txt in virtual environment...")
    try:
        res = subprocess.run([pip_exe, "install", "-r", req_path], capture_output=True, encoding="utf-8", errors="replace", check=True)
        return f"Dependencies installed successfully:\n{res.stdout}"
    except subprocess.CalledProcessError as e:
        return f"Error installing dependencies:\nStdout: {e.stdout}\nStderr: {e.stderr}"

def execute_external_command_tool(command: str, workspace_dir: str = None, timeout: int = 15, task_id: str = None) -> str:
    """
    Executes a shell command inside the specified workspace directory.
    """
    if workspace_dir is None:
        workspace_dir = WORKSPACE_DIR
        
    from external_software import ExternalSoftwareAgent
    agent = ExternalSoftwareAgent()
    res = agent.execute_command(command, workspace_dir, timeout, task_id)
    return f"Exit Code: {res['exit_code']}\nStdout:\n{res['stdout']}\nStderr:\n{res['stderr']}"

def test_python_file(relative_path, workspace_dir=None, task_id=None):
    """
    Runs a python script in the workspace using the virtual environment python interpreter.
    Delegates to the generic command execution engine.
    """
    if workspace_dir is None:
        workspace_dir = WORKSPACE_DIR
        
    python_exe, _ = get_venv_path(workspace_dir)
    command = f'"{python_exe}" "{relative_path}"'
    
    from external_software import ExternalSoftwareAgent
    agent = ExternalSoftwareAgent()
    res = agent.execute_command(command, workspace_dir, timeout=15, task_id=task_id)
    
    status = "PASSED" if res["exit_code"] == 0 else "FAILED"
    if res["exit_code"] == -1:
        status = "TIMEOUT"
        
    return {
        "exit_code": res["exit_code"],
        "status": status,
        "stdout": res["stdout"],
        "stderr": res["stderr"]
    }
