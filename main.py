import logging
import os
import signal
import sys
import threading
import time
from scl.queue.taskQueue import TaskQueue
from scl.queue.capTaskQueues import CapabilityTaskQueues
from scl.queue.awaitingApproveQueue import AwaitingApproveQueue
from scl.queue.awaitingCapTasksQueue import AwaitingCapTasksQueue

from scl.listener.restful_watch import RestFulHandler
from scl.listener.file_watch import FileHandler
from scl.processor.task_processor import TaskProcessor
# Setup telemetry
logger = logging.getLogger(__name__)
from scl.otel.otel import init_telemetry
init_telemetry()

def main():
    todo_queue = TaskQueue()
    captask_queue = CapabilityTaskQueues()
    waiting_approval_queue = AwaitingApproveQueue()
    waiting_captask_queue = AwaitingCapTasksQueue()
    
    logger.info("Starting Todo Receiver Application")

    # Ensure watch directory exists
    watch_dir = os.getenv("TODO_WATCH_DIR", "./todo_folder")
    os.makedirs(watch_dir, exist_ok=True)

    # Start todo processor
    processor = TaskProcessor(todo_queue)
    processor.start()

    # Start listeners
    file_handler = FileHandler(
        watch_path=watch_dir,
        task_queue=todo_queue,
        captask_queue=captask_queue,
        waiting_approval_queue=waiting_approval_queue,
        waiting_captask_queue=waiting_captask_queue
    )
    rest_handler = RestFulHandler(watch_dir, host="0.0.0.0", port="8080")

    file_observer = file_handler.start()
    api_thread = threading.Thread(target=rest_handler.start, daemon=True)
    # Wait for termination signal
    def shutdown(signum, frame):
        logger.info("Shutting down...")
        file_observer.stop()
        file_observer.join()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    api_thread.start()

    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown(None, None)

if __name__ == "__main__":
    main()