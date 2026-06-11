from typing import TypedDict, List, Dict, Any
import json
import re
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage, ToolMessage
from langgraph.graph import StateGraph, END

from config import get_llm
from prompt import AGENT_SYSTEM_PROMPT, get_coder_prompt
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
        content = None
        if content_match:
            raw_content = content_match.group(1)
            content = raw_content.replace('\\n', '\n') \
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
        if content is not None:
            args["content"] = content
        if command is not None:
            args["command"] = command
        if timeout is not None:
            args["timeout"] = timeout
        return {"name": name, "arguments": args}

    # 1. Try parsing entire string as direct JSON (dictionary or list)
    try:
        data = json.loads(content_str)
        if isinstance(data, dict):
            call = extract_call(data)
            if call:
                return [call]
        elif isinstance(data, list):
            for item in data:
                call = extract_call(item)
                if call:
                    calls.append(call)
            if calls:
                return calls
    except json.JSONDecodeError:
        data = parse_with_regex(content_str)
        if data:
            call = extract_call(data)
            if call:
                return [call]

    # 2. Try extracting JSON from markdown code blocks
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
            SystemMessage(content=AGENT_SYSTEM_PROMPT)
        ]
        
        # Build first user message with project description and context
        current_files = list_workspace_files(workspace_dir=state.get("workspace_dir"))
        init_prompt = get_coder_prompt(state["description"], current_files)
        messages.append(HumanMessage(content=init_prompt))
    
    # If there are syntax or runtime execution errors from the last test iteration, inject them
    if state.get("errors"):
        print(f"\n⚠️ Alerting agent to test failures (Iteration {iterations})...")
        error_context = f"""### TEST EXECUTION FAILURE NOTICE (Self-Correction Loop)
The previous code test execution encountered errors. You must refactor your code to fix this.
Stack trace / Error details:
{state['errors']}

Review the files, locate the bugs, edit the files, and verify with your execution/testing tools again.
"""
        messages.append(HumanMessage(content=error_context))
        
    response = llm.invoke(messages)
    print(f"DEBUG: Model response content: {repr(response.content)}")
    
    # Parse tool calls from the raw content manually since we do not bind tools
    parsed_calls = parse_ollama_tool_call(response.content)
    print(f"DEBUG: Model response tool calls (parsed): {parsed_calls}")
    
    ai_msg = AIMessage(
        content=response.content,
        additional_kwargs=response.additional_kwargs,
        response_metadata=response.response_metadata,
        tool_calls=parsed_calls,
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
    if last_message.tool_calls:
        for tool_call in last_message.tool_calls:
            name = tool_call["name"]
            args = tool_call["args"]
            tool_call_id = tool_call["id"]
            
            print(f"🛠️ Executing Tool: {name}({str(args)})")
            
            try:
                if name == "list_workspace_files_tool":
                    tool_res = list_workspace_files(workspace_dir=workspace_dir)
                elif name == "read_code_file_tool":
                    tool_res = read_code_file(args.get("relative_path"), workspace_dir=workspace_dir)
                elif name == "write_code_file_tool":
                    tool_res = write_code_file(args.get("relative_path"), args.get("content"), workspace_dir=workspace_dir)
                elif name == "install_requirements_tool":
                    tool_res = install_workspace_requirements(workspace_dir=workspace_dir)
                elif name == "execute_external_command_tool":
                    tool_res = execute_external_command_tool(
                        args.get("command"), 
                        workspace_dir=workspace_dir, 
                        timeout=args.get("timeout", 15), 
                        task_id=task_id
                    )
                elif name == "test_python_file_tool":
                    tool_res = test_python_file(
                        args.get("relative_path"), 
                        workspace_dir=workspace_dir, 
                        task_id=task_id
                    )
                    if isinstance(tool_res, dict):
                        tool_res = f"Status: {tool_res['status']}\nExit Code: {tool_res['exit_code']}\nStdout:\n{tool_res['stdout']}\nStderr/Errors:\n{tool_res['stderr']}"
                else:
                    tool_res = f"Error: Tool {name} not found."
            except Exception as e:
                tool_res = f"Error executing tool: {str(e)}"
                
            tool_messages.append(
                ToolMessage(content=str(tool_res), tool_call_id=tool_call_id)
            )
            
    return {"messages": messages + tool_messages}

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

    workspace_dir = state.get("workspace_dir")
    test_command = state.get("test_command")
    language = state.get("language", "python")
    
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
        
    res = software_agent.execute_command(test_command, workspace_dir, timeout=15, task_id=task_id)
    
    errors_found = ""
    status = "PASSED" if res["exit_code"] == 0 else "FAILED"
    if res["exit_code"] == -1:
        status = "TIMEOUT"
        
    if res["exit_code"] != 0:
        print(f"❌ Verification Failed (Exit Code: {res['exit_code']})")
        errors_found = software_agent.parse_output(res["stdout"], res["stderr"], language)
    else:
        print(f"✅ Verification Passed!")
        
    test_results = [{
        "command": test_command,
        "status": status,
        "exit_code": res["exit_code"]
    }]
    
    return {
        "errors": errors_found,
        "test_results": test_results
    }

# 4. Graph Construction
def router(state: AgentState) -> str:
    """Routes to tools if model calls tools, else checks compiler tests."""
    last_message = state["messages"][-1]
    if last_message.tool_calls:
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
