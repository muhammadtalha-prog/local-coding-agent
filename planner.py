import json
import logging
import re
from typing import Dict, Any
from llm import async_query_llm
from memory import MemoryAgent
from settings import PLANNER_PROMPT, PLANNER_MODEL

logger = logging.getLogger("avionics_framework.planner")

class PlannerAgent:
    def __init__(self, memory_agent: MemoryAgent):
        self.memory = memory_agent

    async def plan(self, description: str, language: str, failure_context: str = "", failure_hint: str = "") -> Dict[str, Any]:
        self.memory.log_event("PlannerAgent", f"Starting system planning phase for language: {language}")
        
        # Load lessons learned to avoid past mistakes
        from settings import LLM_PROVIDER, TRIM_LOCAL_PROMPTS
        if LLM_PROVIDER.lower() in ("local", "ollama") and TRIM_LOCAL_PROMPTS:
            lessons = []
        else:
            lessons = self.memory.load_lessons()

        lessons_context = ""
        if lessons:
            lessons_context = "--- LESSONS LEARNED FROM PAST MISTAKES (AVOID THESE BUGS) ---\n"
            for idx, lesson in enumerate(lessons, 1):
                lessons_context += f"{idx}. Bug/Mistake: {lesson.get('mistake')}\n   Correction/Rule: {lesson.get('correction')}\n"
            lessons_context += "\n"
            
        prompt = f"{lessons_context}User Requirements Description:\n{description}\n\n"
        if failure_hint:
            prompt += f"--- CRITICAL SYSTEM GUIDANCE ---\n{failure_hint}\n\n"
        if failure_context:
            prompt += f"--- CONTEXT: PREVIOUS ATTEMPTS FAILED ---\nBelow is the compilation/test trace of the failures. Please revise the architectural plan to fix these issues:\n{failure_context}\n\n"
        prompt += f"Generate the revised system plan JSON specifically tailored for {language.upper()}."
        
        # Customize the system instruction to force the selected language
        system_instruction = PLANNER_PROMPT.replace(
            "You must target either PYTHON or MATLAB. If requirements are ambiguous, choose the most appropriate one (prefer Python for general programs, MATLAB for signal processing, control design, or numerical modeling).",
            f"You must target {language.upper()}. All designs, architecture, and safety contracts MUST be tailored specifically for {language.upper()}."
        ).replace(
            '1. "language": "python" or "matlab"',
            f'1. "language": "{language.lower()}" (You MUST specify this exact value)'
        )
        
        max_attempts = 3
        current_prompt = prompt
        raw_response = ""
        
        for attempt in range(1, max_attempts + 1):
            try:
                self.memory.log_event("PlannerAgent", f"Querying LLM (async) - attempt {attempt} of {max_attempts}")
                raw_response = await async_query_llm(current_prompt, system_instruction=system_instruction, model_name=PLANNER_MODEL)
                plan_json = self._parse_json_response(raw_response)
                
                # Enforce the selected language in the plan JSON
                plan_json["language"] = language.lower()
                
                self.memory.update_state("plan", plan_json)
                self.memory.update_state("language", plan_json.get("language", "python").lower())
                self.memory.log_event("PlannerAgent", f"Plan generated and parsed successfully on attempt {attempt}.")
                return plan_json
            except ValueError as e:
                self.memory.log_event("PlannerAgent", f"Attempt {attempt} failed JSON parsing: {str(e)}")
                if attempt == max_attempts:
                    logger.error(f"All {max_attempts} planning attempts failed to produce valid JSON.")
                    raise e
                
                # Feedback loop: Construct a new prompt that includes the malformed response and error
                current_prompt = (
                    f"{prompt}\n\n"
                    f"--- ATTEMPT {attempt} FAILED JSON PARSING ---\n"
                    f"Your previous response was not valid JSON and failed with the following error:\n"
                    f"{str(e)}\n\n"
                    f"Here is the raw response you generated:\n"
                    f"```json\n{raw_response}\n```\n\n"
                    f"Please correct the JSON syntax errors. Ensure that:\n"
                    f"1. There are no invalid keys or colon definitions inside arrays (e.g., `\"inputs\": [\"name\": \"val\"]` is INVALID, use objects like `\"inputs\": [{{\"name\": \"val\"}}]` or strings like `\"inputs\": [\"val\"]`).\n"
                    f"2. All strings are properly enclosed in double quotes, and internal quotes or newlines are escaped.\n"
                    f"3. No trailing commas in arrays or objects.\n"
                    f"Generate ONLY the corrected, strictly valid JSON."
                )
            except Exception as e:
                self.memory.log_event("PlannerAgent", f"Planning failed due to API/system error: {str(e)}")
                raise e

        raise RuntimeError("Planning failed to complete within maximum attempts.")

    def _parse_json_response(self, text: str) -> Dict[str, Any]:
        clean_text = text.strip()
        start_idx = clean_text.find("{")
        end_idx = clean_text.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            clean_text = clean_text[start_idx:end_idx + 1]
            
        try:
            return json.loads(clean_text)
        except json.JSONDecodeError as e:
            logger.warning(f"Standard json.loads failed: {e}. Attempting JSON repair.")

        # Attempt json-repair library via dynamic import
        try:
            import importlib
            json_repair = importlib.import_module("json_repair")
            repair_json = getattr(json_repair, "repair_json")
            repaired = repair_json(clean_text)
            parsed = json.loads(repaired)
            if isinstance(parsed, dict):
                return parsed
        except (ImportError, AttributeError):
            logger.warning("json-repair library not installed or not loaded. Using local regex/replace fallback repair.")
        except Exception as e_repair:
            logger.warning(f"json-repair failed: {e_repair}. Using local regex/replace fallback repair.")

        # Local fallback repair logic
        try:
            repaired_text = self._local_json_repair(clean_text)
            return json.loads(repaired_text)
        except Exception as e2:
            logger.error(f"Failed to parse plan JSON. Raw response:\n{text}")
            raise ValueError(f"Planner response is not valid JSON: {e2}")

    def _local_json_repair(self, text: str) -> str:
        # A simple, robust regex fallback to clean up common LLM JSON syntax mistakes:
        # 1. Strip comments (lines starting with // or #)
        cleaned = re.sub(r'(?m)^\s*//.*$', '', text)
        cleaned = re.sub(r'(?m)^\s*#.*$', '', cleaned)
        
        # 2. Fix trailing commas in objects or arrays
        cleaned = re.sub(r',\s*([}\]])', r'\1', cleaned)
        
        # 3. Clean up Python exponents or expressions (e.g. 10**100 or 10**200)
        cleaned = re.sub(r'10\*\*100', '1e100', cleaned)
        cleaned = re.sub(r'10\*\*200', '1e200', cleaned)
        
        # 4. Handle Python boolean/None literals if LLM hallucinated them
        cleaned = re.sub(r'\bTrue\b', 'true', cleaned)
        cleaned = re.sub(r'\bFalse\b', 'false', cleaned)
        cleaned = re.sub(r'\bNone\b', 'null', cleaned)

        return cleaned
