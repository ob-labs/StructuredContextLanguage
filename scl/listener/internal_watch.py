"""
Internal Watcher for Task and CapTask Items

Converts the task into a Task or CapTask instance and writes a file to the file_watch directory.
Supports JSON (default) and YAML serialization via pyyaml.

Dependencies:
    pyyaml, opentelemetry-api, opentelemetry-sdk

Install with:
    pip install pyyaml opentelemetry-api opentelemetry-sdk
"""

# ---------------------------------------------------------------------------
# Features and design goals
# ---------------------------------------------------------------------------
# - Accept Task or CapTask instances via add() method.
# - Persist objects as files in a watch directory.
# - Support JSON (default) and YAML output formats.
# - Use hash attribute from objects for unique filenames.
# - OpenTelemetry tracing spans for add operation with type-specific metadata.
# - Metrics counters for successful writes and errors per item type.
# - Structured logging at both INFO and DEBUG levels.
# - Automatically create the watch directory if missing.
# - Clear error messages and type validation.
# ---------------------------------------------------------------------------

import json
import logging
import os

import yaml
from opentelemetry import trace

from scl.meta.captask import CapTask
from scl.meta.task import Task
from scl.otel.otel import meter, tracer

logger = logging.getLogger(__name__)


class InternalWatcher:
    """
    A simple internal watcher that accepts Task or CapTask instances and writes them as files
    into the file_watch directory for unified processing by the FileWatcher.
    """

    def __init__(self, watch_path: str, output_format: str = "json"):
        """
        Initialize the internal watcher with the watch directory path and output format.

        :param watch_path: Directory where task files will be written (same as file watcher's watch_path).
        :param output_format: Serialization format, either "json" (default) or "yaml".
        """
        if output_format not in ("json", "yaml"):
            raise ValueError(f"output_format must be 'json' or 'yaml', got '{output_format}'")

        self.watch_path = watch_path
        self.output_format = output_format
        self.logger = logging.getLogger(__name__)

        # Ensure watch directory exists
        os.makedirs(self.watch_path, exist_ok=True)

        # Metrics for Task writes
        self.internal_task_counter = meter.create_counter(
            "internal_task_write", description="Number of internal Task instances written to file"
        )
        self.internal_task_error_counter = meter.create_counter(
            "internal_task_error",
            description="Number of errors while writing internal Task instances to file",
        )

        # Metrics for CapTask writes
        self.internal_captask_counter = meter.create_counter(
            "internal_captask_write",
            description="Number of internal CapTask instances written to file",
        )
        self.internal_captask_error_counter = meter.create_counter(
            "internal_captask_error",
            description="Number of errors while writing internal CapTask instances to file",
        )

        self.logger.info(
            "InternalWatcher initialized with watch_path=%s, format=%s",
            self.watch_path,
            self.output_format,
        )

    @tracer.start_as_current_span("internal_watcher_add")
    def add(self, item: Task | CapTask) -> str:
        """
        Add a Task or CapTask instance by writing it to a file in the watch_path directory.

        :param item: The Task or CapTask object to persist.
        :return: The hash string used in the filename.
        :raises TypeError: If the provided item is not a Task or CapTask instance.
        :raises Exception: If file writing fails (logged and re-raised).
        """
        current_span = trace.get_current_span()

        if isinstance(item, Task):
            return self._add_task(item, current_span)
        elif isinstance(item, CapTask):
            return self._add_captask(item, current_span)
        else:
            error_msg = f"Expected Task or CapTask instance, got {type(item).__name__}"
            self.logger.error(error_msg)
            current_span.set_attribute("error", True)
            current_span.set_attribute("error.message", error_msg)
            raise TypeError(error_msg)

    def _add_task(self, task: Task, span: trace.Span) -> str:
        """Handle Task instance writing."""
        task_hash = getattr(task, "hash", None)
        if not task_hash:
            error_msg = "Task object missing 'hash' attribute"
            self.logger.error(error_msg)
            span.set_attribute("error", True)
            span.set_attribute("error.message", error_msg)
            self.internal_task_error_counter.add(1)
            raise ValueError(error_msg)

        task_id = getattr(task, "id", "unknown")
        task_type = getattr(task, "type", "unknown")
        span.set_attribute("task.id", str(task_id))
        span.set_attribute("task.type", task_type)
        span.set_attribute("task.hash", str(task_hash))
        span.set_attribute("item.type", "Task")

        self.logger.debug(
            "Internally generated Task received: id=%s, hash=%s, type=%s",
            task_id,
            task_hash,
            task_type,
        )

        try:
            file_path = self._write_item_file(task, task_hash, "Task")
            span.set_attribute("file.path", file_path)
            self.internal_task_counter.add(1)
            self.logger.info("Internal Task %s written to file: %s", task_hash, file_path)
            return task_hash
        except Exception as e:
            self.logger.error(
                "Failed to write internal Task %s to file: %s", task_hash, e, exc_info=True
            )
            span.record_exception(e)
            self.internal_task_error_counter.add(1)
            raise

    def _add_captask(self, captask: CapTask, span: trace.Span) -> str:
        """Handle CapTask instance writing."""
        captask_hash = getattr(captask, "hash", None)
        if not captask_hash:
            error_msg = "CapTask object missing 'hash' attribute"
            self.logger.error(error_msg)
            span.set_attribute("error", True)
            span.set_attribute("error.message", error_msg)
            self.internal_captask_error_counter.add(1)
            raise ValueError(error_msg)

        cap_name = getattr(captask, "cap_name", "unknown")
        span.set_attribute("captask.cap_name", cap_name)
        span.set_attribute("captask.hash", str(captask_hash))
        span.set_attribute("item.type", "CapTask")

        self.logger.debug(
            "Internally generated CapTask received: cap_name=%s, hash=%s", cap_name, captask_hash
        )

        try:
            file_path = self._write_item_file(captask, captask_hash, "CapTask")
            span.set_attribute("file.path", file_path)
            self.internal_captask_counter.add(1)
            self.logger.info("Internal CapTask %s written to file: %s", captask_hash, file_path)
            return captask_hash
        except Exception as e:
            self.logger.error(
                "Failed to write internal CapTask %s to file: %s", captask_hash, e, exc_info=True
            )
            span.record_exception(e)
            self.internal_captask_error_counter.add(1)
            raise

    def _write_item_file(self, item: Task | CapTask, item_hash: str, item_type: str) -> str:
        """
        Write a Task or CapTask instance to a file in watch_path.

        Format is determined by self.output_format (json or yaml).

        :param item: Task or CapTask object to serialize.
        :param item_hash: Hash string used as filename stem.
        :param item_type: Descriptive type for logging (not used in filename).
        :return: Full path to the written file.
        """
        ext = ".json" if self.output_format == "json" else ".yaml"
        file_path = os.path.join(self.watch_path, f"{item_hash}{ext}")

        # Extract serializable dictionary
        if hasattr(item, "to_dict"):
            item_dict = item.to_dict()
        else:
            item_dict = item.__dict__

        with open(file_path, "w", encoding="utf-8") as f:
            if self.output_format == "json":
                json.dump(item_dict, f, indent=2)
            else:  # yaml
                yaml.dump(item_dict, f, default_flow_style=False, allow_unicode=True)

        return file_path


# ---------------------------------------------------------------------------
# Example usage
# ---------------------------------------------------------------------------
#
# # Using JSON (default)
# watcher = InternalWatcher("/path/to/watch_dir")
# task = Task(...)                           # Create a Task instance
# task_hash = watcher.add(task)              # Writes /path/to/watch_dir/<hash>.json
#
# captask = CapTask(cap_name="email", args=["to@example.com"])
# captask_hash = watcher.add(captask)        # Writes /path/to/watch_dir/<hash>.json
#
# # Using YAML
# yaml_watcher = InternalWatcher("/path/to/watch_dir", output_format="yaml")
# yaml_watcher.add(task)                     # Writes /path/to/watch_dir/<hash>.yaml
