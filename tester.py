import re
from llm import async_query_llm
from memory import MemoryAgent
from settings import TESTER_PROMPT, get_agent_filenames, TESTER_MODEL

class TesterAgent:
    """Specialized for test generation only - does NOT handle debugging."""
    
    def __init__(self, memory_agent: MemoryAgent):
        self.memory = memory_agent

    def _is_script(self, source_code: str) -> bool:
        """
        Heuristically decide if the implementation is a script (no function definition)
        or a function file (starts with 'function ...').
        """
        lines = source_code.strip().splitlines()
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('function'):
                return False
        return True   # No function found → treat as script

    async def generate_tests(self, description: str, plan_json: dict, source_code: str) -> str:
        self.memory.log_event("TesterAgent", "Starting test suite generation.")
        
        source_filename, test_filename = get_agent_filenames(plan_json)
        source_filename_raw = source_filename.replace(".m", "").replace(".py", "")
        language = str(plan_json.get("language", "python")).lower()
        
        script_instruction = ""
        if language == "matlab":
            if self._is_script(source_code):
                script_instruction = (
                    f"CRITICAL MATLAB TEST RULE:\n"
                    f"The implementation in '{source_filename}' is structured as a MATLAB SCRIPT, not a function. "
                    f"You MUST write test cases that execute the script using the command `run('{source_filename}')` "
                    f"or simply by typing its name `{source_filename_raw}` as a script. "
                    f"Do NOT attempt to call `{source_filename_raw}` with input/output arguments (like `out = {source_filename_raw}(in)`) as that will raise a MATLAB error.\n"
                    f"Verify the results by checking the variables created in the workspace after running the script."
                )
            else:
                script_instruction = (
                    f"CRITICAL MATLAB TEST RULE:\n"
                    f"The implementation in '{source_filename}' is structured as a MATLAB FUNCTION. "
                    f"You MUST write test cases that call the function `{source_filename_raw}` with the appropriate arguments, "
                    f"capture its return value(s), and assert correctness."
                )
        
        prompt = (
            f"Requirements:\n{description}\n\n"
            f"Plan:\n{plan_json}\n\n"
            f"Source Code (import as sandbox.{source_filename_raw}):\n{source_code}\n\n"
            f"Target Test Filename:\n{test_filename}\n\n"
        )
        if script_instruction:
            prompt += f"{script_instruction}\n\n"
        prompt += "Generate the comprehensive test suite."
        
        try:
            raw_response = await async_query_llm(prompt, system_instruction=TESTER_PROMPT, model_name=TESTER_MODEL)
            test_code = self._extract_code(raw_response)
            
            self.memory.update_state("test_code", test_code)
            self.memory.write_source_file(test_filename, test_code)
            
            self.memory.log_event("TesterAgent", f"Test suite generated and saved to sandbox/{test_filename}")
            return test_code
        except Exception as e:
            self.memory.log_event("TesterAgent", f"Test generation failed: {str(e)}")
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
