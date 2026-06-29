import re
from llm import async_query_llm
from memory import MemoryAgent
from settings import CODER_PROMPT, get_agent_filenames

class CoderAgent:
    """Specialized for code generation only - does NOT handle debugging."""
    
    def __init__(self, memory_agent: MemoryAgent):
        self.memory = memory_agent

    async def generate_code(self, description: str, plan_json: dict) -> str:
        self.memory.log_event("CoderAgent", "Starting code generation phase.")
        
        filename, _ = get_agent_filenames(plan_json)
        filename_raw = filename.replace(".m", "").replace(".py", "")
        
        # Load lessons learned
        lessons = self.memory.load_lessons()
        lessons_context = ""
        if lessons:
            lessons_context = "--- LESSONS LEARNED (AVOID THESE BUGS) ---\n"
            for idx, lesson in enumerate(lessons, 1):
                lessons_context += f"{idx}. {lesson.get('mistake')}\n   Fix: {lesson.get('correction')}\n"
            lessons_context += "\n"
            
        prompt = (
            f"{lessons_context}Requirements:\n{description}\n\n"
            f"System Plan:\n{plan_json}\n\n"
            f"Target Filename:\n{filename}\n\n"
            f"Generate the complete implementation code. "
            f"Ensure the main function is named '{filename_raw}'."
        )
        
        try:
            raw_response = await async_query_llm(prompt, system_instruction=CODER_PROMPT)
            code = self._extract_code(raw_response)
            
            self.memory.update_state("source_code", code)
            self.memory.write_source_file(filename, code)
            
            self.memory.log_event("CoderAgent", f"Source code generated and saved to sandbox/{filename}")
            return code
        except Exception as e:
            self.memory.log_event("CoderAgent", f"Code generation failed: {str(e)}")
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
