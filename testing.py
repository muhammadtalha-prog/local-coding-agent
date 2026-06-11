import os
import sys
import shutil

# Ensure local folder is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import WORKSPACE_DIR
from agent import graph
from tools import list_workspace_files

def clear_workspace():
    print("🧹 Cleaning workspace...")
    if not os.path.exists(WORKSPACE_DIR):
        os.makedirs(WORKSPACE_DIR)
        return
    for item in os.listdir(WORKSPACE_DIR):
        item_path = os.path.join(WORKSPACE_DIR, item)
        if item == "venv" or item == ".git":
            continue
        try:
            if os.path.isdir(item_path):
                shutil.rmtree(item_path)
            else:
                os.remove(item_path)
        except Exception as e:
            print(f"Warning: Could not remove {item}: {e}")

def main():
    clear_workspace()
    
    task_desc = (
        "Build a Python library management system. Create library_system.py which includes Book, User, and Library classes. "
        "Library should support adding books, registering users, borrowing books, returning books, and listing available books. "
        "Also create a test script test_library.py with unit tests for these operations, ensuring that running test_library.py runs successfully."
    )
    
    print(f"🚀 Starting task: {task_desc}\n")
    
    initial_state = {
        "description": task_desc,
        "files": list_workspace_files(),
        "messages": [],
        "iterations": 0,
        "max_iterations": 5,
        "errors": "",
        "test_results": []
    }
    
    try:
        final_state = graph.invoke(initial_state)
        print("\n🎉 Agent Execution Finished!")
        print(f"Total iterations taken: {final_state.get('iterations', 0)}")
        print(f"Final error state: {repr(final_state.get('errors', ''))}")
        print("Generated files in workspace:")
        for f in list_workspace_files():
            print(f" - {f}")
    except Exception as e:
        print(f"\n❌ Error during graph execution: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
