import time
import os
from service_agent import CodingAgentService
from external_software import TaskStatusRegistry

def main():
    print("🧪 Starting Cancellation Flow Test...")
    service = CodingAgentService(num_workers=2)
    service.start_service()

    # Define a task that writes and runs a script that sleeps for a long time
    # This simulates a hung execution.
    description = (
        "Create a script named long_sleep.py that sleeps for 20 seconds using time.sleep. "
        "We want to test if it gets cancelled successfully."
    )
    
    # We will enqueue this task
    task_id = service.add_task(
        description=description,
        test_command="python long_sleep.py",
        language="python",
        max_iterations=5
    )
    
    # Wait for it to transition to RUNNING
    print("⏳ Waiting for task to start running...")
    for _ in range(15):
        status_info = service.get_task_status(task_id)
        status = status_info.get("status")
        print(f"Current Status: {status}")
        if status == "RUNNING":
            break
        time.sleep(1)
        
    # Let it run for a couple of seconds, then trigger cancellation
    time.sleep(3)
    print(f"🛑 Triggering cancellation for task: {task_id}")
    service.cancel_task(task_id)
    
    # Wait to see if it moves to CANCELLED
    print("⏳ Waiting to verify task cancellation...")
    cancelled_ok = False
    for _ in range(10):
        status_info = service.get_task_status(task_id)
        status = status_info.get("status")
        print(f"Current Status: {status}")
        if status == "CANCELLED":
            cancelled_ok = True
            break
        time.sleep(1)
        
    service.shutdown()
    
    if cancelled_ok:
        print("🎉 SUCCESS: Task was successfully cancelled and marked CANCELLED!")
    else:
        print("❌ FAILURE: Task cancellation did not trigger or state was not updated correctly.")

if __name__ == "__main__":
    main()
