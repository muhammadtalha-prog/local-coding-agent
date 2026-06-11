import sys
import os
import argparse
import json
import time
from service_agent import CodingAgentService

def print_banner():
    print("==================================================")
    print("🤖 Local Coding Agent - Background Service CLI 🤖")
    print("==================================================")

def execute_single_task(description: str, test_cmd: str, language: str, max_iterations: int, export_path: str):
    print(f"\n🚀 Launching single task: {description[:50]}...")
    service = CodingAgentService(num_workers=1)
    service.start_service()
    
    task_id = service.add_task(
        description=description,
        test_command=test_cmd,
        language=language,
        max_iterations=max_iterations,
        export_path=export_path
    )
    
    while True:
        status = service.get_task_status(task_id)
        current_status = status.get("status")
        print(f"🕒 Task {task_id} status: {current_status}")
        if current_status in ("PASSED", "FAILED", "ERROR"):
            print(f"\n🏁 Task Finished!")
            print(f"Status: {current_status}")
            print(f"Iterations: {status.get('iterations')}")
            print(f"Errors: {status.get('errors')}")
            print(f"Generated Files: {status.get('files')}")
            break
        time.sleep(2)
        
    service.shutdown()

def execute_batch_tasks(batch_file: str, concurrency: int):
    if not os.path.exists(batch_file):
        print(f"❌ Error: Batch file {batch_file} does not exist.")
        sys.exit(1)
        
    try:
        with open(batch_file, 'r', encoding='utf-8') as f:
            tasks = json.load(f)
    except Exception as e:
        print(f"❌ Error reading batch file: {e}")
        sys.exit(1)
        
    if not isinstance(tasks, list):
        print("❌ Error: Batch file must contain a JSON array of tasks.")
        sys.exit(1)
        
    print(f"\n🚀 Launching batch of {len(tasks)} tasks with concurrency = {concurrency}...")
    service = CodingAgentService(num_workers=concurrency)
    service.start_service()
    
    task_ids = []
    for task in tasks:
        description = task.get("description")
        test_cmd = task.get("test_command", "")
        language = task.get("language", "python")
        max_iterations = task.get("max_iterations", 8)
        export_path = task.get("export_path")
        
        tid = service.add_task(
            description=description,
            test_command=test_cmd,
            language=language,
            max_iterations=max_iterations,
            export_path=export_path
        )
        task_ids.append(tid)
        
    # Wait for all tasks to complete
    completed = set()
    while len(completed) < len(task_ids):
        for tid in task_ids:
            if tid in completed:
                continue
            status = service.get_task_status(tid)
            current_status = status.get("status")
            if current_status in ("PASSED", "FAILED", "ERROR"):
                completed.add(tid)
                print(f"✅ Batch Task {tid} finished with status: {current_status}")
        time.sleep(3)
        
    print("\n🏁 All batch tasks completed. Saving batch summary to batch_results.json...")
    results = {}
    for tid in task_ids:
        results[tid] = service.get_task_status(tid)
        
    with open("batch_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
        
    service.shutdown()
    print("Results written to batch_results.json.")

def main():
    print_banner()
    
    parser = argparse.ArgumentParser(description="Stateful Service Coding Agent Launcher CLI")
    parser.add_argument("description", nargs="?", help="Project task description (optional if --batch is used)")
    parser.add_argument("--test-cmd", default="", help="Command to execute for task testing/verification")
    parser.add_argument("--language", default="python", choices=["python", "matlab", "m", "generic"], help="Execution runtime environment")
    parser.add_argument("--iterations", type=int, help="Override maximum iterations")
    parser.add_argument("--export-path", help="Folder path to copy final files on success")
    
    # Task complexity flags
    parser.add_argument("--small", action="store_true", help="Set options for a small, simple task (iterations=3)")
    parser.add_argument("--large", action="store_true", help="Set options for a large, complex task (iterations=8)")
    
    # Batch run options
    parser.add_argument("--batch", help="Path to JSON file containing a list of tasks for batch processing")
    parser.add_argument("--concurrency", type=int, default=3, help="Number of parallel workers for batch tasks")
    
    args = parser.parse_args()
    
    # Determine iterations limit based on complexity flags
    max_iterations = 8
    if args.small:
        max_iterations = 3
    elif args.large:
        max_iterations = 8
        
    if args.iterations:
        max_iterations = args.iterations
        
    if args.batch:
        execute_batch_tasks(args.batch, args.concurrency)
    else:
        # Prompt if single task description is not provided in args
        description = args.description
        if not description:
            print("\nEnter project description:")
            description = input("> ").strip()
            if not description:
                print("❌ Error: Description is required.")
                sys.exit(1)
                
            print("\nEnter test/run command (e.g. 'python test.py' or 'matlab -batch script_name'):")
            test_cmd = input("> ").strip()
            
            print("\nEnter language type (python/matlab):")
            language = input("> ").strip().lower() or "python"
            
            print("\nEnter export path (optional, press Enter to skip):")
            export_path = input("> ").strip() or None
        else:
            description = args.description
            test_cmd = args.test_cmd
            language = args.language
            export_path = args.export_path
            
        execute_single_task(
            description=description,
            test_cmd=test_cmd,
            language=language,
            max_iterations=max_iterations,
            export_path=export_path
        )

if __name__ == "__main__":
    main()
