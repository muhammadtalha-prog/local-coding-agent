"""
config.py — Central configuration for the MATLAB Code Generation Agent.
Optimized for 8GB RAM systems with 1.5-3B local LLMs via Ollama.
"""
import os
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Directory Paths
# ---------------------------------------------------------------------------
ROOT_DIR     = Path(__file__).parent.resolve()
SANDBOX_DIR  = ROOT_DIR / "sandbox"
WORKSPACE_DIR = ROOT_DIR / "workspace"

for _d in (SANDBOX_DIR, WORKSPACE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Load .env
# ---------------------------------------------------------------------------
_env_path = ROOT_DIR / ".env"
if _env_path.exists():
    with open(_env_path, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip("\"'"))

# ---------------------------------------------------------------------------
# Ollama / LLM Settings
# ---------------------------------------------------------------------------
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1").rstrip("/")
OLLAMA_MODEL:    str = os.getenv("OLLAMA_MODEL",    "qwen2.5-coder:3b")

# Max tokens to generate — keep short so the 3B model stays coherent
LLM_MAX_TOKENS: int  = int(os.getenv("LLM_MAX_TOKENS", "2048"))
LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.15"))

# Per-call LLM timeout in seconds (90s is generous for a 3B model)
LLM_TIMEOUT_SEC: float = float(os.getenv("LLM_TIMEOUT_SEC", "120.0"))

# ---------------------------------------------------------------------------
# Pipeline Controls
# ---------------------------------------------------------------------------
# How many times the Debugger may attempt to fix failing code
MAX_DEBUG_RETRIES: int = int(os.getenv("MAX_DEBUG_RETRIES", "3"))

# Timeout for each MATLAB subprocess execution (seconds)
MATLAB_EXEC_TIMEOUT_SEC: float = float(os.getenv("MATLAB_EXEC_TIMEOUT_SEC", "120.0"))

# Disable JVM to speed up MATLAB startup and save RAM (highly recommended on 8GB CPU systems)
MATLAB_NO_JVM: bool = os.getenv("MATLAB_NO_JVM", "true").lower() in ("true", "1", "yes")

# AutoApprove — skip human confirmation before saving to workspace
AUTO_APPROVE: bool = os.getenv("AUTO_APPROVE", "true").lower() in ("true", "1", "yes")

# ---------------------------------------------------------------------------
# MATLAB Executable Discovery
# ---------------------------------------------------------------------------
def find_matlab() -> str | None:
    """
    Returns the full path to the matlab executable, or None if not found.
    Search order:
      1. MATLAB_PATH env var
      2. Known user install paths on D: / C:
      3. Program Files scan (all drives, all R20XX versions)
      4. PATH (shutil.which)
    Result is cached after first call.
    """
    if hasattr(find_matlab, "_cache"):
        return find_matlab._cache  # type: ignore[attr-defined]

    def _exists(p: Path) -> bool:
        return p.is_file()

    # 1. Env var override
    env_override = os.getenv("MATLAB_PATH", "").strip()
    if env_override and _exists(Path(env_override)):
        find_matlab._cache = env_override
        return env_override

    # 2. Known common paths
    for kp in [
        Path("D:/Matlab/install/bin/matlab.exe"),
        Path("D:/Matlab/bin/matlab.exe"),
        Path("D:/MATLAB/bin/matlab.exe"),
        Path("C:/Program Files/MATLAB/R2024b/bin/matlab.exe"),
        Path("C:/Program Files/MATLAB/R2024a/bin/matlab.exe"),
        Path("C:/Program Files/MATLAB/R2023b/bin/matlab.exe"),
    ]:
        if _exists(kp):
            find_matlab._cache = str(kp)
            return find_matlab._cache

    # 3. Program Files scan
    if sys.platform.startswith("win"):
        import re as _re
        ver_re = _re.compile(r"^R?20\d{2}[ab]?$", _re.IGNORECASE)
        for drive in ("C:", "D:", "E:", "F:"):
            base = Path(f"{drive}/Program Files/MATLAB")
            if not base.exists():
                continue
            versions = sorted(
                [v for v in base.iterdir() if v.is_dir() and ver_re.match(v.name)],
                key=lambda v: v.name,
                reverse=True,
            )
            for ver in versions:
                for arch in ("bin", "bin/win64"):
                    cand = ver / arch / "matlab.exe"
                    if _exists(cand):
                        find_matlab._cache = str(cand)
                        return find_matlab._cache

    # 4. PATH fallback
    result = shutil.which("matlab")
    find_matlab._cache = result  # may be None
    return result


MATLAB_EXE: str | None = find_matlab()

# ---------------------------------------------------------------------------
# AutoGen / AG2 LLM Config  (Ollama via OpenAI-compatible API)
# ---------------------------------------------------------------------------
# This config is used by all AutoGen AssistantAgents (Planner, Coder, Debugger).
# Agents run sequentially — never in parallel — to keep RAM under control.
LLM_CONFIG: dict = {
    "config_list": [
        {
            "model": OLLAMA_MODEL,
            "base_url": OLLAMA_BASE_URL,
            "api_key": "ollama",      # Ollama ignores this but AutoGen requires it
            "price": [0, 0],          # Suppress AutoGen's 'model cost unknown' warning
        }
    ],
    "cache_seed": None,               # Disable response caching (always fresh LLM calls)
    "temperature": LLM_TEMPERATURE,
    "max_tokens": LLM_MAX_TOKENS,
}

# ---------------------------------------------------------------------------
# System Prompts  (kept short — every token counts with a 3B model)
# ---------------------------------------------------------------------------

PLANNER_SYSTEM_PROMPT = """\
You are a MATLAB planning agent. Your ONLY job is to produce a JSON plan for a MATLAB function.

Rules:
- Output ONLY valid JSON. No markdown, no explanation outside the JSON.
- Target language is always MATLAB.
- Never plan toolbox functions (no butter, filtfilt, freqz, tf, lsim, ss, bode, step, nyquist).
  Implement all DSP/control algorithms manually using base MATLAB matrix operations.
- Design a single-function MATLAB file. The main function name MUST match the file_name field.

JSON schema (all fields required):
{
  "file_name": "<snake_case name WITHOUT .m extension>",
  "description": "<one sentence>",
  "inputs": [{"name": "...", "type": "...", "description": "..."}],
  "outputs": [{"name": "...", "type": "...", "description": "..."}],
  "components": [{"name": "...", "description": "...", "logic": "..."}],
  "test_call": "<valid MATLAB expression to call and display the result, e.g. disp(my_func(1,2))>"
}
"""

CODER_SYSTEM_PROMPT = """\
You are a MATLAB code generation agent. Translate the JSON plan into a clean MATLAB function file.

Rules:
- Output ONLY the raw MATLAB code. No markdown fences, no English explanation.
- The FIRST LINE must be: function <outputs> = <file_name>(<inputs>)
- The function name MUST exactly match the file_name from the plan.
- Use ONLY base MATLAB — no licensed toolboxes (no butter, filtfilt, freqz, tf, lsim, ss, bode, step, nyquist).
- NEVER use the input() function to interactively prompt for inputs. Handle missing/invalid arguments using validation or default values.
- Comments use % prefix. No Python syntax (no def, import, class, #, colons at line ends).
- All variable names and comments in plain ASCII — no Greek letters or math symbols.
- Add input validation using error() for invalid inputs.
- End the file with a single 'end' keyword.
"""

DEBUGGER_SYSTEM_PROMPT = """\
You are a MATLAB debugging agent. Fix the provided MATLAB code based on the error output.

Rules:
- Output ONLY the corrected raw MATLAB code. No markdown, no explanation.
- The FIRST LINE must be: function <outputs> = <function_name>(<inputs>)
- Fix ALL errors shown. Do not introduce new bugs.
- NEVER use the input() function to interactively prompt for inputs.
- Never use licensed toolboxes (no butter, filtfilt, freqz, tf, lsim, ss).
- If the error mentions a toolbox function, replace it with a manual implementation.
- Keep comments in plain ASCII only.
- If the error cannot be fixed without a toolbox, output exactly: TOOLBOX_ERROR_UNFIXABLE
"""
