import os
import queue
import threading
import uuid
import json
import time
import re
from typing import Dict, Any, List
from agent import graph
from tools import list_workspace_files
from external_software import TaskStatusRegistry

class CodingAgentService:
    """
    Manages parallel execution of coding agent instances using a task queue,
    thread pool, and isolated workspace environments.
    """
    def __init__(self, num_workers: int = 3, history_file: str = "task_history.json"):
        self.num_workers = num_workers
        self.history_file = history_file
        self.task_queue = queue.Queue()
        self.workers = []
        self.running = False
        
        self.tasks_status = {}  # task_id -> task status info dict
        self.lock = threading.Lock()
        
        # Load existing task history if available
        self._load_history()

    def _load_history(self):
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    self.tasks_status = json.load(f)
            except Exception:
                self.tasks_status = {}

    def _save_history(self):
        with self.lock:
            try:
                # Prune task history to prevent file from growing indefinitely
                max_history = int(os.getenv("MAX_TASK_HISTORY", "100"))
                if len(self.tasks_status) > max_history:
                    # Sort tasks by creation timestamp (created_at) ascending
                    sorted_tasks = sorted(self.tasks_status.items(), key=lambda x: x[1].get("created_at", 0))
                    # Keep only the last max_history items
                    self.tasks_status = dict(sorted_tasks[-max_history:])
                
                with open(self.history_file, 'w', encoding='utf-8') as f:
                    json.dump(self.tasks_status, f, indent=2)
            except Exception as e:
                print(f"Error saving task history: {e}")

    def start_service(self):
        """
        Starts the background worker threads and the watchdog thread.
        """
        if self.running:
            return
        self.running = True
        self.workers = []
        for i in range(self.num_workers):
            t = threading.Thread(target=self._worker_loop, name=f"AgentWorker-{i}")
            t.daemon = True
            t.start()
            self.workers.append(t)
            
        # Start watchdog thread for resource limit and heartbeat monitoring
        self.watchdog_thread = threading.Thread(target=self._watchdog_loop, name="AgentWatchdog")
        self.watchdog_thread.daemon = True
        self.watchdog_thread.start()
        print(f"🚀 CodingAgentService started with {self.num_workers} parallel workers and watchdog monitoring.")

    def shutdown(self):
        """
        Signals all worker threads to stop and waits for completion.
        """
        self.running = False
        # Push None sentinels for each worker to unblock queue.get()
        for _ in range(self.num_workers):
            self.task_queue.put(None)
        for t in self.workers:
            t.join()
        if hasattr(self, 'watchdog_thread') and self.watchdog_thread:
            self.watchdog_thread.join(timeout=2)
        print("🛑 CodingAgentService shut down successfully.")

    def add_task(self, description: str, test_command: str, language: str, max_iterations: int = 8, export_path: str = None) -> str:
        """
        Queues a new task for parallel execution. Returns a unique task_id.
        """
        task_id = str(uuid.uuid4())[:8]
        task_info = {
            "task_id": task_id,
            "description": description,
            "test_command": test_command,
            "language": language,
            "max_iterations": max_iterations,
            "export_path": export_path,
            "status": "QUEUED",
            "iterations": 0,
            "errors": "",
            "files": [],
            "created_at": time.time(),
            "started_at": None,
            "completed_at": None,
            "workspace_dir": None
        }
        
        with self.lock:
            self.tasks_status[task_id] = task_info
        self._save_history()
        
        self.task_queue.put(task_info)
        print(f"📥 Queued Task {task_id}: {description[:40]}...")
        return task_id

    def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """
        Returns the current execution details and progress of a task.
        """
        with self.lock:
            return self.tasks_status.get(task_id, {"error": "Task not found."})

    def cancel_task(self, task_id: str):
        """
        Cancels a running or queued task, terminating active subprocesses.
        """
        TaskStatusRegistry.cancel(task_id)
        with self.lock:
            if task_id in self.tasks_status:
                status = self.tasks_status[task_id]["status"]
                if status in ("QUEUED", "RUNNING"):
                    self.tasks_status[task_id]["status"] = "CANCELLED"
                    self.tasks_status[task_id]["completed_at"] = time.time()
                    self.tasks_status[task_id]["errors"] = "Task cancelled by user request."
        self._save_history()
        print(f"🛑 Cancelled Task {task_id}")

    def _watchdog_loop(self):
        """
        Monitors running tasks. Cancels tasks that run longer than 30 minutes
        or fail to update their heartbeat for 600 seconds.
        """
        while self.running:
            time.sleep(5)
            now = time.time()
            running_tasks = []
            with self.lock:
                for task_id, info in self.tasks_status.items():
                    if info.get("status") == "RUNNING":
                        running_tasks.append(info.copy())
                        
            for task in running_tasks:
                task_id = task["task_id"]
                start_time = task.get("started_at") or task.get("created_at") or now
                
                # Check 1: Elapsed time timeout (30 minutes = 1800 seconds)
                elapsed = now - start_time
                if elapsed > 1800:
                    print(f"⚠️ Watchdog: Task {task_id} exceeded maximum duration limit (1800s). Cancelling.")
                    self.cancel_task(task_id)
                    with self.lock:
                        self.tasks_status[task_id]["errors"] = "Timeout: Task exceeded maximum execution limit of 1800s."
                    self._save_history()
                    continue
                    
                # Check 2: Heartbeat timeout (600 seconds)
                last_heartbeat = TaskStatusRegistry.get_last_heartbeat(task_id)
                if last_heartbeat == 0.0:
                    last_heartbeat = start_time
                    
                time_since_heartbeat = now - last_heartbeat
                if time_since_heartbeat > 600:
                    print(f"⚠️ Watchdog: Task {task_id} heartbeat lost for {time_since_heartbeat:.1f}s (limit 600s). Cancelling.")
                    self.cancel_task(task_id)
                    with self.lock:
                        self.tasks_status[task_id]["errors"] = "Timeout: Heartbeat lost (no progress for 600 seconds)."
                    self._save_history()

    def _worker_loop(self):
        """
        Main worker thread function. Picks tasks from queue and executes.
        """
        while self.running:
            task_info = self.task_queue.get()
            if task_info is None:
                # Sentinel to stop thread
                self.task_queue.task_done()
                break
                
            task_id = task_info["task_id"]
            
            with self.lock:
                # Generate clean, human-readable directory name based on task description
                words = re.findall(r'[a-zA-Z0-9]+', task_info["description"].lower())
                stop_words = {
                    "create", "build", "make", "write", "develop", "implement",
                    "a", "an", "the", "of", "to", "for", "in", "with", "and", "by",
                    "that", "this", "is", "it", "we", "want", "as"
                }
                filtered_words = [w for w in words if w not in stop_words]
                if not filtered_words:
                    filtered_words = [w for w in words if w not in {"a", "an", "the"}]
                if not filtered_words:
                    filtered_words = words if words else ["project"]
                    
                base_folder = "_".join(filtered_words)[:40].strip("_")
                
                # Handle duplicate names by adding a number suffix
                folder_name = base_folder
                counter = 2
                os.makedirs("workspace", exist_ok=True)
                while os.path.exists(os.path.join("workspace", folder_name)):
                    suffix = f"_{counter}"
                    max_len = 40 - len(suffix)
                    folder_name = f"{base_folder[:max_len].strip('_')}{suffix}"
                    counter += 1
                    
                workspace_dir = os.path.abspath(os.path.join("workspace", folder_name))
                os.makedirs(workspace_dir, exist_ok=True)
            
            print(f"⚙️ Worker {threading.current_thread().name} picked Task {task_id}. Workspace: {workspace_dir}")
            
            with self.lock:
                self.tasks_status[task_id]["status"] = "RUNNING"
                self.tasks_status[task_id]["started_at"] = time.time()
                self.tasks_status[task_id]["workspace_dir"] = workspace_dir
            self._save_history()
            
            try:
                # Register initial heartbeat and setup cancellation state
                TaskStatusRegistry.update_heartbeat(task_id)
                
                # Prepare initial state for the LangGraph agent
                initial_state = {
                    "description": task_info["description"],
                    "files": [],
                    "messages": [],
                    "iterations": 0,
                    "max_iterations": task_info["max_iterations"],
                    "errors": "",
                    "test_results": [],
                    "test_command": task_info["test_command"],
                    "workspace_dir": workspace_dir,
                    "language": task_info["language"],
                    "task_id": task_id
                }
                
                # Invoke the LangGraph graph
                final_state = graph.invoke(initial_state)
                
                # Populate completion details
                files = list_workspace_files(workspace_dir)
                errors = final_state.get("errors", "")
                
                # Perform export if export_path is configured and not cancelled
                if task_info["export_path"] and not TaskStatusRegistry.is_cancelled(task_id):
                    dest_dir = os.path.abspath(task_info["export_path"])
                    os.makedirs(dest_dir, exist_ok=True)
                    import shutil
                    for root, dirs, files_in_dir in os.walk(workspace_dir):
                        if "venv" in root.split(os.sep) or ".git" in root.split(os.sep) or "__pycache__" in root.split(os.sep):
                            continue
                        for filename in files_in_dir:
                            src_file = os.path.join(root, filename)
                            rel_path = os.path.relpath(src_file, workspace_dir)
                            dest_file = os.path.join(dest_dir, rel_path)
                            os.makedirs(os.path.dirname(dest_file), exist_ok=True)
                            shutil.copy2(src_file, dest_file)
                
                with self.lock:
                    if TaskStatusRegistry.is_cancelled(task_id):
                        self.tasks_status[task_id]["status"] = "CANCELLED"
                        self.tasks_status[task_id]["errors"] = errors or "Task cancelled by user request."
                    else:
                        self.tasks_status[task_id]["status"] = "PASSED" if errors == "" else "FAILED"
                        self.tasks_status[task_id]["errors"] = errors
                    self.tasks_status[task_id]["iterations"] = final_state.get("iterations", 0)
                    self.tasks_status[task_id]["files"] = files
                    self.tasks_status[task_id]["completed_at"] = time.time()
                
            except Exception as e:
                print(f"❌ Worker error on Task {task_id}: {e}")
                with self.lock:
                    self.tasks_status[task_id]["status"] = "ERROR"
                    self.tasks_status[task_id]["errors"] = f"Runtime executor error: {str(e)}"
                    self.tasks_status[task_id]["completed_at"] = time.time()
                    self.tasks_status[task_id]["workspace_dir"] = workspace_dir
            finally:
                self.task_queue.task_done()
                self._save_history()
                print(f"🏁 Task {task_id} completed with status: {self.tasks_status[task_id]['status']}. Project files are stored in: {workspace_dir}")
