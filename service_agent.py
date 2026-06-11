import os
import queue
import threading
import uuid
import json
import time
from typing import Dict, Any, List
from agent import graph
from tools import list_workspace_files

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
            "completed_at": None
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
        from external_software import TaskStatusRegistry
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
        Monitors running tasks. Cancels tasks that run longer than 5 minutes
        or fail to update their heartbeat for 120 seconds.
        """
        from external_software import TaskStatusRegistry
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
                start_time = task.get("created_at") or now
                
                # Check 1: Elapsed time timeout (5 minutes = 300 seconds)
                elapsed = now - start_time
                if elapsed > 300:
                    print(f"⚠️ Watchdog: Task {task_id} exceeded maximum duration limit (300s). Cancelling.")
                    self.cancel_task(task_id)
                    with self.lock:
                        self.tasks_status[task_id]["errors"] = "Timeout: Task exceeded maximum execution limit of 300s."
                    self._save_history()
                    continue
                    
                # Check 2: Heartbeat timeout (120 seconds)
                last_heartbeat = TaskStatusRegistry.get_last_heartbeat(task_id)
                if last_heartbeat == 0.0:
                    last_heartbeat = start_time
                    
                time_since_heartbeat = now - last_heartbeat
                if time_since_heartbeat > 120:
                    print(f"⚠️ Watchdog: Task {task_id} heartbeat lost for {time_since_heartbeat:.1f}s (limit 120s). Cancelling.")
                    self.cancel_task(task_id)
                    with self.lock:
                        self.tasks_status[task_id]["errors"] = "Timeout: Heartbeat lost (no progress for 120 seconds)."
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
            workspace_dir = os.path.abspath(os.path.join("workspace", f"task_{task_id}"))
            os.makedirs(workspace_dir, exist_ok=True)
            
            print(f"⚙️ Worker {threading.current_thread().name} picked Task {task_id}. Workspace: {workspace_dir}")
            
            with self.lock:
                self.tasks_status[task_id]["status"] = "RUNNING"
            self._save_history()
            
            try:
                # Register initial heartbeat and setup cancellation state
                from external_software import TaskStatusRegistry
                TaskStatusRegistry.update_heartbeat(task_id)
                
                # Start a background heartbeat updater thread for the duration of graph.invoke
                # to prevent watchdog timeouts during slow local LLM generations.
                heartbeat_stop = threading.Event()
                def worker_heartbeat():
                    while not heartbeat_stop.is_set():
                        TaskStatusRegistry.update_heartbeat(task_id)
                        heartbeat_stop.wait(timeout=10)
                
                updater = threading.Thread(target=worker_heartbeat, name=f"WorkerHB-{task_id}", daemon=True)
                updater.start()
                
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
                
                try:
                    # Invoke the LangGraph graph
                    final_state = graph.invoke(initial_state)
                finally:
                    heartbeat_stop.set()
                    # Wait briefly for the updater thread to join
                    updater.join(timeout=1)
                
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
            finally:
                self.task_queue.task_done()
                self._save_history()
                print(f"🏁 Task {task_id} completed with status: {self.tasks_status[task_id]['status']}")
