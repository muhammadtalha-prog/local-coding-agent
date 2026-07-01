import os
import sys
import time
import json
import logging
import asyncio
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.logging import RichHandler

# Ensure project root is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Set up logging (mirrors cli.py)
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[
        RichHandler(rich_tracebacks=True, show_level=False, show_path=False),
        logging.FileHandler("agent_system.log", mode="a", encoding="utf-8")
    ]
)

import settings
from head import HeadCoordinator

console = Console()

# Define the 10 benchmark test cases
BENCHMARK_CASES = [
    {
        "id": "py_bst",
        "language": "python",
        "description": "Create a class BinarySearchTree with methods insert(value), search(value) returning a boolean, and delete(value). All methods must be fully type-hinted and include input validation to ensure the tree only accepts integers."
    },
    {
        "id": "py_stack_queue",
        "language": "python",
        "description": "Implement a Stack class with push, pop, and peek, and a Queue class with enqueue, dequeue, and size. Ensure both have appropriate bounds checks and raise UnderflowError (custom exception) when popping/dequeuing from empty structures."
    },
    {
        "id": "py_csv_averager",
        "language": "python",
        "description": "Write a program that reads a CSV file containing columns 'Name', 'Department', and 'Salary'. Filter for rows where Department is 'Engineering', compute the average salary of those engineers, and write the average salary to a text file named 'average_salary.txt'. Handle potential FileNotFoundError and ValueError for invalid data gracefully."
    },
    {
        "id": "py_primes",
        "language": "python",
        "description": "Write a function get_primes_in_range(start, end) that returns a list of all prime numbers between start and end inclusive. Raise ValueError if start or end are negative or if start > end."
    },
    {
        "id": "py_matrix",
        "language": "python",
        "description": "Implement matrix addition, matrix multiplication, and matrix transpose functions without using numpy or any external library. Add checks to ensure the dimensions are compatible for these operations, raising ValueError if not."
    },
    {
        "id": "matlab_sine",
        "language": "matlab",
        "description": "Generate a sine wave signal with frequency f, amplitude a, and duration d at a sampling rate fs. Do not use any licensed toolbox functions. The main function should match the filename and accept t, f, a, fs as inputs and return the signal vector."
    },
    {
        "id": "matlab_butter",
        "language": "matlab",
        "description": "Implement a digital low-pass Butterworth filter manually using the bilinear transform. Do not use the butter or filtfilt functions from the Signal Processing Toolbox. The function should accept input vector x, cutoff frequency fc, and sampling rate fs, and return the filtered signal."
    },
    {
        "id": "matlab_pid",
        "language": "matlab",
        "description": "Simulate a continuous PID controller loop. The function pid_control_sim(kp, ki, kd, setpoint, time_vector) should return the controlled process variable over time. Do not use Control System Toolbox functions like pid, tf, or lsim."
    },
    {
        "id": "matlab_rk4",
        "language": "matlab",
        "description": "Solve a first-order ordinary differential equation dy/dt = -2*y + sin(t) with initial condition y(0)=1 over a given time span using the Runge-Kutta 4th order (RK4) method. The main function should return the time steps and solved y values."
    },
    {
        "id": "matlab_coord",
        "language": "matlab",
        "description": "Create functions to convert Cartesian coordinates (x, y, z) to Spherical coordinates (r, theta, phi) and vice versa. Implement input validations for zero radius and angle bounds."
    }
]


async def run_benchmark():
    console.print(Panel("[bold blue]Local Coding Agent - Evaluation Benchmarking System[/bold blue]", expand=False))
    
    # 1. Force configuration values for programmatic bench runs
    settings.AUTO_APPROVE = True
    # Allow host fallback during benchmark runs to guarantee execution in all test host environments
    settings.HOST_FALLBACK_ALLOWED = True
    
    results = {}
    history_file = "benchmark_results.json"
    previous_results = {}
    
    if os.path.exists(history_file):
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                previous_results = json.load(f)
            console.print(f"[cyan]Loaded historical benchmark results from {history_file} for comparison.[/cyan]\n")
        except Exception as e:
            console.print(f"[yellow]Warning: Could not load {history_file}: {e}[/yellow]\n")
            
    # Run cases
    for case in BENCHMARK_CASES:
        case_id = case["id"]
        console.print(f"[bold yellow]=== Running Benchmark Case: {case_id} ({case['language'].upper()}) ===[/bold yellow]")
        console.print(f"[dim]Description: {case['description']}[/dim]")
        
        start_time = time.monotonic()
        success = False
        error_msg = ""
        loop_count = 0
        
        coordinator = HeadCoordinator(session_id=f"bench_{case_id}")
        
        try:
            success, report = await coordinator.orchestrate(
                description=case["description"],
                language=case["language"]
            )
            loop_count = coordinator.memory.state.get("loop_count", 0)
        except Exception as e:
            success = False
            error_msg = str(e)
            console.print(f"[bold red]Exception during execution of {case_id}: {e}[/bold red]")
            
        duration = time.monotonic() - start_time
        
        results[case_id] = {
            "id": case_id,
            "language": case["language"],
            "success": success,
            "loop_count": loop_count,
            "duration": round(duration, 2),
            "error": error_msg
        }
        
        status_color = "green" if success else "red"
        status_text = "PASSED" if success else "FAILED"
        console.print(f"[{status_color}]Result: {status_text} | Duration: {duration:.2f}s | Debug Loops: {loop_count}[/{status_color}]")
        console.print("-" * 50 + "\n")
        
    # Write current results
    try:
        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=4)
        console.print(f"[green]Saved current benchmark results to {history_file}[/green]\n")
    except Exception as e:
        console.print(f"[red]Error saving benchmark results: {e}[/red]\n")
        
    # 2. Render comparative summary table
    table = Table(title="Local Coding Agent - Benchmark Summary Report")
    table.add_column("Case ID", style="cyan")
    table.add_column("Language", style="magenta")
    table.add_column("Status", style="bold")
    table.add_column("Loops (Current)", justify="right")
    table.add_column("Loops (Delta)", justify="right")
    table.add_column("Time (Current)", justify="right")
    table.add_column("Time (Delta)", justify="right")
    
    for case_id in results:
        curr = results[case_id]
        prev = previous_results.get(case_id, {})
        
        status_text = "[bold green]PASS[/bold green]" if curr["success"] else "[bold red]FAIL[/bold red]"
        
        # Calculate deltas
        if prev:
            loop_delta = curr["loop_count"] - prev.get("loop_count", 0)
            time_delta = curr["duration"] - prev.get("duration", 0.0)
            
            loop_delta_str = f"{loop_delta:+d}" if loop_delta != 0 else "0"
            time_delta_str = f"{time_delta:+.2f}s" if abs(time_delta) > 0.01 else "0.00s"
            
            if loop_delta > 0:
                loop_delta_str = f"[red]{loop_delta_str}[/red]"
            elif loop_delta < 0:
                loop_delta_str = f"[green]{loop_delta_str}[/green]"
                
            if time_delta > 0.5:
                time_delta_str = f"[red]{time_delta_str}[/red]"
            elif time_delta < -0.5:
                time_delta_str = f"[green]{time_delta_str}[/green]"
        else:
            loop_delta_str = "N/A"
            time_delta_str = "N/A"
            
        table.add_row(
            curr["id"],
            curr["language"].upper(),
            status_text,
            str(curr["loop_count"]),
            loop_delta_str,
            f"{curr['duration']:.2f}s",
            time_delta_str
        )
        
    console.print(table)


if __name__ == "__main__":
    asyncio.run(run_benchmark())
