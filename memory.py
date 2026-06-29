import json
import logging
import re
from pathlib import Path
from typing import Dict, Any, Optional
from settings import MEMORY_DIR, SANDBOX_DIR

logger = logging.getLogger("avionics_framework.memory")

class MemoryAgent:
    def __init__(self, session_id: str = "default"):
        self.session_id = session_id
        self.session_file = MEMORY_DIR / f"session_{session_id}.json"
        self.state: Dict[str, Any] = {
            "session_id": session_id,
            "description": "",
            "language": "python",
            "plan": None,
            "source_code": "",
            "test_code": "",
            "loop_count": 0,
            "logs": [],
            "status": "initialized"
        }
        self.load_session()

    def load_session(self) -> None:
        if self.session_file.exists():
            try:
                with open(self.session_file, "r") as f:
                    self.state = json.load(f)
                logger.info(f"Loaded memory session {self.session_id}")
            except Exception as e:
                logger.error(f"Failed to load session file: {e}")

    def save_session(self) -> None:
        try:
            with open(self.session_file, "w") as f:
                json.dump(self.state, f, indent=4)
            logger.debug(f"Saved memory session {self.session_id}")
        except Exception as e:
            logger.error(f"Failed to save session file: {e}")

    def update_state(self, key: str, value: Any) -> None:
        self.state[key] = value
        self.save_session()

    def log_event(self, agent_name: str, message: str) -> None:
        log_entry = {"agent": agent_name, "message": message}
        self.state["logs"].append(log_entry)
        logger.info(f"[{agent_name}] {message}")
        # NOTE: We do NOT call save_session() here to avoid hundreds of disk writes
        # per pipeline run. Logs persist whenever update_state() triggers a save.

    def write_source_file(self, filename: str, content: str) -> Path:
        filepath = SANDBOX_DIR / filename
        # Sanitize MATLAB files to prevent non-ASCII character errors
        if filename.endswith(".m"):
            content = self._sanitize_matlab_content(filename, content)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        self.log_event("MemoryAgent", f"Wrote source file: {filename}")
        return filepath

    def _sanitize_matlab_content(self, filename: str, content: str) -> str:
        """
        Sanitize MATLAB file content:
        1. Replace common Unicode characters with ASCII equivalents.
        2. Detect if the LLM accidentally generated Python instead of MATLAB
           and replace with a minimal stub that will force the debugger to regenerate.
        """
        # --- Step 1: Unicode character replacement ---
        replacements = {
            "\u2018": "'",  # left single quotation mark
            "\u2019": "'",  # right single quotation mark
            "\u201c": "'",  # left double quotation mark -> MATLAB uses single quotes
            "\u201d": "'",  # right double quotation mark
            "\u2013": "-",  # en-dash
            "\u2014": "-",  # em-dash
            "\u2026": "...",  # ellipsis
            "\u00b7": "*",  # middle dot
            "\u00d7": "*",  # multiplication sign
            "\u00e9": "e",  # e with accent
            "\u03b1": "alpha",  # greek alpha
            "\u03b2": "beta",   # greek beta
            "\u03c9": "omega",  # greek omega
        }
        for unicode_char, ascii_char in replacements.items():
            content = content.replace(unicode_char, ascii_char)

        # Strip any remaining non-ASCII characters
        content = content.encode("ascii", errors="ignore").decode("ascii")

        # --- Step 2: Detect Python-in-MATLAB mismatch ---
        # If the LLM generated Python syntax, replace with an error stub
        # so the execution fails with a clear message instead of a cryptic parse error.
        python_indicators = [
            r"^\s*def\s+[a-zA-Z_]",                     # Python function definition
            r"^\s*import\s+[a-zA-Z_]",                  # Python import
            r"^\s*from\s+[a-zA-Z_].*?\s*import\s+",      # Python from-import
            r"^\s*class\s+[a-zA-Z_]",                    # Python class definition
            r"^\s*#",                                    # Python-style comment
            r"^\s*(if|elif|else|for|while|try|except|with)\s+.*:\s*$", # Python control block ending in colon
            r"^\s*(else|try|except|finally):\s*$",        # Python keyword blocks ending in colon
        ]
        lines = content.splitlines()
        python_score = 0
        for line in lines[:20]:  # check first 20 lines only
            for pattern in python_indicators:
                if re.search(pattern, line):
                    python_score += 1
        
        if python_score >= 2:
            logger.warning(
                f"[MemoryAgent] MATLAB file '{filename}' appears to contain Python code "
                f"(score={python_score}). Replacing with error stub to force regeneration."
            )
            func_name = filename.replace(".m", "")
            content = (
                f"% AUTO-GENERATED STUB: LLM generated Python code instead of MATLAB.\n"
                f"% The agent will automatically detect this error and regenerate.\n"
                f"function result = {func_name}(varargin)\n"
                f"  error('STUB: LLM generated Python code instead of MATLAB. Regenerate this file.');\n"
                f"end\n"
            )
            self.log_event(
                "MemoryAgent",
                f"WARNING: Replaced Python-in-MATLAB content in {filename} with error stub."
            )

        return content

    def read_source_file(self, filename: str) -> Optional[str]:
        filepath = SANDBOX_DIR / filename
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                return f.read()
        return None

    def clear_sandbox(self) -> None:
        for item in SANDBOX_DIR.iterdir():
            if item.is_file():
                try:
                    item.unlink()
                except Exception as e:
                    logger.warning(f"Could not delete {item}: {e}")
        try:
            (SANDBOX_DIR / "__init__.py").touch()
        except Exception as e:
            logger.warning(f"Could not recreate __init__.py: {e}")
        self.log_event("MemoryAgent", "Cleared sandbox directory and recreated __init__.py")

    def load_lessons(self) -> list[dict]:
        from settings import ROOT_DIR
        lessons_file = ROOT_DIR / "lessons_learned.json"
        if lessons_file.exists():
            try:
                with open(lessons_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load lessons file: {e}")
        return []

    def save_lesson(self, lesson: dict) -> None:
        from settings import ROOT_DIR
        lessons_file = ROOT_DIR / "lessons_learned.json"
        lessons = self.load_lessons()
        
        # Avoid duplicate mistake entries
        for existing in lessons:
            if existing.get("mistake") == lesson.get("mistake"):
                return
                
        lessons.append(lesson)
        try:
            with open(lessons_file, "w", encoding="utf-8") as f:
                json.dump(lessons, f, indent=4)
            logger.info(f"Saved lesson learned to lessons_learned.json: {lesson.get('mistake')}")
        except Exception as e:
            logger.error(f"Failed to save lesson file: {e}")
