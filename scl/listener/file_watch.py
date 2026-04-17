"""
File Watcher for Todo Items
1. if the file been read, move the file into processed folder.
2. it checks the file following the scl.meta.task format (either json or yaml), just accept format file.
3. it converts the task from file into a task instance and put into queue.
"""
import logging
import os
import shutil
import json
import yaml
from pathlib import Path
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from scl.meta.taskQueue import TaskQueue
from scl.meta.task import Task  # Assumed import; replace with actual if different
from scl.otel.otel import tracer, meter
from opentelemetry import trace


class FileHandler(FileSystemEventHandler):
    """
    Watches a directory for new task files (JSON/YAML), validates them,
    converts to Task objects, and queues them for processing.
    """

    def __init__(self, watch_path: str, queue: TaskQueue):
        self.watch_path = watch_path
        self.queue = queue
        self.logger = logging.getLogger(__name__)
        self.processed_dir = os.path.join(watch_path, "processed")
        self.failed_dir = os.path.join(watch_path, "failed")
        os.makedirs(self.processed_dir, exist_ok=True)
        os.makedirs(self.failed_dir, exist_ok=True)

        # Metrics
        self.file_receive_counter = meter.create_counter(
            "file_receive",
            description="Total number of files detected"
        )
        self.file_valid_counter = meter.create_counter(
            "file_valid",
            description="Number of files successfully validated as task format"
        )
        self.file_invalid_counter = meter.create_counter(
            "file_invalid",
            description="Number of files that failed validation"
        )
        self.task_conversion_failure_counter = meter.create_counter(
            "task_conversion_failure",
            description="Number of files that failed to convert to Task objects"
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

        # Step 1: Validate file format (extension and content)
        if not self._is_task_format_file(filepath):
            self.logger.warning(f"File {filename} is not a supported task format (JSON/YAML). Moving to failed.")
            self.file_invalid_counter.add(1)
            self._move_to_failed(filepath, reason="unsupported_format")
            return

        # Step 2: Parse file content into a dict
        try:
            task_data = self._parse_task_file(filepath)
        except Exception as e:
            self.logger.error(f"Failed to parse task file {filename}: {e}")
            current_span.record_exception(e)
            self.file_invalid_counter.add(1)
            self._move_to_failed(filepath, reason="parse_error")
            return

        # Step 3: Convert to Task instance
        try:
            task_obj = Task.from_dict(task_data)  # Assume a factory method; adjust as needed
            # Alternative: task_obj = Task(**task_data) if constructor accepts kwargs
        except Exception as e:
            self.logger.error(f"Failed to create Task object from {filename}: {e}")
            current_span.record_exception(e)
            self.task_conversion_failure_counter.add(1)
            self._move_to_failed(filepath, reason="conversion_error")
            return

        # Step 4: Add to queue and move to processed
        try:
            self.queue.add(task_obj)  # Now adding Task instance, not raw dict
            self.logger.debug(f"Task from file queued: {filename} (ID: {getattr(task_obj, 'id', 'unknown')})")
            self.file_valid_counter.add(1)

            dest_path = os.path.join(self.processed_dir, filename)
            shutil.move(filepath, dest_path)
            current_span.set_attribute("file.moved_to", dest_path)
            self.logger.info(f"File moved to processed: {dest_path}")

        except Exception as e:
            self.logger.error(f"Error queueing or moving file {filename}: {e}")
            current_span.record_exception(e)
            # Attempt to move to failed if queue fails
            self._move_to_failed(filepath, reason="queue_error")

    def _is_task_format_file(self, filepath: str) -> bool:
        """
        Check if file has a supported extension (.json, .yaml, .yml) and
        (optionally) if it can be parsed. Returns True if format is acceptable.
        """
        ext = Path(filepath).suffix.lower()
        return ext in ('.json', '.yaml', '.yml')

    def _parse_task_file(self, filepath: str) -> dict:
        """
        Parse JSON or YAML file and return dictionary.
        Raises exception if parsing fails.
        """
        with open(filepath, 'r', encoding='utf-8') as f:
            if filepath.lower().endswith('.json'):
                return json.load(f)
            else:
                return yaml.safe_load(f)

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