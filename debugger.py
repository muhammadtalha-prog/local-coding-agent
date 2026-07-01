import re
import os
import difflib
import subprocess
from llm import async_query_llm
from memory import MemoryAgent
from settings import DEBUGGER_PROMPT, get_agent_filenames, DEBUGGER_MODEL, ROOT_DIR

class DebuggerAgent:
    """Specialized for debugging and fixing code."""
    
    def __init__(self, memory_agent: MemoryAgent):
        self.memory = memory_agent

    def _classify_code_block(self, code: str, source_code: str, test_code: str) -> str:
        """
        Classifies a code block as 'test', 'source', or 'unknown' based on:
        1. Explicit filepath comment header.
        2. Known test frameworks or pytest patterns.
        3. SequenceMatcher structural similarity ratios.
        4. Def/Assert symbol counts.
        """
        # 1. Search first 5 lines for filepath comments
        for line in code.splitlines()[:5]:
            if "filepath:" in line:
                path_str = line.split("filepath:", 1)[1].strip()
                filename = path_str.split()[0] if path_str else ""
                if os.path.basename(filename).startswith("test_") or "_test" in filename:
                    return "test"
                else:
                    return "source"

        # 2. Check for python test library imports/framework markers
        test_markers = ["import pytest", "pytest.raises", "def test_", "@pytest.", "unittest"]
        if any(marker in code for marker in test_markers):
            return "test"

        # 3. Match using sequence similarity ratio
        ratio_source = difflib.SequenceMatcher(None, code, source_code).ratio() if source_code else 0.0
        ratio_test = difflib.SequenceMatcher(None, code, test_code).ratio() if test_code else 0.0
        
        if ratio_source > 0.1 or ratio_test > 0.1:
            if ratio_test > ratio_source:
                return "test"
            else:
                return "source"

        # 4. Symbol count signatures
        test_signal = code.count("assert ") + code.count("def test_")
        source_signal = code.count("def ") - code.count("def test_")
        if test_signal > source_signal:
            return "test"
        elif source_signal > test_signal:
            return "source"

        return "unknown"

    def _validate_and_repair_matlab_code(self, code: str, filename: str) -> str:
        """Attempt to repair common MATLAB syntax errors."""
        if not filename.endswith(".m"):
            return code
            
        import re
        # 1. Detect if it's Python code (score based on heuristics)
        python_score = 0
        lines = code.splitlines()
        for line in lines[:20]:
            if re.search(r'^\s*def\s+', line): python_score += 1
            if re.search(r'^\s*import\s+', line): python_score += 1
            if re.search(r'^\s*class\s+', line): python_score += 1
            if re.search(r'^\s*#', line): python_score += 1
            if re.search(r':\s*$', line) and not re.search(r'%', line): python_score += 1  # colon at end
            
        if python_score >= 2:
            self.memory.log_event("DebuggerAgent", "Python syntax leaks detected in MATLAB code; attempting auto-repair.")
            # Attempt conversion: replace def with function, remove colons, replace # with %
            converted = code
            converted = re.sub(r'^\s*def\s+(\w+)\s*\(([^)]*)\)\s*:', r'function \1(\2)', converted, flags=re.MULTILINE)
            converted = re.sub(r'^\s*import\s+(\w+)', r'% import \1', converted, flags=re.MULTILINE)
            converted = re.sub(r'^\s*#', r'%', converted, flags=re.MULTILINE)
            # If the file doesn't start with 'function', it's probably a script – convert to script mode
            if not converted.strip().startswith('function'):
                self.memory.update_state("use_script_mode", True)
                return converted
            return converted
        return code

    async def debug_code(self, source_code: str, test_code: str, execution_log: str) -> tuple[str, str]:
        """
        Debug the code and return (fixed_source, fixed_test).
        Presents both source and test files to the LLM, extracts corrected blocks,
        classifies them, and commits fixes to git.
        """
        loop_count = self.memory.state.get("loop_count", 0) + 1
        self.memory.log_event("DebuggerAgent", f"Initiating debugging cycle. Loop: {loop_count}")
        
        source_filename, test_filename = get_agent_filenames(self.memory.state)
        filename_raw = source_filename.replace(".m", "").replace(".py", "")
        
        prompt = (
            f"--- SOURCE FILENAME ---\n{source_filename}\n\n"
            f"--- SOURCE CODE ---\n{source_code}\n\n"
            f"--- TEST SUITE CODE ---\n{test_code}\n\n"
            f"--- EXECUTION LOG / ERROR REPORT ---\n{execution_log}\n\n"
            f"Identify the bugs and generate the fully corrected code. "
            f"If modifying a MATLAB implementation file, the main function defined at the very top MUST be named exactly '{filename_raw}'. "
            f"Do NOT define any other local function with the name '{filename_raw}' inside this file.\n\n"
            f"Specify which file you are correcting by placing a filepath comment on the very first line of your code output:\n"
            f"For Python: `# filepath: sandbox/{source_filename}` or `# filepath: sandbox/{test_filename}`\n"
            f"For MATLAB: `% filepath: sandbox/{source_filename}` or `% filepath: sandbox/{test_filename}`"
        )
        
        # Analyze error logs for structural patterns and inject hints
        structural_fixes = []
        if "Local function name must be different from the script name" in execution_log:
            structural_fixes.append(
                "The error is because you have a local function with the same name as the main file. "
                "Rename the local function (e.g., append '_helper') or convert the whole file to a script if no main function is required."
            )
        if "Arrays have incompatible sizes" in execution_log:
            structural_fixes.append(
                "Check that the signal vector and the window size are compatible. Use `conv` or ensure the signal length is at least the window size."
            )
        if "STUB: LLM generated Python code instead of MATLAB" in execution_log:
            structural_fixes.append(
                "You must generate pure MATLAB code. Do not use Python syntax. The main function must be at the top and named exactly as the file."
            )
        if "Execution of script" in execution_log and "as a function is not supported" in execution_log:
            structural_fixes.append(
                "The test file is attempting to execute a MATLAB script as a function. "
                "If the implementation file is a script, modify the test to run it using the `run` command or by name without parameters. "
                "If the implementation file should be a function, correct the implementation file to include a function definition header at the top."
            )

        if structural_fixes:
            prompt += "\n\n--- STRUCTURAL REPAIR INSTRUCTIONS ---\n" + "\n".join(structural_fixes) + "\n"
        
        try:
            raw_response = await async_query_llm(prompt, system_instruction=DEBUGGER_PROMPT, model_name=DEBUGGER_MODEL)
            
            # Find all markdown code blocks in the response
            blocks = re.findall(r"```[a-zA-Z]*\s*\r?\n(.*?)\s*```", raw_response, re.DOTALL | re.IGNORECASE)
            
            # Fallback if no code blocks are found
            if not blocks:
                blocks = [raw_response.strip()]
                
            updated_source = source_code
            updated_test = test_code
            has_source_update = False
            has_test_update = False
            
            for block in blocks:
                block_clean = block.strip()
                if not block_clean:
                    continue
                    
                classification = self._classify_code_block(block_clean, source_code, test_code)
                if classification == "test":
                    if test_filename.endswith(".m"):
                        block_clean = self._validate_and_repair_matlab_code(block_clean, test_filename)
                    updated_test = block_clean
                    has_test_update = True
                elif classification == "source":
                    if source_filename.endswith(".m"):
                        block_clean = self._validate_and_repair_matlab_code(block_clean, source_filename)
                    updated_source = block_clean
                    has_source_update = True
                else:
                    # Default fallback: update source if nothing else matches
                    if source_filename.endswith(".m"):
                        block_clean = self._validate_and_repair_matlab_code(block_clean, source_filename)
                    updated_source = block_clean
                    has_source_update = True
            
            self.memory.update_state("loop_count", loop_count)
            
            # Commit and save source if updated
            if has_source_update:
                self.memory.update_state("source_code", updated_source)
                self.memory.write_source_file(source_filename, updated_source)
                self._commit_patch(source_filename, loop_count)
                self.memory.log_event("DebuggerAgent", f"Debugging iteration {loop_count} completed. Updated source code saved.")
                
            # Commit and save test if updated
            if has_test_update:
                self.memory.update_state("test_code", updated_test)
                self.memory.write_source_file(test_filename, updated_test)
                self._commit_patch(test_filename, loop_count)
                self.memory.log_event("DebuggerAgent", f"Debugging iteration {loop_count} completed. Updated test suite saved.")
                
            return updated_source, updated_test
            
        except Exception as e:
            self.memory.log_event("DebuggerAgent", f"Debugging failed: {str(e)}")
            raise e

    def _commit_patch(self, filename: str, loop_count: int) -> None:
        """Safely commit changes to git for tracking."""
        from settings import DEBUGGER_GIT_COMMIT
        if not DEBUGGER_GIT_COMMIT:
            return
            
        try:
            # Force add since sandbox/ is git-ignored
            subprocess.run(["git", "add", "-f", f"sandbox/{filename}"], cwd=str(ROOT_DIR), check=True, capture_output=True)
            commit_msg = f"[Debugger] Fix file sandbox/{filename} in loop {loop_count}"
            subprocess.run(["git", "commit", "-m", commit_msg], cwd=str(ROOT_DIR), check=True, capture_output=True)
            self.memory.log_event("DebuggerAgent", f"Committed debugger patch to git for sandbox/{filename}")
        except Exception as git_err:
            self.memory.log_event("DebuggerAgent", f"Failed to commit debugger patch: {git_err}")
