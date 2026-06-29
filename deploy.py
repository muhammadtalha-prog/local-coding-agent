import re
from llm import async_query_llm
from memory import MemoryAgent


DEPLOY_PROMPT = """You are the Deployment Agent. Your task is to rewrite a verified safety-critical function to make it interactive for real-time users, while preserving its core logic, safety contracts, and parameter signatures.

Requirements:
1. For MATLAB:
   - Identify the main function signature at the top (e.g., `function rmse = avionics_voice_transceiver(sample_rate, channel_noise_variance, simulation_duration)`).
   - Right at the start of this main function, add a check for the number of input arguments using `nargin`.
   - If the number of inputs supplied is less than the required number of parameters (e.g., `if nargin < 3`), prompt the user in real-time using MATLAB's `input('Prompt String: ')` command to obtain each of the parameters.
   - Example:
     ```matlab
     if nargin < 3
         sample_rate = input('Enter sample rate (Hz): ');
         channel_noise_variance = input('Enter channel noise variance: ');
         simulation_duration = input('Enter simulation duration (sec): ');
     end
     ```
   - Ensure the prompt messages are clear, descriptive, and user-friendly.

2. For Python:
   - Add an `if __name__ == "__main__":` block at the very bottom of the file.
   - Inside this block, prompt the user for each input argument required by the main function using Python's `input()` (casting them to the appropriate type, e.g. float or int).
   - Call the main function with these inputs and print the final result.
   - After printing, add a line `input("Press Enter to exit...")` to keep the console window open when run by double-clicking on Windows.

3. Real-Time Audio Recording (If applicable):
   - If the program involves processing, simulating, or transmitting voice or audio signals:
     - In the verified sandbox code, the signal was generated synthetically (e.g. using sine waves) to support headless testing.
     - In the deployed code, you must replace the synthetic generator logic (such as inside a `generate_speech_signal` function or signal generation block) with real-time audio recording code.
     - For MATLAB: use MATLAB's built-in `audiorecorder(sample_rate, 16, 1)`, followed by `recordblocking(recObj, duration)`, and then `getaudiodata(recObj)` to capture actual real-time microphone input from the user for the requested duration. Print a message to the user when recording starts and ends so they know it is recording.
     - Example MATLAB replacement inside signal generation function:
       ```matlab
       disp(['Recording voice from microphone for ' num2str(duration) ' seconds...']);
       recObj = audiorecorder(sample_rate, 16, 1);
       recordblocking(recObj, duration);
       disp('Recording finished.');
       signal = getaudiodata(recObj);
       ```

4. General constraints:
   - Output ONLY the modified code. Do NOT output any markdown descriptions or explanations. Output the code inside standard markdown blocks.
   - Keep all other lines of code, helper functions, docstrings, and safety assertions exactly the same.
"""

class DeployAgent:
    def __init__(self, memory_agent: MemoryAgent):
        self.memory = memory_agent

    async def make_interactive(self, source_code: str, language: str) -> str:
        self.memory.log_event("DeployAgent", f"Converting verified code to interactive deployment mode for language: {language}")
        
        prompt = (
            f"--- VERIFIED FUNCTION SOURCE CODE ---\n{source_code}\n\n"
            f"Please rewrite this function to inject interactive user prompt logic. "
            f"Follow all requirements. Keep the function signature, all code safety asserts, and sub-functions intact."
        )
        
        try:
            raw_response = await async_query_llm(prompt, system_instruction=DEPLOY_PROMPT)
            interactive_code = self._extract_code(raw_response)
            self.memory.log_event("DeployAgent", "Interactive deployment code successfully generated.")
            return interactive_code
        except Exception as e:
            self.memory.log_event("DeployAgent", f"Failed to rewrite code for interactive deployment: {e}")
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
