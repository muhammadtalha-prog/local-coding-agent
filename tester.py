import re
from llm import async_query_llm
from memory import MemoryAgent
from settings import TESTER_PROMPT, get_agent_filenames, TESTER_MODEL

class TesterAgent:
    """Specialized for test generation only - does NOT handle debugging."""
    
    def __init__(self, memory_agent: MemoryAgent):
        self.memory = memory_agent

    async def generate_tests(self, description: str, plan_json: dict, source_code: str) -> str:
        self.memory.log_event("TesterAgent", "Starting test suite generation.")
        
        source_filename, test_filename = get_agent_filenames(plan_json)
        source_filename_raw = source_filename.replace(".m", "").replace(".py", "")
        
        prompt = (
            f"Requirements:\n{description}\n\n"
            f"Plan:\n{plan_json}\n\n"
            f"Source Code (import as sandbox.{source_filename_raw}):\n{source_code}\n\n"
            f"Target Test Filename:\n{test_filename}\n\n"
            f"Generate the comprehensive test suite."
        )
        
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
