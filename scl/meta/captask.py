"""
File path:
cap_task.py

Features and design goals
- Each CapTask has:
  - hash: unique identifier for this task instance.
  - task_hash: identifier of the parent task or workflow this task belongs to.
  - cap_name: name of the Capability to be invoked.
  - args: list of arguments to pass to the capability invocation.
  - approval: a flag if get approval for running, default is True.
  - status: current status of the CapTask in ["created", "Processed", "Error"]
  - result: 500 lines of full_result.
  - full_result: the full result of the capability invocation.
- Support JSON serialization/deserialization for persistence or message passing.
- When this class been initialized, it will write down a file to scl.config.todo_watch_dir folder, the file name is hash value.

Project Constraints Applied:
----------------------------
- OpenTelemetry integrated for tracing, metrics, and structured logging.
- Logger provides info and debug levels.
- Dependencies are documented as `pip install` commands, not requirements.txt.

Installation:
    pip install opentelemetry-api opentelemetry-sdk
"""

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass
from typing import Any

# OpenTelemetry imports
from opentelemetry import trace

from scl.otel.otel import meter, tracer

# Try to import todo_watch_dir from config; fallback if not available
try:
    from scl.config import todo_watch_dir
except ImportError:
    # Placeholder default for open-source compatibility
    todo_watch_dir = os.path.join(os.getcwd(), "todo_watch")
    logging.warning(f"scl.config not found, using default todo_watch_dir: {todo_watch_dir}")

# Setup logger
logger = logging.getLogger(__name__)

# Setup metrics
cap_task_created_counter = meter.create_counter(
    "cap_task.created", description="Number of CapTask instances created"
)
cap_task_serialized_counter = meter.create_counter(
    "cap_task.serialized", description="Number of times a CapTask was serialized to JSON"
)
cap_task_deserialized_counter = meter.create_counter(
    "cap_task.deserialized", description="Number of times a CapTask was deserialized from JSON"
)
cap_task_status_changed_counter = meter.create_counter(
    "cap_task.status_changed", description="Number of times a CapTask status changed"
)
cap_task_file_written_counter = meter.create_counter(
    "cap_task.file_written", description="Number of CapTask files written to todo_watch_dir"
)


@dataclass
class CapTask:
    """
    Represents a single invocation task for a Capability.

    Attributes:
        cap_name (str): Name of the capability to invoke.
        args (List[Any]): Arguments for the capability call.
        task_hash (Optional[str]): Hash/ID of the parent workflow.
        hash (Optional[str]): Unique identifier of this task (auto-generated if None).
        approval (bool): Whether the task is approved for execution (default True).
        status (str): Current status, one of {"created", "Processed", "Error"}.
        full_result (str): The complete result/output from capability invocation.

    Properties:
        result (str): First 500 lines of full_result.

    Example usage:
        task = CapTask(
            cap_name="send_email",
            args=["user@example.com", "Hello!"],
            task_hash="workflow-123",
            approval=True
        )
        # After the capability runs, store the result
        task.full_result = "email sent successfully\\n...more details..."
        print(task.result)          # first 500 lines
        print(task.full_result)     # entire result

        # Update status after processing
        task.set_status("Processed")

        json_str = task.to_json()
        restored = CapTask.from_json(json_str)
    """

    cap_name: str
    args: list[Any]
    task_hash: str | None = None
    hash: str | None = None  # Auto-generated if not provided
    approval: bool = True  # Approval flag
    status: str = "created"  # Current status
    full_result: str = ""  # Full result of the capability invocation

    VALID_STATUSES = {"created", "Processed", "Error"}

    @property
    def result(self) -> str:
        """
        Returns the first 500 lines of the full_result.
        """
        lines = self.full_result.splitlines(keepends=True)
        return "".join(lines[:500])

    def __post_init__(self):
        """Auto-generate unique hash if not supplied, set default task_hash, validate status, and write file."""
        with tracer.start_as_current_span("CapTask.__post_init__") as span:
            if self.hash is None:
                object.__setattr__(self, "hash", str(uuid.uuid4()))
                span.set_attribute("cap_task.hash_generated", True)
            else:
                span.set_attribute("cap_task.hash_generated", False)

            if self.task_hash is None:
                object.__setattr__(self, "task_hash", "default")

            # Validate initial status
            if self.status not in self.VALID_STATUSES:
                logger.warning(
                    f"Invalid initial status '{self.status}' for CapTask {self.hash}, "
                    f"defaulting to 'created'"
                )
                object.__setattr__(self, "status", "created")

            span.set_attribute("cap_task.hash", self.hash)
            span.set_attribute("cap_task.task_hash", self.task_hash)
            span.set_attribute("cap_task.cap_name", self.cap_name)
            span.set_attribute("cap_task.args_count", len(self.args))
            span.set_attribute("cap_task.approval", self.approval)
            span.set_attribute("cap_task.status", self.status)

            cap_task_created_counter.add(
                1,
                {
                    "cap_name": self.cap_name,
                    "approved": str(self.approval).lower(),
                    "status": self.status,
                },
            )
            logger.debug(
                f"Created CapTask: hash={self.hash}, cap_name={self.cap_name}, "
                f"approval={self.approval}, status={self.status}"
            )
            logger.info(
                f"CapTask created for capability '{self.cap_name}' with status '{self.status}'"
            )

            # Write file to todo_watch_dir
            self._write_file_to_watch_dir(span)

    def _write_file_to_watch_dir(self, parent_span: trace.Span):
        """
        Write the CapTask as JSON file to the todo_watch_dir.
        File name is <hash>.json.
        """
        try:
            # Ensure directory exists
            os.makedirs(todo_watch_dir, exist_ok=True)

            file_path = os.path.join(todo_watch_dir, f"{self.hash}.json")
            json_content = self.to_json(indent=2)

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(json_content)

            cap_task_file_written_counter.add(1, {"cap_name": self.cap_name})
            parent_span.set_attribute("cap_task.file_written_path", file_path)
            logger.debug(f"CapTask written to file: {file_path}")
            logger.info(f"CapTask file saved: {file_path}")

        except Exception as e:
            logger.error(f"Failed to write CapTask file for {self.hash}: {e}")
            parent_span.record_exception(e)
            # Do not re-raise; file writing is non-critical for object creation

    @tracer.start_as_current_span("CapTask.set_status")
    def set_status(self, new_status: str) -> None:
        """
        Update the CapTask status.

        Args:
            new_status: Must be one of VALID_STATUSES.

        Raises:
            ValueError: If the status is invalid.
        """
        current_span = trace.get_current_span()
        current_span.set_attribute("cap_task.hash", self.hash)
        current_span.set_attribute("cap_task.old_status", self.status)
        current_span.set_attribute("cap_task.new_status", new_status)

        if new_status not in self.VALID_STATUSES:
            error_msg = f"Invalid status '{new_status}'. Must be one of {self.VALID_STATUSES}"
            logger.error(error_msg)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
            raise ValueError(error_msg)

        old_status = self.status
        object.__setattr__(self, "status", new_status)
        cap_task_status_changed_counter.add(
            1,
            {
                "cap_name": self.cap_name,
                "old_status": old_status,
                "new_status": new_status,
            },
        )
        logger.info(f"CapTask {self.hash} status changed from '{old_status}' to '{new_status}'")

    def to_dict(self) -> dict[str, Any]:
        """Convert CapTask to a dictionary suitable for JSON serialization."""
        with tracer.start_as_current_span("CapTask.to_dict") as span:
            span.set_attribute("cap_task.hash", self.hash)
            data = asdict(self)
            # Optionally, we could exclude result if desired, but full_result is already included.
            logger.debug(f"Serialized CapTask {self.hash} to dict")
            return data

    def to_json(self, indent: int | None = None) -> str:
        """
        Serialize CapTask to a JSON string.

        Args:
            indent: Optional indentation for pretty printing.

        Returns:
            JSON string representation of the task.
        """
        with tracer.start_as_current_span("CapTask.to_json") as span:
            span.set_attribute("cap_task.hash", self.hash)
            json_str = json.dumps(self.to_dict(), indent=indent)
            cap_task_serialized_counter.add(1, {"cap_name": self.cap_name})
            logger.debug(f"Serialized CapTask {self.hash} to JSON (length={len(json_str)})")
            return json_str

    @classmethod
    @tracer.start_as_current_span("CapTask.from_dict")
    def from_dict(cls, data: dict[str, Any]) -> "CapTask":
        """
        Create a CapTask instance from a dictionary.

        Args:
            data: Dictionary containing 'cap_name', 'args', and optionally
                  'task_hash', 'hash', 'approval', 'status', 'full_result'.

        Returns:
            New CapTask instance.
        """
        current_span = trace.get_current_span()
        # Validate required fields
        if "cap_name" not in data or "args" not in data:
            error_msg = "Missing required fields: 'cap_name' and/or 'args'"
            logger.error(error_msg)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
            raise ValueError(error_msg)

        current_span.set_attribute("cap_task.cap_name", data["cap_name"])
        current_span.set_attribute("cap_task.args_count", len(data["args"]))

        task = cls(
            cap_name=data["cap_name"],
            args=data["args"],
            task_hash=data.get("task_hash"),
            hash=data.get("hash"),
            approval=data.get("approval", True),
            status=data.get("status", "created"),
            full_result=data.get("full_result", ""),
        )
        cap_task_deserialized_counter.add(1, {"cap_name": task.cap_name})
        logger.debug(
            f"Deserialized CapTask from dict: hash={task.hash}, "
            f"approval={task.approval}, status={task.status}"
        )
        return task

    @classmethod
    @tracer.start_as_current_span("CapTask.from_json")
    def from_json(cls, json_str: str) -> "CapTask":
        """
        Create a CapTask instance from a JSON string.

        Args:
            json_str: JSON string containing task data.

        Returns:
            New CapTask instance.
        """
        current_span = trace.get_current_span()
        try:
            data = json.loads(json_str)
            current_span.set_attribute("cap_task.json_length", len(json_str))
            logger.debug(f"Deserializing CapTask from JSON string of length {len(json_str)}")
            return cls.from_dict(data)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON for CapTask: {e}")
            current_span.record_exception(e)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, "JSON decode failed"))
            raise

    def __repr__(self) -> str:
        return (
            f"CapTask(hash='{self.hash}', cap_name='{self.cap_name}', "
            f"args_count={len(self.args)}, approval={self.approval}, status='{self.status}')"
        )


# =============================================================================
# Example usage:
#   This module is typically invoked from other parts of the system.
#   Below are common patterns for using CapTask in workflows or tests.
# =============================================================================
"""
Example usage:

    from cap_task import CapTask

    # 1. Creating a new task (automatically writes to todo_watch_dir/<hash>.json)
    task = CapTask(
        cap_name="send_email",
        args=["user@example.com", "Hello!"],
        task_hash="workflow-123",
        approval=True
    )

    # 2. Simulate capability execution and store result
    task.full_result = "Line 1\\nLine 2\\n... possibly thousands of lines ..."
    # Access first 500 lines
    preview = task.result

    # 3. Update task status after processing
    task.set_status("Processed")

    # 4. Serializing to JSON for message queuing or storage
    json_str = task.to_json()
    print(json_str)

    # 5. Deserializing from JSON (will also write a file to todo_watch_dir)
    restored_task = CapTask.from_json(json_str)

    # 6. Creating from a dictionary (e.g., from an API payload)
    data = {
        "cap_name": "run_report",
        "args": ["monthly", 42],
        "approval": False
    }
    task2 = CapTask.from_dict(data)

    # 7. Inspecting the task
    print(task2.hash)         # auto-generated UUID
    print(task2.status)       # 'created' (or 'Processed' if set)
    print(task2.approval)     # False
    print(task2.result)       # '' until full_result is set
"""
