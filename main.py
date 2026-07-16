"""
main.py — MATLAB Code Generation Agent Entry Point

Usage:
  python main.py --task "Generate a PID controller function"
  python main.py                          # interactive prompt mode
  python main.py --task "..." --model qwen2.5-coder:1.5b

Pipeline:
  1. Planner   → JSON plan
  2. Coder     → .m file
  3. Executor  → MATLAB -batch run
  4. Debugger  → fix & retry (max MAX_DEBUG_RETRIES times)
  5. Save      → workspace/<task_name>/

Designed for 8GB RAM + 1.5-3B local LLM via Ollama.
"""
import argparse
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path

if sys.platform.startswith("win"):
    # Force UTF-8 output so Rich unicode chars don't crash the legacy Windows console
    import io as _io
    sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = _io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.text import Text

# ── Project imports ──────────────────────────────────────────────────────────
from config import (
    AUTO_APPROVE,
    MAX_DEBUG_RETRIES,
    MATLAB_EXE,
    OLLAMA_MODEL,
    ROOT_DIR,
    WORKSPACE_DIR,
)
from agents.llm_client import check_ollama_health
from agents.planner import generate_plan
from agents.coder import generate_code
from agents.debugger import fix_code
from agents.matlab_executor import MatlabExecutor

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(ROOT_DIR / "agent.log", encoding="utf-8")],
)
logger = logging.getLogger("matlab_agent.main")

console = Console()
executor = MatlabExecutor()


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline(task: str) -> bool:
    """
    Execute the full Planner → Coder → Executor → Debugger pipeline.
    Returns True on success, False on failure.
    """
    start_time = time.monotonic()
    console.print(Rule("[bold cyan]MATLAB Code Generation Agent[/bold cyan]"))
    console.print(f"[dim]Task:[/dim] {task}\n")

    # ── Pre-flight: check Ollama ──────────────────────────────────────────────
    console.print("[yellow]>> Checking Ollama connection...[/yellow]")
    ok, msg = check_ollama_health()
    if not ok:
        console.print(Panel(f"[bold red]Ollama Not Ready[/bold red]\n\n{msg}", border_style="red"))
        return False
    console.print(f"[green]OK  {msg}[/green]\n")

    # ── Step 1: Planning ──────────────────────────────────────────────────────
    console.print("[yellow]>> [1/3] Planner Agent: Designing MATLAB function...[/yellow]")
    try:
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                      transient=True, console=console) as progress:
            progress.add_task("Planning...", total=None)
            plan = generate_plan(task)

    except (ValueError, RuntimeError, TimeoutError) as e:
        console.print(Panel(f"[bold red]Planning Failed[/bold red]\n\n{e}", border_style="red"))
        logger.error("Planning failed: %s", e)
        return False

    console.print(f"[green]OK  Plan ready:[/green] [bold]{plan['file_name']}.m[/bold]")
    console.print(f"   [dim]{plan['description']}[/dim]\n")

    # ── Step 2: Code Generation ───────────────────────────────────────────────
    console.print("[yellow]>> [2/3] Coder Agent: Generating MATLAB code...[/yellow]")
    try:
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                      transient=True, console=console) as progress:
            progress.add_task("Generating code...", total=None)
            matlab_file, source_code = generate_code(plan)

    except (ValueError, RuntimeError, TimeoutError) as e:
        console.print(Panel(f"[bold red]Code Generation Failed[/bold red]\n\n{e}", border_style="red"))
        logger.error("Code generation failed: %s", e)
        return False

    console.print(f"[green]OK  Code written to sandbox:[/green] [bold]{matlab_file.name}[/bold]\n")

    # ── Step 3: Execute + Debug loop ─────────────────────────────────────────
    console.print("[yellow]>> [3/3] Executing & Verifying MATLAB code...[/yellow]\n")

    success = False
    error_output = ""

    for attempt in range(MAX_DEBUG_RETRIES + 1):
        if attempt > 0:
            console.print(
                f"[yellow]  >> Debugger Attempt {attempt}/{MAX_DEBUG_RETRIES}...[/yellow]"
            )

        # Execute
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                      transient=True, console=console) as progress:
            label = "Running matlab -batch..." if attempt == 0 else "Re-running after fix..."
            progress.add_task(label, total=None)
            exec_ok, exec_out = executor.execute_file(matlab_file)

        error_output = exec_out

        if exec_ok or "MATLAB_NOT_INSTALLED" in exec_out:
            if "MATLAB_NOT_INSTALLED" in exec_out:
                success = True
                console.print(f"[green]OK  MATLAB not installed. Skipping test validation.[/green]")
                break

            # Run test_call if MATLAB is available
            test_ok = True
            test_out = ""
            if MATLAB_EXE and plan.get("test_call"):
                console.print(f"\n[yellow]  ▶  Running test call: [code]{plan['test_call']}[/code][/yellow]")
                test_ok, test_out = executor.run_test_call(plan["file_name"], plan["test_call"])

            if test_ok:
                success = True
                console.print(f"[green]OK  Execution & Test call passed![/green]")
                if test_out.strip():
                    preview = test_out[-300:].strip()
                    console.print(Panel(preview, title="MATLAB Output", border_style="green"))
                break
            else:
                console.print(f"[red]FAIL  Test call failed: {plan['test_call']}[/red]")
                # Capture the test call error in error_output to pass to the debugger
                error_output = f"ERROR: Test call '{plan['test_call']}' failed with:\n{test_out}"

        # Check error type — only toolbox errors are immediately fatal
        is_timeout = "timed out" in error_output.lower() or "ERROR: MATLAB timed out" in error_output
        if is_timeout:
            console.print(
                "[yellow]  >> MATLAB startup/execution timed out.\n"
                "  Tip: Increase MATLAB_EXEC_TIMEOUT_SEC in .env (currently 120s).\n"
                "  Retrying...[/yellow]"
            )
            # Timeout is retriable — don't call debugger, just re-run
            continue

        if MatlabExecutor.is_toolbox_error(error_output):
            console.print(
                "[bold red]FAIL  Toolbox function detected - cannot fix without a licensed MATLAB toolbox.[/bold red]\n"
                "[yellow]  Hint: Rephrase your task to avoid: butter, filtfilt, tf, lsim, ss, bode, step[/yellow]"
            )
            logger.error("Toolbox error - pipeline halted.")
            return False

        if attempt >= MAX_DEBUG_RETRIES:
            console.print(f"[bold red]FAIL  Max debug retries ({MAX_DEBUG_RETRIES}) reached.[/bold red]")
            break

        # Call debugger
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                      transient=True, console=console) as progress:
            progress.add_task("Fixing code...", total=None)
            fixable, matlab_file, source_code = fix_code(plan, source_code, error_output)

        if not fixable:
            console.print("[bold red]FAIL  Debugger: Error is unfixable (toolbox dependency).[/bold red]")
            return False

        console.print(f"[cyan]  OK  Fix applied - retrying execution...[/cyan]")

    if not success:
        console.print("[bold red]\nFAIL  Pipeline failed after all retries.[/bold red]")
        logger.error("Pipeline failed for task: %s", task)
        return False

    # ── Step 4: Save to workspace ─────────────────────────────────────────────
    _save_to_workspace(plan, matlab_file, source_code, task)

    elapsed = time.monotonic() - start_time
    console.print(Rule())
    console.print(f"[bold green]DONE  in {elapsed:.1f}s[/bold green]")
    return True


def _save_to_workspace(
    plan: dict,
    matlab_file: Path,
    source_code: str,
    task: str,
) -> None:
    """Copy ONLY the verified .m file to workspace/<file_name>/. No JSON saved to disk."""
    safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", plan["file_name"])
    dest_dir = WORKSPACE_DIR / safe_name
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Copy .m file only
    dest_m = dest_dir / matlab_file.name
    shutil.copy2(matlab_file, dest_m)

    console.print(f"\n[bold green]OK  Saved to workspace:[/bold green]")
    console.print(f"   >> {dest_m.resolve()}")
    logger.info("Saved verified .m to: %s", dest_m)


# ─────────────────────────────────────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="MATLAB Code Generation Agent — powered by local Ollama LLM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --task "Generate a moving average filter function"
  python main.py --task "Create a PID controller for a DC motor" --model qwen2.5-coder:1.5b
  python main.py                        # interactive mode
        """,
    )
    parser.add_argument(
        "--task", "-t",
        type=str,
        default=None,
        help="Task description for the MATLAB function to generate.",
    )
    parser.add_argument(
        "--model", "-m",
        type=str,
        default=None,
        help=f"Ollama model to use (default: {OLLAMA_MODEL}). E.g. qwen2.5-coder:1.5b",
    )
    parser.add_argument(
        "--retries", "-r",
        type=int,
        default=None,
        help=f"Max debug retries (default: {MAX_DEBUG_RETRIES}).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="MATLAB execution timeout in seconds (default: from config).",
    )
    return parser.parse_args()


def _print_banner() -> None:
    banner = Text()
    banner.append("  MATLAB Code Generation Agent  \n", style="bold cyan")
    banner.append(f"  Model : {OLLAMA_MODEL}\n",         style="dim")
    banner.append(f"  MATLAB: {MATLAB_EXE or 'not found (generation only)'}\n", style="dim")
    banner.append(f"  Output: {WORKSPACE_DIR}\n",        style="dim")
    console.print(Panel(banner, border_style="cyan"))


def main() -> None:
    """Main entry point — handles both CLI and interactive modes."""
    args = _parse_args()

    # Apply CLI overrides to environment (config.py reads from os.environ)
    if args.model:
        os.environ["OLLAMA_MODEL"] = args.model
    if args.retries is not None:
        os.environ["MAX_DEBUG_RETRIES"] = str(args.retries)
    if args.timeout is not None:
        os.environ["MATLAB_EXEC_TIMEOUT_SEC"] = str(args.timeout)

    _print_banner()

    if args.task:
        # Single-shot mode
        ok = run_pipeline(args.task)
        sys.exit(0 if ok else 1)
    else:
        # Interactive loop
        console.print("[dim]No --task provided. Entering interactive mode. Type 'quit' to exit.[/dim]\n")
        while True:
            try:
                task = console.input("[bold cyan]Task>[/bold cyan] ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Goodbye.[/dim]")
                break

            if not task:
                continue
            if task.lower() in ("quit", "exit", "q"):
                console.print("[dim]Goodbye.[/dim]")
                break

            run_pipeline(task)
            console.print()


if __name__ == "__main__":
    main()
