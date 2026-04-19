"""
CapTask Module

Design Goals & Features:
------------------------
1. Each CapTask has:
   - hash: unique identifier for this task instance.
   - task_hash: identifier of the parent task or workflow this task belongs to.
   - cap_name: name of the Capability to be invoked.
   - args: list of arguments to pass to the capability invocation.
   - approval: a flag if get approval for running, default is True.
   - status: current status of the CapTask in ["created", "Processed", "Error"]
2. Support JSON serialization/deserialization for persistence or message passing.

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
import uuid
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

# OpenTelemetry imports
from opentelemetry import trace
from scl.otel.otel import tracer, meter

# Setup logger
logger = logging.getLogger(__name__)

# Setup metrics
cap_task_created_counter = meter.create_counter(
    "cap_task.created",
    description="Number of CapTask instances created"
)
cap_task_serialized_counter = meter.create_counter(
    "cap_task.serialized",
    description="Number of times a CapTask was serialized to JSON"
)
cap_task_deserialized_counter = meter.create_counter(
    "cap_task.deserialized",
    description="Number of times a CapTask was deserialized from JSON"
)
cap_task_status_changed_counter = meter.create_counter(
    "cap_task.status_changed",
    description="Number of times a CapTask status changed"
)


@dataclass
class CapTask:
    """
    Represents a single invocation task for a Capability.

    Example usage:
        task = CapTask(
            cap_name="send_email",
            args=["user@example.com", "Hello!"],
            task_hash="workflow-123",
            approval=True
        )
        # Update status after processing
        task.set_status("Processed")

        json_str = task.to_json()
        restored = CapTask.from_json(json_str)
    """

    cap_name: str
    args: List[Any]
    task_hash: Optional[str] = None
    hash: Optional[str] = None   # Will be auto-generated if not provided
    approval: bool = True        # Flag indicating if the task is approved for execution
    status: str = "created"      # Current status; allowed: created, Processed, Error

    VALID_STATUSES = {"created", "Processed", "Error"}

    def __post_init__(self):
        """Auto-generate unique hash if not supplied, set default task_hash, and validate status."""
        with tracer.start_as_current_span("CapTask.__post_init__") as span:
            if self.hash is None:
                # Use object.__setattr__ because dataclass fields are frozen in spirit
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

            cap_task_created_counter.add(1, {
                "cap_name": self.cap_name,
                "approved": str(self.approval).lower(),
                "status": self.status,
            })
            logger.debug(
                f"Created CapTask: hash={self.hash}, cap_name={self.cap_name}, "
                f"approval={self.approval}, status={self.status}"
            )
            logger.info(f"CapTask created for capability '{self.cap_name}' with status '{self.status}'")

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
        cap_task_status_changed_counter.add(1, {
            "cap_name": self.cap_name,
            "old_status": old_status,
            "new_status": new_status,
        })
        logger.info(f"CapTask {self.hash} status changed from '{old_status}' to '{new_status}'")

    def to_dict(self) -> Dict[str, Any]:
        """Convert CapTask to a dictionary suitable for JSON serialization."""
        with tracer.start_as_current_span("CapTask.to_dict") as span:
            span.set_attribute("cap_task.hash", self.hash)
            data = asdict(self)
            logger.debug(f"Serialized CapTask {self.hash} to dict")
            return data

    def to_json(self, indent: Optional[int] = None) -> str:
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
    def from_dict(cls, data: Dict[str, Any]) -> "CapTask":
        """
        Create a CapTask instance from a dictionary.

        Args:
            data: Dictionary containing 'cap_name', 'args', and optionally
                  'task_hash', 'hash', 'approval', 'status'.

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
            status=data.get("status", "created")
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