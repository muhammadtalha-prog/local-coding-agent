from llm import async_query_llm
from memory import MemoryAgent
from settings import REVIEW_PROMPT, get_agent_filenames, REVIEW_MODEL


class ReviewAgent:
    def __init__(self, memory_agent: MemoryAgent):
        self.memory = memory_agent

    async def review_code(self, source_code: str, test_code: str, execution_log: str) -> str:
        """
        Conducts a rigorous safety, risk, and compliance audit on the generated code.
        """
        self.memory.log_event("ReviewAgent", "Starting safety and compliance audit review.")
        
        description = self.memory.state.get("description", "")
        plan = self.memory.state.get("plan", {})
        source_filename, test_filename = get_agent_filenames(self.memory.state)
        
        prompt = (
            f"--- SYSTEM REQUIREMENTS ---\n{description}\n\n"
            f"--- ARCHITECTURAL SPECIFICATION ---\n{plan}\n\n"
            f"--- GENERATED SOURCE CODE ({source_filename}) ---\n{source_code}\n\n"
            f"--- GENERATED TEST SUITE ({test_filename}) ---\n{test_code}\n\n"
            f"--- VERIFICATION & TEST RUN LOGS ---\n{execution_log}\n\n"
            f"Please conduct the safety, risk, and DO-178C alignment audit and output the markdown compliance report."
        )
        
        try:
            # Query LLM asynchronously using the REVIEW_PROMPT system prompt
            report = await async_query_llm(prompt, system_instruction=REVIEW_PROMPT, model_name=REVIEW_MODEL)
            
            # Save the report in memory
            self.memory.update_state("review_report", report)
            
            # Write report file in sandbox
            self.memory.write_source_file("compliance_review.md", report)
            
            self.memory.log_event("ReviewAgent", "Safety compliance audit complete. Saved to sandbox/compliance_review.md")
            return report
        except Exception as e:
            self.memory.log_event("ReviewAgent", f"Safety compliance audit failed: {e}")
            raise e
