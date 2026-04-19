"""
File Watcher for Todo Items
1. read files from a specific folder.
2. if the file is task is scl.meta.task format (either json or yaml), accept format file.
2.1. it converts the task from file into a task instance and put into queue as a TaskQueue instance.
2.2. move the file into processed folder.

3. if the file is scl.meta.CapTask format (either json or yaml), accept format file.
3.1. it converts the CapTask from file into a CapTask instance and put into queue as a CapTaskQueues instance.
3.2. move the file into processedCapTask folder.

4. if the file is not supported format, move the file into failed folder.
"""
import logging
import os
import shutil
import json
import yaml
from pathlib import Path
from typing import Optional

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from scl.queue.taskQueue import TaskQueue
from scl.queue.capTaskQueues import CapabilityTaskQueues
from scl.meta.task import Task
from scl.meta.captask import CapTask
from scl.otel.otel import tracer, meter
from opentelemetry import trace


class FileHandler(FileSystemEventHandler):
    """
    Watches a directory for new task files (JSON/YAML), validates them,
    converts to Task or CapTask objects, and queues them appropriately.
    """

    def __init__(
        self,
        watch_path: str,
        task_queue: TaskQueue,
        captask_queue: CapabilityTaskQueues
    ):
        self.watch_path = watch_path
        self.task_queue = task_queue
        self.captask_queue = captask_queue
        self.logger = logging.getLogger(__name__)

        # Setup folders
        self.processed_dir = os.path.join(watch_path, "processed")
        self.processed_captask_dir = os.path.join(watch_path, "processedCapTask")
        self.failed_dir = os.path.join(watch_path, "failed")
        os.makedirs(self.processed_dir, exist_ok=True)
        os.makedirs(self.processed_captask_dir, exist_ok=True)
        os.makedirs(self.failed_dir, exist_ok=True)

        # Metrics
        self.file_receive_counter = meter.create_counter(
            "file_receive",
            description="Total number of files detected"
        )
        self.task_file_valid_counter = meter.create_counter(
            "task_file_valid",
            description="Number of files successfully processed as Task"
        )
        self.captask_file_valid_counter = meter.create_counter(
            "captask_file_valid",
            description="Number of files successfully processed as CapTask"
        )
        self.file_invalid_counter = meter.create_counter(
            "file_invalid",
            description="Number of files that failed validation or conversion"
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
            self.logger.warning(f"File {filename} is not a supported format (JSON/YAML). Moving to failed.")
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
        return ext in ('.json', '.yaml', '.yml')

    def _parse_file(self, filepath: str) -> dict:
        with open(filepath, 'r', encoding='utf-8') as f:
            if filepath.lower().endswith('.json'):
                return json.load(f)
            else:
                return yaml.safe_load(f)

    def _process_as_task(self, filepath: str, filename: str, data: dict, span) -> bool:
        """
        Attempt to convert data to a Task and enqueue.
        Returns True if successful, False otherwise.
        """
        # Simple heuristic: Task requires at least 'id' and 'description' fields.
        # Adjust according to actual Task schema.
        if not self._looks_like_task(data):
            return False

        try:
            task_obj = Task.from_dict(data)   # Assuming a factory method exists
        except Exception as e:
            self.logger.debug(f"File {filename} appears to be Task but conversion failed: {e}")
            return False

        try:
            self.task_queue.add(task_obj)
            self.logger.debug(f"Task from file queued: {filename} (ID: {getattr(task_obj, 'id', 'unknown')})")
            self.task_file_valid_counter.add(1)

            dest = os.path.join(self.processed_dir, filename)
            shutil.move(filepath, dest)
            span.set_attribute("file.moved_to", dest)
            span.set_attribute("file.type", "Task")
            self.logger.info(f"Task file moved to processed: {dest}")
            return True

        except Exception as e:
            self.logger.error(f"Error queueing or moving Task file {filename}: {e}")
            span.record_exception(e)
            self._move_to_failed(filepath, reason="task_queue_error")
            return True  # We handled the error, but file is moved to failed

    def _process_as_captask(self, filepath: str, filename: str, data: dict, span) -> bool:
        """
        Attempt to convert data to a CapTask and enqueue.
        Returns True if successful, False otherwise.
        """
        # CapTask requires 'cap_name' and 'args' fields.
        if not self._looks_like_captask(data):
            return False

        try:
            captask_obj = CapTask.from_dict(data)
        except Exception as e:
            self.logger.debug(f"File {filename} appears to be CapTask but conversion failed: {e}")
            return False

        try:
            self.captask_queue.add(captask_obj)
            self.logger.debug(f"CapTask from file queued: {filename} (hash: {captask_obj.hash})")
            self.captask_file_valid_counter.add(1)

            dest = os.path.join(self.processed_captask_dir, filename)
            shutil.move(filepath, dest)
            span.set_attribute("file.moved_to", dest)
            span.set_attribute("file.type", "CapTask")
            self.logger.info(f"CapTask file moved to processedCapTask: {dest}")
            return True

        except Exception as e:
            self.logger.error(f"Error queueing or moving CapTask file {filename}: {e}")
            span.record_exception(e)
            self._move_to_failed(filepath, reason="captask_queue_error")
            return True

    def _looks_like_task(self, data: dict) -> bool:
        """Heuristic to identify Task format."""
        # Example: Task might have 'id', 'description', 'status'
        return 'id' in data and 'description' in data

    def _looks_like_captask(self, data: dict) -> bool:
        """Heuristic to identify CapTask format."""
        return 'cap_name' in data and 'args' in data

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