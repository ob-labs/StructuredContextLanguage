"""
File Watcher for Todo Items
1. read files from a specific folder.
2. if the file is task is scl.meta.task format (either json or yaml), accept format file.
2.1 it converts the task from file into a task instance
2.1.1 if the task instance got approval put into queue as a TaskQueue instance and move the file into processed folder.
2.1.2 if the task instance is not got approval put into waitingapproval folder and move the file into waitingapproval folder.
2.1.3 if the task instance has CapTask to completed put into waitingCapTask queue and move the file into waitingCapTask folder.

3. if the file is scl.meta.CapTask format (either json or yaml), accept format file.
3.1. it converts the CapTask from file into a CapTask instance and put into queue as a CapTaskQueues instance.
3.2. move the file into processedCapTask folder.

4. if the file is not supported format, move the file into failed folder.
"""

import json
import logging
import os
import shutil
from pathlib import Path

import yaml
from opentelemetry import trace
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from scl.meta.captask import CapTask
from scl.meta.task import Task
from scl.otel.otel import meter, tracer
from scl.queue.awaiting_approve_queue import AwaitingApproveQueue
from scl.queue.awaiting_cap_tasks_queue import AwaitingCapTasksQueue
from scl.queue.cap_task_queues import CapabilityTaskQueues
from scl.queue.task_queue import TaskQueue


class FileHandler(FileSystemEventHandler):
    """
    Watches a directory for new task files (JSON/YAML), validates them,
    converts to Task or CapTask objects, and queues them appropriately.
    """

    def __init__(
        self,
        watch_path: str,
        task_queue: TaskQueue,
        captask_queue: CapabilityTaskQueues,
        waiting_approval_queue: AwaitingApproveQueue,
        waiting_captask_queue: AwaitingCapTasksQueue,
    ):
        self.watch_path = watch_path
        self.task_queue = task_queue
        self.captask_queue = captask_queue
        self.waiting_approval_queue = waiting_approval_queue
        self.waiting_captask_queue = waiting_captask_queue
        self.logger = logging.getLogger(__name__)

        # Setup folders
        self.processed_dir = os.path.join(watch_path, "processed")
        self.processed_captask_dir = os.path.join(watch_path, "processedCapTask")
        self.waiting_approval_dir = os.path.join(watch_path, "waitingapproval")
        self.waiting_captask_dir = os.path.join(watch_path, "waitingCapTask")
        self.failed_dir = os.path.join(watch_path, "failed")

        os.makedirs(self.processed_dir, exist_ok=True)
        os.makedirs(self.processed_captask_dir, exist_ok=True)
        os.makedirs(self.waiting_approval_dir, exist_ok=True)
        os.makedirs(self.waiting_captask_dir, exist_ok=True)
        os.makedirs(self.failed_dir, exist_ok=True)

        # Metrics
        self.file_receive_counter = meter.create_counter(
            "file_receive", description="Total number of files detected"
        )
        self.task_file_approved_counter = meter.create_counter(
            "task_file_approved", description="Number of Task files that were approved and queued"
        )
        self.task_file_unapproved_counter = meter.create_counter(
            "task_file_unapproved",
            description="Number of Task files that lacked approval and were moved to waiting",
        )
        self.task_file_pending_captasks_counter = meter.create_counter(
            "task_file_pending_captasks",
            description="Number of Task files with incomplete CapTasks moved to waiting",
        )
        self.captask_file_valid_counter = meter.create_counter(
            "captask_file_valid", description="Number of files successfully processed as CapTask"
        )
        self.file_invalid_counter = meter.create_counter(
            "file_invalid", description="Number of files that failed validation or conversion"
        )

    @tracer.start_as_current_span("file_watcher_on_created")
    def on_created(self, event):
        """Triggered when a new file appears in the watched directory."""
        if event.is_directory:
            return

        current_span = trace.get_current_span()
        filepath = event.src_path
        filename = os.path.basename(filepath)

        current_span.set_attribute("file.path", filepath)
        current_span.set_attribute("file.name", filename)
        self.logger.info(f"New file detected: {filepath}")
        self.file_receive_counter.add(1)

        # Step 1: Validate file extension
        if not self._is_supported_extension(filepath):
            self.logger.warning(
                f"File {filename} is not a supported format (JSON/YAML). Moving to failed."
            )
            self.file_invalid_counter.add(1)
            self._move_to_failed(filepath, reason="unsupported_extension")
            return

        # Step 2: Parse file content into a dict
        try:
            data = self._parse_file(filepath)
        except Exception as e:
            self.logger.error(f"Failed to parse file {filename}: {e}")
            current_span.record_exception(e)
            self.file_invalid_counter.add(1)
            self._move_to_failed(filepath, reason="parse_error")
            return

        # Step 3: Determine format and process accordingly
        processed = self._process_as_task(filepath, filename, data, current_span)
        if processed:
            return

        processed = self._process_as_captask(filepath, filename, data, current_span)
        if processed:
            return

        # Step 4: Unrecognized format
        self.logger.warning(f"File {filename} does not match Task or CapTask structure.")
        self.file_invalid_counter.add(1)
        self._move_to_failed(filepath, reason="unrecognized_format")

    def _is_supported_extension(self, filepath: str) -> bool:
        ext = Path(filepath).suffix.lower()
        return ext in (".json", ".yaml", ".yml")

    def _parse_file(self, filepath: str) -> dict:
        with open(filepath, encoding="utf-8") as f:
            if filepath.lower().endswith(".json"):
                return json.load(f)
            else:
                return yaml.safe_load(f)

    def _process_as_task(self, filepath: str, filename: str, data: dict, span) -> bool:
        """
        Attempt to convert data to a Task and enqueue based on approval and CapTask status.
        Returns True if successful, False otherwise.
        """
        # Validate by attempting conversion; Task.from_dict will raise if invalid
        try:
            task_obj = Task.from_dict(data)
        except Exception as e:
            self.logger.debug(f"File {filename} cannot be converted to Task: {e}")
            return False

        try:
            # Check approval status
            if not task_obj.approval:
                # Not approved -> waiting approval queue
                self.waiting_approval_queue.add(task_obj)
                self.task_file_unapproved_counter.add(1)
                dest_dir = self.waiting_approval_dir
                span.set_attribute("task.approval", False)
                span.set_attribute("task.routed_to", "waiting_approval")
                self.logger.info(f"Task {task_obj.hash} lacks approval, moved to waitingapproval")
            elif not self._all_captasks_completed(task_obj):
                # Approved but pending CapTasks -> waiting CapTasks queue
                self.waiting_captask_queue.push(task_obj)
                self.task_file_pending_captasks_counter.add(1)
                dest_dir = self.waiting_captask_dir
                span.set_attribute("task.approval", True)
                span.set_attribute("task.pending_captasks", True)
                span.set_attribute("task.routed_to", "waiting_captasks")
                self.logger.info(
                    f"Task {task_obj.hash} has pending CapTasks, moved to waitingCapTask"
                )
            else:
                # Fully ready -> TaskQueue
                self.task_queue.add(task_obj)
                self.task_file_approved_counter.add(1)
                dest_dir = self.processed_dir
                span.set_attribute("task.approval", True)
                span.set_attribute("task.pending_captasks", False)
                span.set_attribute("task.routed_to", "task_queue")
                self.logger.info(f"Task {task_obj.hash} approved and ready, moved to processed")

            # Move file to appropriate directory
            dest = os.path.join(dest_dir, filename)
            shutil.move(filepath, dest)
            span.set_attribute("file.moved_to", dest)
            span.set_attribute("file.type", "Task")
            self.logger.info(f"Task file moved: {dest}")
            return True

        except Exception as e:
            self.logger.error(f"Error processing Task file {filename}: {e}")
            span.record_exception(e)
            self._move_to_failed(filepath, reason="task_processing_error")
            return True  # Handled, but file moved to failed

    def _all_captasks_completed(self, task: Task) -> bool:
        """Return True if all CapTasks of the task are in a completed state."""
        for cap in task.cap_tasks:
            if cap.status not in ("Processed", "Error"):
                return False
        return True

    def _process_as_captask(self, filepath: str, filename: str, data: dict, span) -> bool:
        """
        Attempt to convert data to a CapTask and enqueue to CapabilityTaskQueues.
        Returns True if successful, False otherwise.
        """
        try:
            captask_obj = CapTask.from_dict(data)
        except Exception as e:
            self.logger.debug(f"File {filename} cannot be converted to CapTask: {e}")
            return False

        try:
            self.captask_queue.add(captask_obj)
            self.captask_file_valid_counter.add(1)

            dest = os.path.join(self.processed_captask_dir, filename)
            shutil.move(filepath, dest)
            span.set_attribute("file.moved_to", dest)
            span.set_attribute("file.type", "CapTask")
            span.set_attribute("captask.hash", captask_obj.hash)
            self.logger.info(f"CapTask file moved to processedCapTask: {dest}")
            return True

        except Exception as e:
            self.logger.error(f"Error queuing or moving CapTask file {filename}: {e}")
            span.record_exception(e)
            self._move_to_failed(filepath, reason="captask_queue_error")
            return True

    def _move_to_failed(self, filepath: str, reason: str = ""):
        """
        Move invalid/unprocessable file to a 'failed' subdirectory.
        Appends reason to filename to aid debugging.
        """
        try:
            base = os.path.basename(filepath)
            name, ext = os.path.splitext(base)
            new_name = f"{name}.{reason}{ext}" if reason else base
            dest = os.path.join(self.failed_dir, new_name)
            shutil.move(filepath, dest)
            self.logger.info(f"File moved to failed: {dest}")
        except Exception as e:
            self.logger.error(f"Could not move file {filepath} to failed: {e}")

    def start(self) -> Observer:
        """Start watchdog observer on the given path."""
        observer = Observer()
        observer.schedule(self, self.watch_path, recursive=False)
        observer.start()
        self.logger.info(f"File watcher started on {self.watch_path}")
        return observer


# Missing / Future Features (kept as comments for open-source tracking):
# - Recursive directory watching.
# - File debouncing to avoid processing partial writes.
# - Atomic move/rename handling across filesystems.
# - Configurable folder names.
# - Support for other serialization formats (e.g., TOML).

"""
    Example usage:
        from scl.queue.task_queue import TaskQueue
        from scl.queue.capabilityTaskQueues import CapabilityTaskQueues
        from scl.queue.awaiting_approve_queue import AwaitingApproveQueue
        from scl.queue.awaiting_cap_tasks_queue import AwaitingCapTasksQueue
        from watchdog.observers import Observer
        from file_handler import FileHandler  # adjust import

        # Setup queues
        task_queue = TaskQueue()
        captask_queue = CapabilityTaskQueues()
        waiting_approval_queue = AwaitingApproveQueue()
        waiting_captask_queue = AwaitingCapTasksQueue()

        # Create handler
        handler = FileHandler(
            watch_path="/path/to/watch",
            task_queue=task_queue,
            captask_queue=captask_queue,
            waiting_approval_queue=waiting_approval_queue,
            waiting_captask_queue=waiting_captask_queue
        )

        # Start watching
        observer = handler.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
        observer.join()
"""
