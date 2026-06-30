import re
from llm import async_query_llm
from memory import MemoryAgent
from settings import DEBUGGER_PROMPT, get_agent_filenames, DEBUGGER_MODEL

class DebuggerAgent:
    """Specialized for debugging and fixing code."""
    
    def __init__(self, memory_agent: MemoryAgent):
        self.memory = memory_agent

    async def debug_code(self, source_code: str, test_code: str, execution_log: str) -> tuple[str, str]:
        """
        Debug the code and return (fixed_source, fixed_test).
        Presents both source and test files to the LLM and parses its response
        to determine which file was corrected.
        """
        self.memory.log_event("DebuggerAgent", f"Initiating debugging cycle. Loop: {self.memory.state.get('loop_count', 0) + 1}")
        
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
            f"Specify which file you are correcting by placing a filepath comment on the very first line of your code output."
        )
        
        try:
            raw_response = await async_query_llm(prompt, system_instruction=DEBUGGER_PROMPT, model_name=DEBUGGER_MODEL)
            fixed_code = self._extract_code(raw_response)
            
            loop_count = self.memory.state.get("loop_count", 0) + 1
            self.memory.update_state("loop_count", loop_count)
            
            # Determine which file was fixed by scanning the entire response for a filepath line
            target_is_test = False
            filepath_line = ""
            if fixed_code:
                for line in fixed_code.splitlines():
                    if "filepath:" in line:
                        filepath_line = line.strip()
                        break
            
            if filepath_line:
                path_str = filepath_line.split("filepath:", 1)[1].strip()
                filename = path_str.split()[0] if path_str else ""
                import os
                if os.path.basename(filename).startswith("test_"):
                    target_is_test = True
                self.memory.log_event("DebuggerAgent", f"Detected file override target in LLM response: {filepath_line}")
            else:
                # Fallback heuristic: if the fixed code closely resembles the test file's
                # imports/assert structure rather than the source's function defs, guess test.
                test_signal = fixed_code.count("assert") + fixed_code.count("def test_") + fixed_code.count("import pytest")
                source_signal = fixed_code.count("def ") - fixed_code.count("def test_")
                target_is_test = test_signal > source_signal and test_signal > 0
                self.memory.log_event("DebuggerAgent", f"No filepath marker found; heuristic guessed {'test' if target_is_test else 'source'} file (test_signal={test_signal}, source_signal={source_signal}).")
            
            if target_is_test:
                self.memory.update_state("test_code", fixed_code)
                self.memory.write_source_file(test_filename, fixed_code)
                self.memory.log_event("DebuggerAgent", f"Debugging iteration {loop_count} completed. Updated test suite saved.")
                return source_code, fixed_code
            else:
                self.memory.update_state("source_code", fixed_code)
                self.memory.write_source_file(source_filename, fixed_code)
                self.memory.log_event("DebuggerAgent", f"Debugging iteration {loop_count} completed. Updated source code saved.")
                return fixed_code, test_code
                
        except Exception as e:
            self.memory.log_event("DebuggerAgent", f"Debugging failed: {str(e)}")
            raise e

    def _extract_code(self, response: str) -> str:
        if not response:
            return ""
        clean = response.strip()
        pattern = r"```[a-zA-Z]*\s*\r?\n(.*?)\s*```"
        match = re.search(pattern, clean, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return clean
