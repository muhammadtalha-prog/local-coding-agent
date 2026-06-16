from typing import TypedDict, List, Dict, Any
import json
import re
import os
import shutil
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage, ToolMessage
from langgraph.graph import StateGraph, END

from config import get_llm, WORKSPACE_DIR, DEFAULT_COMMAND_TIMEOUT
from prompt import RAW_CODE_SYSTEM_PROMPT, get_raw_coder_prompt
from tools import (
    list_workspace_files,
    read_code_file,
    write_code_file,
    install_workspace_requirements,
    execute_external_command_tool,
    test_python_file
)
from external_software import TaskStatusRegistry

# 1. State Definition
class AgentState(TypedDict):
    description: str
    files: List[str]
    messages: List[BaseMessage]
    iterations: int
    max_iterations: int
    errors: str
    test_results: List[Dict[str, Any]]
    test_command: str
    workspace_dir: str
    language: str
    task_id: str # Unique identifier for service execution tracking

def parse_ollama_tool_call(content: str) -> List[Dict[str, Any]]:
    """
    Attempts to parse tool calls from raw response content when Ollama/model
    returns tool calls as JSON in text instead of populating tool_calls natively.
    Supports parsing multiple JSON objects representing separate tool calls.
    Also falls back to robust text-based heuristic parsing to force Ollama to write files
    and execute commands even if formatting is off.
    """
    if not content:
        return []
        
    content_str = content.strip().replace(r"\'", "'")
    calls = []
    
    def extract_call(d):
        if not isinstance(d, dict):
            return None
        name = d.get("name")
        args = d.get("arguments") or d.get("args") or {}
        if name:
            if "." in name:
                name = name.split(".")[-1]
            
            # Map common suffix-dropped synonyms
            if name == "test_python_file":
                name = "test_python_file_tool"
            elif name == "write_code_file":
                name = "write_code_file_tool"
            elif name == "read_code_file":
                name = "read_code_file_tool"
            elif name == "list_workspace_files":
                name = "list_workspace_files_tool"
            elif name == "install_requirements":
                name = "install_requirements_tool"
            elif name == "execute_external_command":
                name = "execute_external_command_tool"
                
            return {
                "name": name,
                "args": args,
                "id": f"call_{name}_{len(calls)}",
                "type": "tool_call"
            }
        return None

    def parse_with_regex(block_str: str) -> Dict[str, Any]:
        name_match = re.search(r'"name"\s*:\s*"([^"]+)"', block_str)
        if not name_match:
            return None
        name = name_match.group(1)
        
        path_match = re.search(r'"relative_path"\s*:\s*"([^"]+)"', block_str)
        relative_path = path_match.group(1) if path_match else None
        
        content_match = re.search(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"', block_str, re.DOTALL)
        content_val = None
        if content_match:
            raw_content = content_match.group(1)
            content_val = raw_content.replace('\\n', '\n') \
                                     .replace('\\t', '\t') \
                                     .replace('\\"', '"') \
                                     .replace("\\'", "'") \
                                     .replace('\\/', '/') \
                                     .replace('\\\\', '\\')
                                     
        cmd_match = re.search(r'"command"\s*:\s*"((?:[^"\\]|\\.)*)"', block_str)
        command = None
        if cmd_match:
            raw_command = cmd_match.group(1)
            command = raw_command.replace('\\"', '"').replace('\\\\', '\\')
            
        timeout_match = re.search(r'"timeout"\s*:\s*(\d+)', block_str)
        timeout = int(timeout_match.group(1)) if timeout_match else 15
        
        args = {}
        if relative_path is not None:
            args["relative_path"] = relative_path
        if content_val is not None:
            args["content"] = content_val
        if command is not None:
            args["command"] = command
        if timeout is not None:
            args["timeout"] = timeout
        return {"name": name, "arguments": args}

    def unescape_string(s: str) -> str:
        return s.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"').replace("\\'", "'").replace('\\\\', '\\')

    def extract_markdown_code_block(text: str) -> str:
        # Match ```language\n...\n``` or ```\n...\n``` with optional whitespace/newlines
        code_block = re.search(r'```(?:\w+)?\s*\n([\s\S]+?)\s*```', text)
        if code_block:
            return code_block.group(1)
        return ""

    # 1. Try parsing entire string as direct JSON (dictionary or list)
    try:
        data = json.loads(content_str)
        if isinstance(data, dict):
            call = extract_call(data)
            if call:
                calls.append(call)
        elif isinstance(data, list):
            for item in data:
                call = extract_call(item)
                if call:
                    calls.append(call)
    except json.JSONDecodeError:
        pass

    # 2. Try extracting JSON from markdown code blocks
    if not calls:
        code_blocks = re.findall(r"```(?:json)?\s*([\s\S]+?)\s*```", content_str)
        for block in code_blocks:
            try:
                data = json.loads(block.strip())
                if isinstance(data, dict):
                    call = extract_call(data)
                    if call:
                        calls.append(call)
                elif isinstance(data, list):
                    for item in data:
                        call = extract_call(item)
                        if call:
                            calls.append(call)
            except json.JSONDecodeError:
                data = parse_with_regex(block.strip())
                if data:
                    call = extract_call(data)
                    if call:
                        calls.append(call)

    # 3. Fallback: Parse multiple balanced JSON blocks using bracket tracking
    if not calls:
        i = 0
        while i < len(content_str):
            if content_str[i] == '{':
                bracket_count = 1
                j = i + 1
                while j < len(content_str) and bracket_count > 0:
                    if content_str[j] == '{':
                        bracket_count += 1
                    elif content_str[j] == '}':
                        bracket_count -= 1
                    j += 1
                if bracket_count == 0:
                    block = content_str[i:j]
                    try:
                        data = json.loads(block)
                        call = extract_call(data)
                        if call:
                            calls.append(call)
                    except json.JSONDecodeError:
                        data = parse_with_regex(block)
                        if data:
                            call = extract_call(data)
                            if call:
                                calls.append(call)
                    i = j - 1
            i += 1

    # 4. Post-processing/healing: Check if we got write_code_file_tool calls but content is missing or empty
    for call in calls:
        if call.get("name") == "write_code_file_tool":
            args = call.setdefault("args", {})
            if not args.get("content"):
                code_content = extract_markdown_code_block(content)
                if code_content:
                    args["content"] = code_content

    # 5. Greedy fallback using heuristics if no tool calls were successfully parsed
    if not calls:
        # Check for write_code_file_tool pattern
        if "write_code_file_tool" in content or "write_code_file" in content:
            # Try to extract relative_path
            path_match = re.search(r'"relative_path":\s*"([^"]+)"', content)
            if not path_match:
                path_match = re.search(r'(?:"relative_path"|relative_path)\s*[:=]\s*["\']([^"\']+)["\']', content)
            
            if path_match:
                rel_path = path_match.group(1)
                code_content = ""
                content_match = re.search(r'"content":\s*"((?:[^"\\]|\\.)*)"', content, re.DOTALL)
                if content_match:
                    code_content = unescape_string(content_match.group(1))
                else:
                    content_match = re.search(r'"content"\s*:\s*\'((?:[^\'\\]|\\.)*)\'', content, re.DOTALL)
                    if content_match:
                        code_content = unescape_string(content_match.group(1))
                
                # Also look for code in markdown blocks if not extracted yet
                if not code_content:
                    code_content = extract_markdown_code_block(content)
                
                calls.append({
                    "name": "write_code_file_tool",
                    "args": {"relative_path": rel_path, "content": code_content},
                    "id": f"call_write_{rel_path.replace('/', '_').replace('.', '_')}",
                    "type": "tool_call"
                })

        # Also look for execute_external_command_tool
        if "execute_external_command_tool" in content or "execute_external_command" in content or "python" in content:
            cmd_match = re.search(r'"command":\s*"([^"]+)"', content)
            if not cmd_match:
                cmd_match = re.search(r'(?:"command"|command)\s*[:=]\s*["\']([^"\']+)["\']', content)
            
            if cmd_match:
                cmd = cmd_match.group(1)
                calls.append({
                    "name": "execute_external_command_tool",
                    "args": {"command": cmd, "timeout": 30},
                    "id": f"call_execute_{len(calls)}",
                    "type": "tool_call"
                })

    # 6. Ultra-fallback: Parse markdown blocks directly if still no calls, looking for filenames in preceding text
    if not calls:
        # Split content into parts: text and code blocks
        parts = re.split(r'(```[\s\S]*?```)', content)
        for k in range(1, len(parts), 2):
            block_with_backticks = parts[k]
            text_before = parts[k-1] if k > 0 else ""
            
            # Extract language and block content
            block_match = re.match(r'```(\w*)\s*\n([\s\S]*?)\s*```', block_with_backticks)
            if block_match:
                lang = block_match.group(1).lower()
                code_content = block_match.group(2)
                
                # Check if it's a command shell block
                if lang in ('bash', 'sh', 'shell', 'cmd', 'powershell', 'bat'):
                    # Split command lines
                    for line in code_content.splitlines():
                        line_strip = line.strip()
                        if line_strip and not line_strip.startswith('#'):
                            calls.append({
                                "name": "execute_external_command_tool",
                                "args": {"command": line_strip, "timeout": 30},
                                "id": f"call_execute_{len(calls)}",
                                "type": "tool_call"
                            })
                else:
                    # Look for a filename in text_before
                    filenames = re.findall(r'`?(\b[a-zA-Z0-9_\-\/]+\.(?:py|js|ts|json|html|css|sh|bat|m|txt|md|yaml|yml)\b)`?', text_before)
                    if filenames:
                        # Use the last mentioned filename (closest to code block)
                        rel_path = filenames[-1]
                        calls.append({
                            "name": "write_code_file_tool",
                            "args": {"relative_path": rel_path, "content": code_content},
                            "id": f"call_write_{rel_path.replace('/', '_').replace('.', '_')}",
                            "type": "tool_call"
                        })

    # Ensure it always returns a list, never None
    if not isinstance(calls, list):
        calls = []
    return calls

llm = get_llm()

# 3. Graph Nodes
def call_model_node(state: AgentState) -> Dict[str, Any]:
    """
    LLM reasoning node that analyzes code, reads stack traces, and schedules edits.
    Checks cancellation flags and updates worker heartbeat.
    """
    task_id = state.get("task_id")
    if task_id:
        TaskStatusRegistry.update_heartbeat(task_id)
        if TaskStatusRegistry.is_cancelled(task_id):
            print(f"🛑 Task {task_id} has been cancelled. Terminating execution loop.")
            return {
                "messages": state.get("messages", []) + [AIMessage(content="Task cancelled by user.")],
                "errors": "Task cancelled by user."
            }

    messages = state.get("messages", [])
    iterations = state.get("iterations", 0)
    max_iterations = state.get("max_iterations", 8)

    # Check if we have already reached the self-correction/iteration limit
    if iterations >= max_iterations:
        print(f"\n⚠️ Reached max self-correction/iteration limit ({max_iterations}). Finalizing with current state.")
        return {
            "messages": messages + [AIMessage(content="Reached maximum execution steps. Terminating loop.")],
            "iterations": iterations
        }

    # Increment iterations count for this model run
    iterations += 1

    # If starting, construct initial system prompt and workspace target instructions
    if not messages:
        messages = [
            SystemMessage(content=RAW_CODE_SYSTEM_PROMPT)
        ]
        
        # Build first user message with project description and context
        current_files = list_workspace_files(workspace_dir=state.get("workspace_dir"))
        init_prompt = get_raw_coder_prompt(state["description"], current_files, state.get("test_command", ""))
        messages.append(HumanMessage(content=init_prompt))
    
    # If there are syntax or runtime execution errors from the last test iteration, inject them
    if state.get("errors"):
        print(f"\n⚠️ Alerting agent to test failures (Iteration {iterations})...")
        error_context = f"""### TEST EXECUTION FAILURE NOTICE (Self-Correction Loop)
The previous code test execution encountered errors. You must refactor your code to fix this.
Stack trace / Error details:
{state['errors']}

Review the files, locate the bugs, edit the files, and verify with your execution/testing tools again.
Remember to output the corrected code for the necessary files using the `# FILE: filename` format.
"""
        messages.append(HumanMessage(content=error_context))

    # Construct active messages list with updated workspace file state to prevent loops
    active_messages = list(messages)
    if len(active_messages) > 2:
        current_files = list_workspace_files(workspace_dir=state.get("workspace_dir"))
        state_context = f"### CURRENT WORKSPACE STATE UPDATE\nFiles currently in workspace:\n" + \
                        ("\n".join([f"- {f}" for f in current_files]) if current_files else "No files yet.")
        active_messages.append(HumanMessage(content=state_context))
        
    response = llm.invoke(active_messages)
    print(f"DEBUG: Model response content: {repr(response.content)}")
    
    # 1. Parse "# FILE: filename" separators from the response
    workspace_dir = state.get("workspace_dir")
    written_files = []
    
    # Regex to find "# FILE: filename" and content
    pattern = r'#+\s*[Ff][Ii][Ll][Ee]\s*:\s*([a-zA-Z0-9_\-\/\.]+)([\s\S]*?)(?=(?:#+\s*[Ff][Ii][Ll][Ee]\s*:)|$)'
    matches = re.findall(pattern, response.content)
    
    has_requirements = False
    for filename, file_content in matches:
        filename = filename.strip()
        # Clean up file content
        code_block = re.search(r'```(?:\w+)?\s*\n([\s\S]+?)\s*```', file_content)
        if code_block:
            code = code_block.group(1).strip()
        else:
            code = file_content.strip()
            if code.startswith('```python'):
                code = code[9:].strip()
            elif code.startswith('```'):
                code = code[3:].strip()
            if code.endswith('```'):
                code = code[:-3].strip()
        
        # Write file to workspace_dir
        if workspace_dir:
            full_path = os.path.join(workspace_dir, filename)
            # Create subdirectories if they don't exist
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(code)
            print(f"📝 Wrote file to workspace: {filename} ({len(code)} bytes)")
            written_files.append(filename)
            
            if filename == "requirements.txt":
                has_requirements = True
                
    # If requirements.txt was written, automatically install requirements
    if has_requirements and workspace_dir:
        print("📦 requirements.txt updated. Installing dependencies...")
        try:
            install_workspace_requirements(workspace_dir=workspace_dir)
            print("✅ Dependencies installed successfully.")
        except Exception as e:
            print(f"❌ Error installing dependencies: {e}")

    # 2. Fallback to standard parsing if no raw # FILE: blocks were written
    parsed_calls = []
    if not written_files:
        parsed_calls = parse_ollama_tool_call(response.content)
        if parsed_calls is None:
            parsed_calls = []
        print(f"DEBUG: Model response tool calls (parsed): {parsed_calls}")
    else:
        print(f"📝 Directly generated and saved {len(written_files)} files: {written_files}. Bypassing tool node routing.")
    
    ai_msg = AIMessage(
        content=response.content,
        additional_kwargs=response.additional_kwargs,
        response_metadata=response.response_metadata,
        tool_calls=parsed_calls if parsed_calls is not None else [],
        id=response.id
    )
            
    return {"messages": messages + [ai_msg], "iterations": iterations}

def execute_tools_node(state: AgentState) -> Dict[str, Any]:
    """
    Executes tool calls requested by the model, injecting the isolated workspace folder path.
    Checks cancellation flags and updates worker heartbeat.
    """
    task_id = state.get("task_id")
    if task_id:
        TaskStatusRegistry.update_heartbeat(task_id)
        if TaskStatusRegistry.is_cancelled(task_id):
            print(f"🛑 Task {task_id} has been cancelled. Terminating execution loop.")
            return {"errors": "Task cancelled by user."}

    messages = state["messages"]
    last_message = messages[-1]
    workspace_dir = state.get("workspace_dir")
    
    tool_messages = []
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        for tool_call in last_message.tool_calls:
            name = tool_call["name"]
            args = tool_call["args"] or {}
            tool_call_id = tool_call["id"]
            
            print(f"🛠️ Executing Tool (Robust Mode): {name}({str(args)})")
            
            try:
                # 1. Handle auto-writing files if relative_path and content are both in arguments
                auto_write_res = ""
                has_write = "relative_path" in args and "content" in args
                if has_write:
                    auto_write_res = write_code_file(args.get("relative_path"), args.get("content"), workspace_dir=workspace_dir)
                    print(f"📝 Auto-wrote file {args.get('relative_path')}: {auto_write_res}")

                # 2. Handle running command if command is in arguments
                auto_cmd_res = ""
                has_command = "command" in args or name == "execute_external_command_tool"
                if has_command:
                    cmd = args.get("command")
                    if not cmd and name == "execute_external_command_tool":
                        cmd = args.get("relative_path") # sometimes model puts command in relative_path
                    if cmd:
                        print(f"💻 Running auto-command: {cmd}")
                        auto_cmd_res = execute_external_command_tool(
                            cmd, 
                            workspace_dir=workspace_dir, 
                            timeout=args.get("timeout", DEFAULT_COMMAND_TIMEOUT), 
                            task_id=task_id
                        )

                # 3. Standard routing fallback if neither auto-write nor auto-command executed, or to augment them
                if name == "list_workspace_files_tool":
                    tool_res = list_workspace_files(workspace_dir=workspace_dir)
                elif name == "read_code_file_tool":
                    if not has_write:
                        tool_res = read_code_file(args.get("relative_path"), workspace_dir=workspace_dir)
                    else:
                        tool_res = f"Success: {auto_write_res}"
                        if auto_cmd_res:
                            tool_res += f"\nCommand Execution Results:\n{auto_cmd_res}"
                elif name == "write_code_file_tool":
                    tool_res = f"Success: {auto_write_res}"
                    if auto_cmd_res:
                        tool_res += f"\nCommand Execution Results:\n{auto_cmd_res}"
                elif name == "install_requirements_tool":
                    tool_res = install_workspace_requirements(workspace_dir=workspace_dir)
                elif name == "execute_external_command_tool":
                    tool_res = auto_cmd_res
                elif name == "test_python_file_tool":
                    tool_res = test_python_file(
                        args.get("relative_path"), 
                        workspace_dir=workspace_dir, 
                        task_id=task_id
                    )
                    if isinstance(tool_res, dict):
                        tool_res = f"Status: {tool_res['status']}\nExit Code: {tool_res['exit_code']}\nStdout:\n{tool_res['stdout']}\nStderr/Errors:\n{tool_res['stderr']}"
                else:
                    # Fallback/default logic
                    if has_write or has_command:
                        tool_res = ""
                        if has_write:
                            tool_res += f"Success: {auto_write_res}"
                        if auto_cmd_res:
                            tool_res += f"\nCommand Execution Results:\n{auto_cmd_res}"
                    else:
                        tool_res = f"Error: Tool {name} not found."
            except Exception as e:
                tool_res = f"Error executing tool: {str(e)}"
                
            tool_messages.append(
                ToolMessage(content=str(tool_res), tool_call_id=tool_call_id)
            )
            
    return {"messages": messages + tool_messages}

def detect_placeholders(code: str) -> List[str]:
    placeholders = []
    # Match comments containing todo, fixme, placeholder, or code here
    comment_pattern = r'(?:#|//|/\*|%)\s*(?i:\b(?:todo|fixme|placeholder|insert\s+code|code\s+here|implement\s+here)\b)'
    if re.search(comment_pattern, code):
        placeholders.append("comment placeholder (e.g. TODO, placeholder, etc.)")
    
    # Match ellipsis (...) on a line by itself
    if re.search(r'(?m)^\s*\.\.\.\s*$', code):
        placeholders.append("Ellipsis (...) placeholder")
        
    return placeholders

def heal_missing_files(workspace_dir: str, test_command: str):
    if not test_command or not workspace_dir:
        return
    try:
        # Find all potential python/matlab files in the test command
        files_in_cmd = re.findall(r'\b([a-zA-Z0-9_\-\.]+)\.(py|m)\b', test_command)
        py_files = [f"{name}.{ext}" for name, ext in files_in_cmd]
        
        # If the command has python -m unittest module, extract module name
        unittest_modules = re.findall(r'-m\s+unittest\s+([a-zA-Z0-9_\-\.]+)\b', test_command)
        for m in unittest_modules:
            if not m.endswith('.py'):
                py_files.append(m + '.py')
                
        # Check if any of these expected files are missing
        if os.path.exists(workspace_dir):
            for expected_file in py_files:
                expected_path = os.path.join(workspace_dir, expected_file)
                if not os.path.exists(expected_path):
                    print(f"🔍 Healing: Expected file '{expected_file}' is missing in workspace.")
                    # Look for written python/matlab files
                    all_files = os.listdir(workspace_dir)
                    
                    # Check for candidates that are files and have matching extension
                    candidates = []
                    for f in all_files:
                        full_f_path = os.path.join(workspace_dir, f)
                        if os.path.isfile(full_f_path) and f.endswith(expected_file.split('.')[-1]) and f != expected_file:
                            candidates.append(f)
                    
                    if candidates:
                        # Find the best candidate based on name similarity and test matching
                        is_expected_test = 'test' in expected_file.lower()
                        
                        def score_candidate(c):
                            is_c_test = 'test' in c.lower()
                            # Preference for matching test status
                            test_match_bonus = 1.0 if is_expected_test == is_c_test else 0.0
                            # Similarity ratio
                            import difflib
                            sim = difflib.SequenceMatcher(None, expected_file.lower(), c.lower()).ratio()
                            return (test_match_bonus, sim)
                        
                        # Sort candidates descending by score
                        candidates.sort(key=score_candidate, reverse=True)
                        candidate = candidates[0]
                        
                        src_path = os.path.join(workspace_dir, candidate)
                        shutil.copy2(src_path, expected_path)
                        print(f"💡 Healing: Copied '{candidate}' to '{expected_file}' to satisfy verification command.")
    except Exception as e:
        print(f"⚠️ Error during healing: {e}")

def execution_node(state: AgentState) -> Dict[str, Any]:
    """
    Generic execution node that executes commands in the designated workspace and language.
    Iteratively parses stdout/stderr to update error details.
    Checks cancellation flags and updates worker heartbeat.
    """
    task_id = state.get("task_id")
    if task_id:
        TaskStatusRegistry.update_heartbeat(task_id)
        if TaskStatusRegistry.is_cancelled(task_id):
            print(f"🛑 Task {task_id} has been cancelled. Terminating execution loop.")
            return {"errors": "Task cancelled by user."}

    workspace_dir = state.get("workspace_dir") or os.path.abspath(WORKSPACE_DIR)
    test_command = state.get("test_command")
    language = state.get("language", "python")
    
    # 1. Run the file healer before executing the test command
    heal_missing_files(workspace_dir, test_command)
    
    if not test_command:
        print("ℹ️ No test_command specified. Skipping execution checks.")
        return {"errors": ""}
        
    print(f"\n🔍 Executing task verification: {test_command} inside {workspace_dir}...")
    
    from external_software import ExternalSoftwareAgent, PythonAgent, MATLABAgent
    
    # Select appropriate agent subclass
    if language.lower() == "python":
        software_agent = PythonAgent()
    elif language.lower() in ("matlab", "m"):
        software_agent = MATLABAgent()
    else:
        software_agent = ExternalSoftwareAgent()
        
    res = software_agent.execute_command(test_command, workspace_dir, timeout=DEFAULT_COMMAND_TIMEOUT, task_id=task_id)
    
    errors_found = ""
    status = "PASSED" if res["exit_code"] == 0 else "FAILED"
    if res["exit_code"] == -1:
        status = "TIMEOUT"
        
    if res["exit_code"] != 0:
        print(f"❌ Verification Failed (Exit Code: {res['exit_code']})")
        errors_found = software_agent.parse_output(res["stdout"], res["stderr"], language)
    else:
        print(f"✅ Verification Passed!")
        
    # Check for placeholders in files
    placeholders_errors = []
    if os.path.exists(workspace_dir):
        for root, _, files in os.walk(workspace_dir):
            if "venv" in root.split(os.sep) or ".git" in root.split(os.sep) or "__pycache__" in root.split(os.sep):
                continue
            for file in files:
                if file.endswith(('.py', '.m', '.js', '.ts', '.java', '.c', '.cpp', '.h')):
                    file_path = os.path.join(root, file)
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                        placeholders = detect_placeholders(content)
                        if placeholders:
                            placeholders_errors.append(f"File '{file}' contains placeholders or TODOs: {', '.join(placeholders)}. You must write the complete code without placeholders.")
                    except Exception:
                        pass
                        
    if placeholders_errors:
        placeholder_msg = "\n".join(placeholders_errors)
        status = "FAILED"
        if errors_found:
            errors_found += "\n" + placeholder_msg
        else:
            errors_found = placeholder_msg
            
    test_results = [{
        "command": test_command,
        "status": status,
        "exit_code": res["exit_code"] if not placeholders_errors else -3
    }]
    
    return {
        "errors": errors_found,
        "test_results": test_results
    }

# 4. Graph Construction
def router(state: AgentState) -> str:
    """Routes to tools if model calls tools, else checks compiler tests."""
    last_message = state["messages"][-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"
    return "tester"

def test_router(state: AgentState) -> str:
    """Checks if compile errors occurred. Loops back to coder if yes, else ends."""
    task_id = state.get("task_id")
    if task_id and TaskStatusRegistry.is_cancelled(task_id):
        return END

    if state.get("errors"):
        iterations = state.get("iterations", 0)
        max_iterations = state.get("max_iterations", 8)
        if iterations < max_iterations:
            return "agent"
        else:
            print(f"⚠️ Reached max self-correction limit ({max_iterations} iterations). Finalizing with current state.")
    return END

workflow = StateGraph(AgentState)

# Add Nodes
workflow.add_node("agent", call_model_node)
workflow.add_node("tools", execute_tools_node)
workflow.add_node("execution", execution_node)

# Set Entry Point
workflow.set_entry_point("agent")

# Add Edges
workflow.add_conditional_edges("agent", router, {"tools": "tools", "tester": "execution"})
workflow.add_edge("tools", "agent")
workflow.add_conditional_edges("execution", test_router, {"agent": "agent", END: END})

# Compile Graph
graph = workflow.compile()
