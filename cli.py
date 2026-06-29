import sys
import logging
import click
import io
import asyncio
from rich.console import Console
from rich.panel import Panel
from rich.logging import RichHandler

# Set UTF-8 encoding for stdout/stderr to prevent UnicodeEncodeErrors on Windows
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

# Set up logging before configuring imports that log
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[
        RichHandler(rich_tracebacks=True, show_level=False, show_path=False),
        logging.FileHandler("agent_system.log", mode="a", encoding="utf-8")
    ]
)

# Import setting overrides and coordinator
import settings  # noqa: E402
from head import HeadCoordinator  # noqa: E402



console = Console()
error_console = Console(stderr=True)

@click.command()
@click.option(
    "--description", "-d",
    prompt="Enter software description/requirements",
    help="Text description of the program/system to build."
)
@click.option(
    "--session", "-s",
    default="avionics_session",
    help="Unique session ID to persist memory state."
)
@click.option(
    "--timeout", "-t",
    type=float,
    default=None,
    help="Subprocess run timeout in seconds (default: None for no timeout)."
)
@click.option(
    "--max-loops", "-l",
    type=int,
    default=settings.MAX_DEBUG_LOOPS,
    help="Maximum debug and fix loop attempts."
)
@click.option(
    "--language",
    default="python",
    type=click.Choice(["python", "matlab"], case_sensitive=False),
    help="Target programming language (python or matlab)."
)
def main(description: str, session: str, timeout: float, max_loops: int, language: str):
    """
    Military/Avionics-grade Multi-Agent System CLI.
    Generates, lints, verifies, and executes code safely.
    """
    # Validate LLM server/API availability at startup
    primary = settings.LLM_PROVIDER.lower()
    if primary == "groq":
        if not settings.GROQ_API_KEY:
            error_console.print("[bold red]ERROR: Groq API key is not configured![/bold red]")
            error_console.print("Please add GROQ_API_KEY=your_key to your .env file.")
            sys.exit(1)
        # Warn if HF fallback is also unavailable
        if not settings.HF_API_TOKEN:
            console.print("[yellow]⚠ HF_API_TOKEN not set — HuggingFace fallback unavailable. The system will fail if Groq is rate-limited.[/yellow]")
    elif primary == "gemini":
        if not settings.GEMINI_API_KEY:
            error_console.print("[bold red]ERROR: Gemini API key is not configured![/bold red]")
            error_console.print("Please add GEMINI_API_KEY=your_key to your .env file.")
            sys.exit(1)
    elif primary == "huggingface":
        if not settings.HF_API_TOKEN:
            error_console.print("[bold red]ERROR: HuggingFace API token is not configured![/bold red]")
            error_console.print("Please add HF_API_TOKEN=your_token to your .env file.")
            sys.exit(1)



    # Validate Python dev tools availability (ruff, mypy, pytest) in virtual environment
    if not settings.DOCKER_ENABLED:
        python_exe = settings.get_python_exe()
        missing_tools = []
        import subprocess
        for tool in ["ruff", "mypy", "pytest"]:
            try:
                res = subprocess.run([python_exe, "-m", tool, "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
                if res.returncode != 0:
                    missing_tools.append(tool)
            except Exception:
                missing_tools.append(tool)
        if missing_tools:
            error_console.print(f"[bold red]ERROR: Missing required Python tools in your virtual environment: {', '.join(missing_tools)}[/bold red]")
            error_console.print(f"Please install them using: {python_exe} -m pip install {' '.join(missing_tools)}")
            sys.exit(1)


    # Apply override configurations
    settings.DEFAULT_TIMEOUT_SEC = timeout
    settings.MAX_DEBUG_LOOPS = max_loops

    console.print(Panel(
        f"[bold blue]Multi-Agent Code Generation System[/bold blue]\n\n"
        f"Session ID:       {session}\n"
        f"LLM Provider:     {settings.LLM_PROVIDER.upper()}\n"
        f"Target Language:  {language.upper()}\n"
        f"Max Debug Loops:  {max_loops}\n"
        f"Timeout:          {timeout or 'None'} seconds",
        title="Configuration"
    ))

    try:
        coordinator = HeadCoordinator(session_id=session)
        # Execute the orchestrator asynchronously
        success, report = asyncio.run(coordinator.orchestrate(description, language=language))
        
        if success:
            console.print(Panel(
                f"[bold green]Final Sandboxed Execution Output:[/bold green]\n\n{report}",
                title="System Success Output",
                border_style="green"
            ))
            sys.exit(0)
        else:
            console.print(Panel(
                f"[bold red]Execution Failure Report:[/bold red]\n\n{report}",
                title="System Halt/Failure Output",
                border_style="red"
            ))
            sys.exit(1)
            
    except Exception as e:
        error_console.print(f"[bold red]Fatal system error occurred:[/bold red] {e}")
        import traceback
        error_console.print(traceback.format_exc())
        sys.exit(2)

if __name__ == "__main__":
    main()
