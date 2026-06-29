import sys
import io
import logging
from typing import Tuple
from rich.console import Console
from rich.panel import Panel

# Configure Windows encoding support for Unicode stdout/stderr
if sys.platform.startswith("win"):
    try:
        reconfigure_out = getattr(sys.stdout, "reconfigure", None)
        if reconfigure_out:
            reconfigure_out(encoding='utf-8')
        reconfigure_err = getattr(sys.stderr, "reconfigure", None)
        if reconfigure_err:
            reconfigure_err(encoding='utf-8')
    except AttributeError:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from memory import MemoryAgent
from planner import PlannerAgent
from coder import CoderAgent
from linter import LinterAgent
from tester import TesterAgent
from debugger import DebuggerAgent
from timer import TimerAgent
from executor import ExecutorAgent
from review import ReviewAgent
from deploy import DeployAgent
from settings import get_agent_filenames

logger = logging.getLogger("avionics_framework.head")
console = Console()

class HeadCoordinator:
    def __init__(self, session_id: str = "default"):
        self.memory = MemoryAgent(session_id)
        self.planner = PlannerAgent(self.memory)
        self.coder = CoderAgent(self.memory)
        self.linter = LinterAgent(self.memory)
        self.tester = TesterAgent(self.memory)
        self.debugger = DebuggerAgent(self.memory)
        self.timer = TimerAgent(self.memory)
        self.executor = ExecutorAgent(self.memory)
        self.reviewer = ReviewAgent(self.memory)
        self.deployer = DeployAgent(self.memory)


    async def orchestrate(self, description: str, language: str = "python") -> Tuple[bool, str]:
        """
        Coordinates the entire planning, coding, verification, debugging, safety audit, 
        mandatory human approval, and final sandbox deployment pipeline asynchronously.
        """
        console.print(Panel("[bold green]Multi-Agent Code Generation System Starting...[/bold green]", title="System Startup"))
        self.memory.clear_sandbox()
        self.memory.update_state("description", description)
        self.memory.update_state("loop_count", 0)
        self.memory.update_state("logs", [])

        from settings import MAX_PLANNER_ESCALATIONS
        
        planner_escalation_count = 0
        failure_context = ""
        
        while True:
            # 1. Planning
            console.print(f"[yellow]Planning Agent: Analyzing requirements (Escalation: {planner_escalation_count}/{MAX_PLANNER_ESCALATIONS})...[/yellow]")
            try:
                plan = await self.planner.plan(description, language=language, failure_context=failure_context)
            except Exception as e:
                return False, f"Planning phase failed: {e}"

            language = plan.get("language", "python").lower()
            
            # Check environment availability for MATLAB target
            if language == "matlab":
                import shutil
                from pathlib import Path
                from settings import get_matlab_exe
                resolved_matlab = get_matlab_exe()
                matlab_exists = False
                if resolved_matlab != "matlab" and Path(resolved_matlab).exists():
                    matlab_exists = True
                else:
                    matlab_exists = bool(shutil.which("matlab"))
                
                if not matlab_exists:
                    console.print("[bold yellow]Warning: MATLAB executable not found. Falling back to Python target...[/bold yellow]")
                    self.memory.log_event("HeadCoordinator", "MATLAB executable missing. Falling back to Python.")
                    
                    # Modify plan for python fallback
                    plan["language"] = "python"
                    # Rename file_name regardless of whether it has .m extension
                    raw_name = plan.get("file_name", "") or ""
                    plan["file_name"] = raw_name.replace(".m", "")
                    if "components" in plan and isinstance(plan["components"], list):
                        for comp in plan["components"]:
                            if "name" in comp and comp["name"]:
                                comp["name"] = comp["name"].replace(".m", "")
                                
                    self.memory.update_state("language", "python")
                    self.memory.update_state("plan", plan)
                    language = "python"
            
            console.print("[yellow]Coding Agent: Generating implementation source code...[/yellow]")
            try:
                source_code = await self.coder.generate_code(description, plan)
            except Exception as e:
                return False, f"Code generation phase failed: {e}"

            # 3. Main Testing & Debugging Loop
            tests_generated = False
            test_code = ""
            last_log = ""
            loop_success = False

            while True:
                # 3a. Static Lint Checks
                source_filename, _ = get_agent_filenames(plan)
                with console.status("[bold yellow]Linter Agent: Verifying coding style & type checking safety in VM...[/bold yellow]", spinner="aesthetic"):
                    lint_pass, lint_report = await self.linter.lint(source_filename, language)
                last_log = lint_report
                
                if not lint_pass:
                    console.print(Panel(f"[bold red]Linter verification failed:[/bold red]\n{lint_report}", title="Lint Failure"))
                    self.memory.log_event("HeadCoordinator", f"Linter issues found:\n{lint_report}")
                    
                    # Check with Timer Agent if we should halt
                    continue_debug, message = self.timer.verify_loop_safety(lint_report)
                    if not continue_debug:
                        if message.startswith("INSTALL:"):
                            missing_module = message.split(":")[1]
                            installed = await self._install_missing_package(missing_module)
                            if installed:
                                continue
                        console.print(f"[bold red]Circuit Breaker Triggered: {message}[/bold red]")
                        break
                    
                    # Request Debug/Fix from Debugger Agent
                    console.print("[yellow]Debugger Agent: Attempting to fix static analysis violations...[/yellow]")
                    source_code, test_code = await self.debugger.debug_code(source_code, test_code, lint_report)
                    continue

                # 3b. Generate Test Suite (if not done already)
                if not tests_generated:
                    console.print("[yellow]Tester Agent: Designing safety unit test cases...[/yellow]")
                    try:
                        test_code = await self.tester.generate_tests(description, plan, source_code)
                        tests_generated = True
                    except Exception as e:
                        return False, f"Test generation failed: {e}"

                # 3c. Execute Test Suite
                with console.status("[bold yellow]Executor Agent: Running sandbox test suite verification in isolated VM...[/bold yellow]", spinner="aesthetic"):
                    test_success, test_report = await self.executor.run_tests(language)
                last_log = test_report

                if not test_success:
                    console.print(Panel(f"[bold red]Test suite failed:[/bold red]\n{test_report}", title="Test Failure"))
                    self.memory.log_event("HeadCoordinator", f"Execution error trace:\n{test_report}")
                    
                    # Check loop limits via Timer Agent
                    current_loop_limit, message = self.timer.verify_loop_safety(test_report)
                    if not current_loop_limit:
                        if message.startswith("INSTALL:"):
                            missing_module = message.split(":")[1]
                            installed = await self._install_missing_package(missing_module)
                            if installed:
                                continue
                        console.print(f"[bold red]Circuit Breaker Triggered: {message}[/bold red]")
                        break

                    # Debug & fix
                    console.print("[yellow]Debugger Agent: Debugging execution failures and adjusting code...[/yellow]")
                    source_code, test_code = await self.debugger.debug_code(source_code, test_code, test_report)
                    continue

                console.print("[bold green]All checks passed: static analysis and unit tests succeeded![/bold green]")
                loop_success = True
                break

            if loop_success:
                break
                
            # Escalation to planner if loops failed
            if planner_escalation_count < MAX_PLANNER_ESCALATIONS:
                planner_escalation_count += 1
                failure_context = last_log
                console.print(f"[bold yellow]Escalating to Planner Agent to revise architecture (Attempt {planner_escalation_count}/{MAX_PLANNER_ESCALATIONS})...[/bold yellow]")
                # Reset iteration loop count for new architecture run
                self.memory.update_state("loop_count", 0)
                continue
            else:
                return False, f"Halted: Maximum planner escalations ({MAX_PLANNER_ESCALATIONS}) reached without resolving safety check issues. Last log:\n{last_log}"

        from settings import ENABLE_REVIEW
        if ENABLE_REVIEW:
            console.print("[yellow]Review Agent: Conducting code quality audit...[/yellow]")
            try:
                review_report = await self.reviewer.review_code(source_code, test_code, last_log)
                console.print(Panel(review_report, title="Code Quality Review Report", border_style="cyan"))
            except Exception as e:
                console.print(f"[yellow]Warning: Review Agent audit failed: {e}. Proceeding...[/yellow]")
                review_report = "Review Agent audit failed to execute."
        else:
            console.print("[yellow]Review Agent: Code quality audit disabled. Skipping...[/yellow]")
            review_report = "Review Agent audit disabled by user."

        # 5. Human Approval (HITL)
        from settings import AUTO_APPROVE
        
        if AUTO_APPROVE:
            console.print("\n[bold green]AUTO-APPROVE: All checks passed. Proceeding to deploy...[/bold green]")
            self.memory.log_event("HeadCoordinator", "Deployment automatically approved (AUTO_APPROVE=True).")
        else:
            console.print("\n[bold yellow]HUMAN-IN-THE-LOOP APPROVAL REQUIRED[/bold yellow]")
            print("Do you approve deploying this verified code to the workspace/ directory? (y/n): ", end="", flush=True)
            
            approval = sys.stdin.readline().strip().lower()
            if approval not in ("y", "yes"):
                self.memory.log_event("HeadCoordinator", "Deployment rejected by engineer.")
                console.print("[bold red]Deployment Rejected. Halting system without copying to workspace/ directory.[/bold red]")
                return False, "Deployment rejected by engineer during Human-in-the-Loop approval step."
            
            console.print("[bold green]Deployment Approved by Engineer! Converting to interactive user script...[/bold green]")
            self.memory.log_event("HeadCoordinator", "Deployment approved by engineer.")

        # Save verified program to workspace directory under a subfolder per program using name from plan
        import re
        from settings import ROOT_DIR
        
        plan_filename = plan.get("file_name", "verified_program").strip()
        plan_filename = re.sub(r'[^a-zA-Z0-9_]', '', plan_filename)
        if not plan_filename:
            plan_filename = "program"
            
        workspace_dir = ROOT_DIR / "workspace" / plan_filename
        workspace_dir.mkdir(parents=True, exist_ok=True)
            
        source_filename, _ = get_agent_filenames(plan)
        dest_filename = source_filename
        dest_filepath = workspace_dir / dest_filename
        
        # Run DeployAgent to rewrite the code to take interactive inputs
        from settings import ENABLE_DEPLOY
        if ENABLE_DEPLOY:
            console.print("[yellow]Deploy Agent: Generating interactive prompt wrapper...[/yellow]")
            try:
                interactive_code = await self.deployer.make_interactive(source_code, language)
            except Exception as deploy_err:
                console.print(f"[yellow]Warning: Could not generate interactive wrapper: {deploy_err}. Deploying non-interactive function...[/yellow]")
                interactive_code = source_code
        else:
            console.print("[yellow]Deploy Agent: Interactive prompt wrapper disabled. Deploying raw non-interactive code...[/yellow]")
            interactive_code = source_code

        # Write test file as well
        _, test_filename = get_agent_filenames(plan)
        dest_test_filepath = workspace_dir / test_filename
        
        # Save plan JSON
        import json
        plan_json_filepath = workspace_dir / f"{plan_filename}_plan.json"

        try:
            # 1. Save interactive implementation code
            with open(dest_filepath, "w", encoding="utf-8") as f:
                f.write(interactive_code)
            self.memory.log_event("HeadCoordinator", f"Saved interactive verified script to workspace/{plan_filename}/{dest_filename}")
            console.print(f"[bold green]Saved interactive verified script to workspace/{plan_filename}/{dest_filename}[/bold green]")
            
            # 2. Save test suite code
            if test_code:
                with open(dest_test_filepath, "w", encoding="utf-8") as f:
                    f.write(test_code)
                self.memory.log_event("HeadCoordinator", f"Saved verified test suite to workspace/{plan_filename}/{test_filename}")
                console.print(f"[bold green]Saved verified test suite to workspace/{plan_filename}/{test_filename}[/bold green]")
                
            # 3. Save system plan JSON
            with open(plan_json_filepath, "w", encoding="utf-8") as f:
                json.dump(plan, f, indent=4)
            self.memory.log_event("HeadCoordinator", f"Saved system plan JSON to workspace/{plan_filename}/{plan_json_filepath.name}")
            console.print(f"[bold green]Saved system plan JSON to workspace/{plan_filename}/{plan_json_filepath.name}[/bold green]")
            
        except Exception as save_err:
            self.memory.log_event("HeadCoordinator", f"Failed to save verified files to workspace/{plan_filename}: {save_err}")
            console.print(f"[yellow]Warning: Could not copy verified files to workspace/{plan_filename}: {save_err}[/yellow]")

        # 6. Final Sandbox Execution
        with console.status("[bold yellow]Executing Agent: Running finalized implementation program inside VM...[/bold yellow]", spinner="aesthetic"):
            exec_success, exec_report = await self.executor.execute_final(language)
        
        self.memory.update_state("status", "completed" if exec_success else "execution_failed")

        # Learn from mistakes if we had correction loops
        loop_count = self.memory.state.get("loop_count", 0)
        if exec_success and loop_count > 0:
            console.print("[yellow]Learning Agent: Analyzing mistakes to update lessons learned database...[/yellow]")
            try:
                # Get the logs of what failed and how it was fixed
                logs = self.memory.state.get("logs", [])
                error_traces = []
                for entry in logs:
                    if "Linter issues found" in entry["message"] or "Execution error trace" in entry["message"]:
                        error_traces.append(entry["message"])
                
                if error_traces:
                    recent_errors = "\n\n".join(error_traces[-2:])
                    prompt = (
                        f"Requirements Description: {description}\n\n"
                        f"The implementation failed initially with the following errors/violations:\n"
                        f"{recent_errors}\n\n"
                        f"Here is the final corrected code that passed all lint checks and unit tests:\n"
                        f"{source_code}\n\n"
                        f"Please identify the main bug or violation that caused the failure, and summarize it as a lesson learned in a JSON object with exactly two keys:\n"
                        f"1. 'mistake': A short, clear description of the bug/mistake.\n"
                        f"2. 'correction': A short rule or explanation of how to code it correctly to avoid this mistake.\n"
                        f"Return ONLY the raw JSON object. Do not include markdown backticks or any other text."
                    )
                    from llm import async_query_llm
                    raw_lesson = await async_query_llm(prompt, system_instruction="You are a senior safety compiler engineer. Summarize lessons learned in raw JSON format.")
                    
                    import json
                    clean_lesson = raw_lesson.strip()
                    start_idx = clean_lesson.find("{")
                    end_idx = clean_lesson.rfind("}")
                    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                        clean_lesson = clean_lesson[start_idx:end_idx + 1]
                    
                    lesson_data = json.loads(clean_lesson)
                    self.memory.save_lesson(lesson_data)
                    console.print(f"[bold green]Saved new lesson learned: {lesson_data.get('mistake')}[/bold green]")
            except Exception as e:
                console.print(f"[yellow]Warning: Could not save lesson learned: {e}[/yellow]")

        if exec_success:
            report_msg = f"{exec_report}\n\n[SUCCESS] Verified interactive script is stored at:\n{dest_filepath.resolve()}"
            return True, report_msg
        else:
            report_msg = f"{exec_report}\n\n[FAILURE] Final execution check failed. Error details:\n{exec_report}"
            return False, report_msg
    async def _install_missing_package(self, module_name: str) -> bool:
        """Install a missing Python package by name, with common alias remapping."""
        import subprocess
        from settings import get_python_exe
        alias_map = {
            "sklearn": "scikit-learn",
            "cv2": "opencv-python",
            "yaml": "pyyaml",
        }
        pkg_name = alias_map.get(module_name, module_name)
        console.print(f"[yellow]Automatically installing missing dependency: {pkg_name}...[/yellow]")
        try:
            python_exe = get_python_exe()
            res = subprocess.run(
                [python_exe, "-m", "pip", "install", pkg_name],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60
            )
            if res.returncode == 0:
                console.print(f"[bold green]Successfully installed {pkg_name}! Retrying...[/bold green]")
                return True
            else:
                console.print(f"[bold red]Failed to install {pkg_name}.[/bold red]")
                return False
        except Exception as install_err:
            console.print(f"[bold red]Error installing {pkg_name}: {install_err}[/bold red]")
            return False
