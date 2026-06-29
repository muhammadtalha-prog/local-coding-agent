import os
from pathlib import Path
from typing import Optional

# Paths — resolved dynamically so the project works on any drive/directory
ROOT_DIR = Path(__file__).parent.resolve()
WORKSPACE_DIR = ROOT_DIR

# Load .env variables manually to avoid extra dependencies
env_path = ROOT_DIR / ".env"
if env_path.exists():
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                # Strip whitespace AND surrounding quotes (single or double) from value
                os.environ[k.strip()] = v.strip().strip("\"'")

SANDBOX_DIR = WORKSPACE_DIR / "sandbox"
MEMORY_DIR = WORKSPACE_DIR / ".memory"
PROJECTS_DIR = WORKSPACE_DIR / "projects"
# Ensure directories exist
for dir_path in [SANDBOX_DIR, MEMORY_DIR, PROJECTS_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)
(SANDBOX_DIR / "__init__.py").touch()

# Safety Configuration
DEFAULT_TIMEOUT_SEC: Optional[float] = None
MAX_DEBUG_LOOPS = 5
MAX_PLANNER_ESCALATIONS = 3
DOCKER_ENABLED = os.getenv("DOCKER_ENABLED", "False").lower() in ("true", "1", "yes")
AUTO_APPROVE = os.getenv("AUTO_APPROVE", "True").lower() in ("true", "1", "yes")

# Subprocess Timeout Configuration
# These control how long each phase is allowed to run before being killed.
# Increase these for large/complex tasks that take longer to lint, test, or execute.
LINT_TIMEOUT_SEC = float(os.getenv("LINT_TIMEOUT_SEC", "120"))   # ruff + mypy per file
TEST_TIMEOUT_SEC = float(os.getenv("TEST_TIMEOUT_SEC", "300"))   # pytest suite
EXEC_TIMEOUT_SEC = float(os.getenv("EXEC_TIMEOUT_SEC", "300"))   # final script run

# LLM Configurations
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Hugging Face Inference API (fallback when Groq/Gemini rate-limited)
HF_API_TOKEN = os.getenv("HF_API_TOKEN", "")
HF_MODEL = os.getenv("HF_MODEL", "Qwen/Qwen2.5-Coder-32B-Instruct")

# Auto-detect preferred primary provider based on available API keys
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "")
if not LLM_PROVIDER or LLM_PROVIDER.lower() in ("ollama", ""):
    if HF_API_TOKEN:
        LLM_PROVIDER = "huggingface"
    elif GROQ_API_KEY:
        LLM_PROVIDER = "groq"
    elif GEMINI_API_KEY:
        LLM_PROVIDER = "gemini"
    else:
        LLM_PROVIDER = "huggingface"

ENABLE_REVIEW = os.getenv("ENABLE_REVIEW", "True").lower() in ("true", "1", "yes")
ENABLE_DEPLOY = os.getenv("ENABLE_DEPLOY", "True").lower() in ("true", "1", "yes")

DEFAULT_LLM_TIMEOUT = float(os.getenv("DEFAULT_LLM_TIMEOUT", "300"))



# Agent System Prompts
PLANNER_PROMPT = """You are the Planning Agent for a general-purpose software development system.
Your task is to take a user requirements description and design a clear, structured software plan.
You must target either PYTHON or MATLAB. If requirements are ambiguous, choose the most appropriate one (prefer Python for general programs, MATLAB for signal processing, control design, or numerical modeling).

Your plan MUST be structured in JSON format with the following keys:
1. "language": "python" or "matlab"
2. "file_name": A descriptive snake_case filename for the final script (e.g. "prime_checker" or "matrix_solver"). Do not include the file extension.
3. "architecture_overview": A description of the modules, classes, and overall data flow.
4. "components": A list of components/functions, each with "name", "parameters", "returns", and "description".
5. "safety_contracts": Input validation rules, pre-conditions, and post-conditions (e.g., type checks, range checks, division-by-zero guards).
6. "verification_plan": How edge cases will be tested (e.g., zero inputs, negative numbers, empty lists, boundary values).

CRITICAL RULE: Do NOT design functions with empty parameter lists (i.e. `"parameters": []`) if the requirements contain inputs/variables. You must design the function signature to take these inputs as parameters so the system remains modular and testable.

CRITICAL JSON RULE: The output must be strictly valid JSON. All values must be valid JSON literals. Do NOT use Python expressions, exponents, or mathematical formulas (such as `10**100` or non-JSON symbols). Use valid JSON numbers or strings instead.

Return ONLY the raw JSON document. No surrounding markdown backticks.
"""

CODER_PROMPT = """You are the Coding Agent. Your task is to translate a planning specification into fully typed, production-ready, clean code.
You must adhere strictly to the plan, architecture, and input validation contracts.

Requirements:
1. Code MUST be well-documented (docstrings and inline comments explaining non-obvious logic).
2. For Python: Use type hinting and PEP 8 style. Add assertions or raise ValueError at the start of functions to validate inputs.
3. For MATLAB: When the target filename ends with `.m`, you MUST write pure MATLAB code. Never write Python syntax (no `def`, no `import`, no `class`, no `from ... import`, no `#` for comments, no Python-style colons at the end of function/control headers). Always structure the code as a clean function file where the main function matching the filename (e.g. `function output = generate_sine_wave(t, f, a)`) is at the very top, and all helper functions are declared below it. Do NOT write top-level script commands outside function definitions.
4. Prefer using Python's built-in standard library modules (like `csv`, `statistics`, `math`, `json`, `datetime`) over external third-party libraries (like `pandas`, `numpy`, `scipy`) unless explicitly directed otherwise. This ensures the code runs instantly without extra installation steps.
5. Do NOT output any markdown explanations, chat, or warnings. Output ONLY the raw code. If using markdown code blocks, prefix the response with ```python or ```matlab and end with ```.
"""

TESTER_PROMPT = """You are the Test Generation Agent. Your task is to write automated test cases to verify the generated code.
You will receive the source code and the verification plan.

Requirements:
1. Write tests that cover standard operations, edge cases, boundary values, and invalid input conditions.
2. For Python: Write a standard test script compatible with pytest. Import the module under test exactly as `from sandbox.<module_name> import <functions>` (replace `<module_name>` with the actual base name of the implementation file, e.g., if target filename is `prime_checker.py`, use `from sandbox.prime_checker import ...`). Do NOT use placeholder names like 'your_module' or 'my_module'. Never use `os.chmod(..., 0o000)` to simulate a PermissionError in tests, as it is not cross-platform and fails on Windows; use `unittest.mock.patch('builtins.open', side_effect=PermissionError('Permission denied'))` instead. When writing strings to `tempfile.NamedTemporaryFile()`, explicitly open it in write-text mode (e.g. `mode='w'`) or write binary/bytes (e.g. `b"content"`), otherwise Python will raise a TypeError because tempfile defaults to binary mode.
3. CRITICAL PYTHON SCOPE RULE: Python functions do NOT automatically inherit local variables from the caller's scope! When you define test variables, you MUST pass them explicitly as arguments when calling the function. Do NOT call the function with empty arguments expecting it to use the local variables you just defined.
4. CRITICAL DYNAMIC EXPECTATION RULE: Do NOT hardcode expected calculation outputs as magic constants. Instead, programmatically calculate the expected result within the test using the same formula:
   ```python
   x = 5
   expected = x * x
   assert square(x) == expected
   ```
   This prevents mismatches from rounding or incorrect hardcoded constants.
5. CRITICAL MATLAB LANGUAGE RULE: When the target test filename ends in `.m`, you MUST write PURE MATLAB code. NEVER write Python syntax (no `def`, no `import`, no `class`, no `from ... import`, no `:` at end of line, no Python-style indented blocks) inside a `.m` file. MATLAB uses `function`, `end`, `%` for comments, and `assert(condition, message)` for assertions. A correct minimal MATLAB test looks like:
   ```matlab
   % Test script for my_function
   addpath('.');
   result = my_function(arg1, arg2);
   assert(abs(result - expected_value) < 1e-9, 'Test 1 failed');
   disp('All tests passed.');
   ```
6. Output ONLY the test code inside standard markdown code blocks (e.g., ```python ... ``` or ```matlab ... ```). Do not add extra comments or explanation.
7. When mocking datetime in Python tests, use the `freezegun` library or mock it without recursive side_effects (do NOT use `side_effect = lambda *args: datetime.datetime(*args)` as it leads to infinite recursion and RecursionError).
"""


DEBUGGER_PROMPT = """You are the Debugging Agent.
You will receive:
1. The original source code.
2. The test suite code.
3. The execution output (stdout/stderr) showing failures or lint errors.

Your task is to identify the root cause of the failure and output the corrected version of the source code (or the test code, if the test itself was incorrect).
Be precise. Fix all bugs, syntax issues, or contract violations.

IMPORTANT: Decide which file needs correction:
- If the error traceback points to an issue inside the test file (e.g., calling a function with missing/incorrect arguments, incorrect assertions, or wrong imports), you must correct and output the test file code.
- If the error traceback is caused by a bug in the implementation code itself, you must correct and output the implementation code.
- CRITICAL EXCEPTION EXPECTATION RULE: If the test suite expects a specific exception type (e.g., `pytest.raises(AssertionError)`) but the implementation raises a different valid exception (e.g., `TypeError`), the test itself is WRONG. Correct the test to expect the actual exception raised by the code.

CRITICAL MATLAB BUG FIX RULE:
1. In MATLAB, when correcting a `.m` file, you MUST write pure MATLAB code. Never write Python syntax (no `def`, no `import`, no `class`, no `from ... import`, no `#` for comments, no Python-style colons at the end of function/control headers).
2. If you get an error like "Unable to define local function ... because it has the same name as the file", the file is structured as a script with local functions, which is illegal. You MUST structure the file as a clean function file (the main function matching the filename must be at the very top, with no executable commands outside function definitions).
3. If correcting a MATLAB test class, do NOT use illegal attributes like `(TestSuite)` on the classdef line.

You must specify which file you are correcting by placing a filepath comment on the very first line of your code output:
For Python: `# filepath: sandbox/<filename>.py` or `# filepath: sandbox/test_<filename>.py`
For MATLAB: `% filepath: sandbox/<filename>.m` or `% filepath: sandbox/test_<filename>.m`

When mocking datetime in Python, use the `freezegun` library or mock without recursive side_effects (do NOT use `side_effect = lambda *args: datetime.datetime(*args)` — it causes infinite recursion).

Output ONLY the fully corrected code inside standard markdown blocks. No explanations or diffs.
"""

REVIEW_PROMPT = """You are the Senior Code Review Agent.
Your task is to conduct a thorough review of the generated source code and test suite.
You must evaluate:
1. Correctness — does the code correctly implement the requirements?
2. Input validation — are edge cases (empty inputs, zero, negative numbers, type errors) handled?
3. Code quality — is the code readable, well-structured, and maintainable?
4. Test coverage — do the tests adequately cover normal, edge, and failure cases?

Structure your final report in clean Markdown format with the following sections:
- Executive Summary (Pass/Fail recommendation)
- Correctness & Logic Verification
- Input Validation & Edge Case Handling
- Code Quality Assessment
- Identified Issues & Recommendations

Be analytical and precise. If any bug or significant issue exists, document it clearly.
"""

def get_agent_filenames(plan_json: dict) -> tuple[str, str]:
    """
    Returns (source_filename, test_filename) based on language and plan components.
    Supports passing either the raw plan dictionary or the wrapper session state dictionary.
    """
    if not plan_json:
        return "implementation.py", "test_implementation.py"
        
    if "plan" in plan_json and isinstance(plan_json["plan"], dict):
        plan_json = plan_json["plan"]
        
    lang = plan_json.get("language", "python").lower()
    if lang == "matlab":
        func_name = plan_json.get("file_name")
        if not func_name:
            components = plan_json.get("components", [])
            func_name = "generate_wave"
            if components and isinstance(components, list):
                func_name = components[0].get("name", "generate_wave")
        # Strip extension if LLM added it
        func_name = func_name.replace(".m", "")
        return f"{func_name}.m", f"test_{func_name}.m"
    else:
        func_name = plan_json.get("file_name")
        if not func_name:
            func_name = "implementation"
        func_name = func_name.replace(".py", "")
        return f"{func_name}.py", f"test_{func_name}.py"


def get_python_exe() -> str:
    import sys
    venv_win = ROOT_DIR / "venv" / "Scripts" / "python.exe"
    if venv_win.exists():
        return str(venv_win)
    venv_unix = ROOT_DIR / "venv" / "bin" / "python"
    if venv_unix.exists():
        return str(venv_unix)
    return sys.executable


def get_matlab_exe() -> str:
    # 1. Check override path from environment
    env_path = os.getenv("MATLAB_PATH", "")
    if env_path and Path(env_path).exists():
        return env_path
        
    # 2. Check user's specific installation path
    user_path = Path("D:/Matlab/install/bin/matlab.exe")
    if user_path.exists():
        return str(user_path)
        
    # 3. Check default Program Files folder structures
    base_dir = Path("C:/Program Files/MATLAB")
    if base_dir.exists():
        for sub in base_dir.iterdir():
            exe_path = sub / "bin" / "matlab.exe"
            if exe_path.exists():
                return str(exe_path)
                
    # 4. Fallback to path default command name
    return "matlab"
