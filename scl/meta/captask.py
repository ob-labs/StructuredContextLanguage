"""
CapTask Module

Design Goals & Features:
------------------------
1. Each CapTask has:
   - hash: unique identifier for this task instance.
   - task_hash: identifier of the parent task or workflow this task belongs to.
   - cap_name: name of the Capability to be invoked.
   - args: list of arguments to pass to the capability invocation.
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


@dataclass
class CapTask:
    """
    Represents a single invocation task for a Capability.
    Immutable once created (dataclass with frozen=True ensures hashability).
    """

    cap_name: str
    args: List[Any]
    task_hash: Optional[str] = None
    hash: str = None  # Will be auto-generated if not provided

    def __post_init__(self):
        """Auto-generate unique hash if not supplied, and set default task_hash."""
        with tracer.start_as_current_span("CapTask.__post_init__") as span:
            if self.hash is None:
                self.hash = str(uuid.uuid4())
                span.set_attribute("cap_task.hash_generated", True)
            else:
                span.set_attribute("cap_task.hash_generated", False)

            if self.task_hash is None:
                self.task_hash = "default"

            span.set_attribute("cap_task.hash", self.hash)
            span.set_attribute("cap_task.task_hash", self.task_hash)
            span.set_attribute("cap_task.cap_name", self.cap_name)
            span.set_attribute("cap_task.args_count", len(self.args))

            cap_task_created_counter.add(1, {"cap_name": self.cap_name})
            logger.debug(f"Created CapTask: hash={self.hash}, cap_name={self.cap_name}")
            logger.info(f"CapTask created for capability '{self.cap_name}'")

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
            data: Dictionary containing 'cap_name', 'args', and optionally 'task_hash' and 'hash'.

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
            hash=data.get("hash")
        )
        cap_task_deserialized_counter.add(1, {"cap_name": task.cap_name})
        logger.debug(f"Deserialized CapTask from dict: hash={task.hash}")
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
        return f"CapTask(hash='{self.hash}', cap_name='{self.cap_name}', args_count={len(self.args)})"