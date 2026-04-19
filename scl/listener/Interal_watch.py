"""
Internal Watcher for Task Items
It converts the task into a Task instance and writes a file to the file_watch directory.

Dependencies:
    pyyaml, opentelemetry-api, opentelemetry-sdk

Install with:
    pip install pyyaml opentelemetry-api opentelemetry-sdk
"""

import logging
import os
import json
from typing import TYPE_CHECKING

from opentelemetry import trace

from scl.meta.task import Task  # Import Task class for type checking
from scl.otel.otel import tracer, meter

if TYPE_CHECKING:
    # For type hints without circular imports
    pass

logger = logging.getLogger(__name__)


class InternalWatcher:
    """
    A simple internal watcher that accepts Task instances and writes them as files
    into the file_watch directory for unified processing by the FileWatcher.
    """

    def __init__(self, watch_path: str):
        """
        Initialize the internal watcher with the watch directory path.

        :param watch_path: Directory where task files will be written (same as file watcher's watch_path)
        """
        self.watch_path = watch_path
        self.logger = logging.getLogger(__name__)

        # Ensure watch directory exists
        os.makedirs(self.watch_path, exist_ok=True)

        # Metrics
        self.internal_task_counter = meter.create_counter(
            "internal_task_write",
            description="Number of internal tasks written to file"
        )
        self.internal_task_error_counter = meter.create_counter(
            "internal_task_error",
            description="Number of errors while writing internal tasks to file"
        )

        self.logger.info(f"InternalWatcher initialized with watch_path: {self.watch_path}")

    @tracer.start_as_current_span("internal_watcher_add_task")
    def add(self, task: Task) -> str:
        """
        Add a Task instance by writing it to a file in the watch_path directory.

        :param task: The Task object to persist.
        :return: The task hash string used in the filename.
        :raises TypeError: If the provided item is not a Task instance.
        :raises Exception: If file writing fails (logged and re‑raised).
        """
        current_span = trace.get_current_span()

        # Validate input type
        if not isinstance(task, Task):
            error_msg = f"Expected Task instance, got {type(task).__name__}"
            self.logger.error(error_msg)
            current_span.set_attribute("error", True)
            current_span.set_attribute("error.message", error_msg)
            self.internal_task_error_counter.add(1)
            raise TypeError(error_msg)

        # Extract task hash (required for filename)
        task_hash = getattr(task, 'hash', None)
        if not task_hash:
            error_msg = "Task object missing 'hash' attribute"
            self.logger.error(error_msg)
            current_span.set_attribute("error", True)
            current_span.set_attribute("error.message", error_msg)
            self.internal_task_error_counter.add(1)
            raise ValueError(error_msg)

        # Enrich span with task metadata
        task_id = getattr(task, 'id', 'unknown')
        task_type = getattr(task, 'type', 'unknown')
        current_span.set_attribute("task.id", str(task_id))
        current_span.set_attribute("task.type", task_type)
        current_span.set_attribute("task.hash", str(task_hash))

        self.logger.debug(f"Internally generated task received: id={task_id}, hash={task_hash}, type={task_type}")

        # Write task to file
        try:
            file_path = self._write_task_file(task, task_hash)
            current_span.set_attribute("file.path", file_path)
            self.internal_task_counter.add(1)
            self.logger.info(f"Internal task {task_hash} written to file: {file_path}")
            return task_hash
        except Exception as e:
            self.logger.error(f"Failed to write internal task {task_hash} to file: {e}", exc_info=True)
            current_span.record_exception(e)
            self.internal_task_error_counter.add(1)
            raise

    def _write_task_file(self, task: Task, task_hash: str) -> str:
        """
        Write Task instance to a file in watch_path. Format defaults to JSON.

        :param task: Task object to serialize.
        :param task_hash: Hash string used as filename stem.
        :return: Full path to the written file.
        """
        ext = ".json"
        file_path = os.path.join(self.watch_path, f"{task_hash}{ext}")

        # Serialize task to dict
        task_dict = task.to_dict() if hasattr(task, 'to_dict') else task.__dict__

        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(task_dict, f, indent=2)

        return file_path