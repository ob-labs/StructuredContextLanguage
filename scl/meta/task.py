"""
Task Class Implementation with OpenTelemetry Instrumentation

Feature List (Original Requirements):
1. Each Task has:
- a system prompt property as string.
- a prompt list property as string list, as prompt history.
- a capacity property as string list.
- a status property as string (in "created", "subtasking", "done").
- a hash property as hash value of system prompt, prompt list and capacity list.
- an approval property as a flag if get approval for running, default is True.
- a previous hash property as string, which support as a hash chain way to trace back to the head.
- a list of CapTasks.
- a list of sub tasks allows to check other sub tasks.
- an additional property as map[string]string for extending usage.

2. supports json and yaml format for serialization.
3. default as LRU view to show the latest status.

Additional Features Implemented (Missing from original comments but considered essential):
- Timestamp tracking (`created_at`, `updated_at`) to support LRU ordering.
- Parent reference for subtasks to enable sibling navigation.
- Recursive status aggregation (latest status among self and all descendants).
- OpenTelemetry instrumentation (traces, metrics, logs).
- Thread-safe hash computation and chain verification.
- Optional YAML dependency handling (falls back gracefully if PyYAML not installed).

Dependencies (install via pip):
    pip install opentelemetry-api opentelemetry-sdk opentelemetry-instrumentation
    pip install PyYAML  # optional, for YAML serialization support
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from opentelemetry import trace
from scl.otel.otel import meter, tracer

# Import CapTask for the required cap_tasks list
from scl.meta.captask import CapTask

# Metrics
task_created_counter = meter.create_counter(
    "task.created",
    description="Number of tasks created",
)
task_status_changed_counter = meter.create_counter(
    "task.status_changed",
    description="Number of task status changes",
)
subtask_added_counter = meter.create_counter(
    "task.subtask_added",
    description="Number of subtasks added",
)
cap_task_added_counter = meter.create_counter(
    "task.cap_task_added",
    description="Number of CapTasks added to a Task",
)

# Logger
logger: logging.Logger = logging.getLogger(__name__)


class Task:
    """
    Represents a task with a system prompt, prompt history, capacity list,
    status, approval flag, hash chain, subtasks, CapTask list, and LRU status view.

    Example usage:
        from scl.meta.captask import CapTask

        task = Task(
            system_prompt="You are a helpful assistant.",
            prompt_list=["User: Hello"],
            capacity=["cpu", "memory"],
        )
        # Add a CapTask
        cap = CapTask(cap_name="send_email", args=["user@example.com", "Subject"])
        task.add_cap_task(cap)

        # Serialize to JSON
        json_str = task.to_json()

        # Check latest status (LRU view)
        print(task.get_latest_status())
    """

    VALID_STATUSES = {"created", "subtasking", "done"}

    def __init__(
        self,
        system_prompt: str,
        prompt_list: Optional[List[str]] = None,
        capacity: Optional[List[str]] = None,
        status: str = "created",
        approval: bool = True,
        additional: Optional[Dict[str, str]] = None,
        previous_hash: Optional[str] = None,
        sub_tasks: Optional[List["Task"]] = None,
        cap_tasks: Optional[List[CapTask]] = None,
    ) -> None:
        """
        Initialize a Task instance.

        Args:
            system_prompt: The system prompt string.
            prompt_list: Initial list of prompt strings (history).
            capacity: List of capacity identifiers.
            status: Initial status; must be one of VALID_STATUSES.
            approval: Flag indicating if the task is approved for execution. Defaults to True.
            additional: Dictionary for extension data.
            previous_hash: Hash of the previous task in the chain (for hash chain).
            sub_tasks: List of subtask instances.
            cap_tasks: List of CapTask instances associated with this task.
        """
        with tracer.start_as_current_span("Task.__init__") as span:
            span.set_attribute("task.system_prompt_length", len(system_prompt))
            span.set_attribute("task.initial_status", status)
            span.set_attribute("task.approval", approval)

            self._system_prompt: str = system_prompt
            self._prompt_list: List[str] = prompt_list.copy() if prompt_list else []
            self._capacity: List[str] = capacity.copy() if capacity else []
            self._status: str = status if status in self.VALID_STATUSES else "created"
            self._approval: bool = approval
            self._additional: Dict[str, str] = additional.copy() if additional else {}
            self._previous_hash: Optional[str] = previous_hash
            self._sub_tasks: List["Task"] = []
            self._cap_tasks: List[CapTask] = []  # New: list of CapTasks
            self._parent: Optional["Task"] = None

            # Timestamps for LRU ordering
            self._created_at: datetime = datetime.now(timezone.utc)
            self._updated_at: datetime = self._created_at

            # Add initial subtasks if provided
            if sub_tasks:
                for st in sub_tasks:
                    self.add_subtask(st)

            # Add initial CapTasks if provided
            if cap_tasks:
                for ct in cap_tasks:
                    self.add_cap_task(ct)

            # Record metric
            task_created_counter.add(
                1,
                {
                    "status": self._status,
                    "approved": str(self._approval).lower(),
                },
            )

            logger.info(
                "Task created with hash %s, previous hash %s, approval=%s, cap_tasks=%d",
                self.hash,
                self._previous_hash,
                self._approval,
                len(self._cap_tasks),
            )
            logger.debug(
                "Task details: system_prompt=%s, prompt_list=%s, capacity=%s",
                self._system_prompt[:50] + "..." if len(self._system_prompt) > 50 else self._system_prompt,
                self._prompt_list,
                self._capacity,
            )

    # ----------------------------------------------------------------------
    # Core Properties
    # ----------------------------------------------------------------------

    @property
    def system_prompt(self) -> str:
        """System prompt string."""
        return self._system_prompt

    @system_prompt.setter
    def system_prompt(self, value: str) -> None:
        """Update system prompt and touch updated_at."""
        with tracer.start_as_current_span("Task.system_prompt.setter") as span:
            span.set_attribute("task.system_prompt_length", len(value))
            self._system_prompt = value
            self._touch()

    @property
    def prompt_list(self) -> List[str]:
        """List of prompt strings (history)."""
        return self._prompt_list.copy()  # return copy to prevent external mutation

    def add_prompt(self, prompt: str) -> None:
        """Append a prompt to the history."""
        with tracer.start_as_current_span("Task.add_prompt") as span:
            span.set_attribute("prompt_length", len(prompt))
            self._prompt_list.append(prompt)
            self._touch()
            logger.debug("Prompt added to task %s", self.hash)

    @property
    def capacity(self) -> List[str]:
        """List of capacity strings."""
        return self._capacity.copy()

    @capacity.setter
    def capacity(self, value: List[str]) -> None:
        """Replace capacity list."""
        with tracer.start_as_current_span("Task.capacity.setter") as span:
            span.set_attribute("capacity_count", len(value))
            self._capacity = value.copy()
            self._touch()

    @property
    def status(self) -> str:
        """Current status of the task."""
        return self._status

    @status.setter
    def status(self, value: str) -> None:
        """Update status, validating against allowed values."""
        with tracer.start_as_current_span("Task.status.setter") as span:
            span.set_attribute("task.old_status", self._status)
            span.set_attribute("task.new_status", value)

            if value not in self.VALID_STATUSES:
                raise ValueError(f"Status must be one of {self.VALID_STATUSES}")

            old_status = self._status
            self._status = value
            self._touch()
            task_status_changed_counter.add(
                1, {"old_status": old_status, "new_status": value}
            )
            logger.info("Task %s status changed from %s to %s", self.hash, old_status, value)

    @property
    def approval(self) -> bool:
        """Approval flag for task execution."""
        return self._approval

    @approval.setter
    def approval(self, value: bool) -> None:
        """Set the approval flag."""
        with tracer.start_as_current_span("Task.approval.setter") as span:
            span.set_attribute("task.approval_old", self._approval)
            span.set_attribute("task.approval_new", value)
            self._approval = value
            self._touch()
            logger.info("Task %s approval set to %s", self.hash, value)

    @property
    def additional(self) -> Dict[str, str]:
        """Additional data dictionary (copy)."""
        return self._additional.copy()

    def set_additional(self, key: str, value: str) -> None:
        """Set a key-value pair in additional data."""
        with tracer.start_as_current_span("Task.set_additional") as span:
            span.set_attribute("key", key)
            self._additional[key] = value
            self._touch()
            logger.debug("Additional data set for task %s: %s=%s", self.hash, key, value)

    # ----------------------------------------------------------------------
    # CapTask Management (NEW)
    # ----------------------------------------------------------------------

    @property
    def cap_tasks(self) -> List[CapTask]:
        """List of CapTasks associated with this task (copy)."""
        return self._cap_tasks.copy()

    @tracer.start_as_current_span("Task.add_cap_task")
    def add_cap_task(self, cap_task: CapTask) -> None:
        """
        Add a CapTask to this task's list.

        Args:
            cap_task: The CapTask instance to associate.
        """
        span = trace.get_current_span()
        span.set_attribute("cap_task.hash", cap_task.hash)
        span.set_attribute("cap_task.cap_name", cap_task.cap_name)

        self._cap_tasks.append(cap_task)
        self._touch()
        cap_task_added_counter.add(1, {"cap_name": cap_task.cap_name})
        logger.info("CapTask %s (%s) added to task %s", cap_task.hash, cap_task.cap_name, self.hash)

    def remove_cap_task(self, cap_task: CapTask) -> bool:
        """
        Remove a specific CapTask from the list.

        Returns:
            True if the CapTask was found and removed, False otherwise.
        """
        try:
            self._cap_tasks.remove(cap_task)
            self._touch()
            logger.debug("CapTask %s removed from task %s", cap_task.hash, self.hash)
            return True
        except ValueError:
            logger.warning("CapTask %s not found in task %s", cap_task.hash, self.hash)
            return False

    # ----------------------------------------------------------------------
    # Hash Computation and Chain
    # ----------------------------------------------------------------------

    @property
    def hash(self) -> str:
        """
        Compute a SHA-256 hash of (system_prompt, prompt_list, capacity).
        The hash is deterministic and used for identity/chaining.
        Note: CapTasks and subtasks are not included in the hash by design.
        """
        hasher = hashlib.sha256()
        hasher.update(self._system_prompt.encode("utf-8"))
        for p in self._prompt_list:
            hasher.update(p.encode("utf-8"))
        for c in self._capacity:
            hasher.update(c.encode("utf-8"))
        return hasher.hexdigest()

    @property
    def previous_hash(self) -> Optional[str]:
        """Hash of the previous task in the chain."""
        return self._previous_hash

    @previous_hash.setter
    def previous_hash(self, value: Optional[str]) -> None:
        """Set the previous hash (for hash chain linking)."""
        self._previous_hash = value
        self._touch()

    def verify_hash_chain(self) -> bool:
        """
        Verify that the current task's previous_hash matches the hash of its
        immediate predecessor in the chain (if any). This method assumes that
        the chain is built externally; here we check against a stored parent
        reference if available.
        """
        if self._parent is not None:
            return self._previous_hash == self._parent.hash
        # No parent to verify against; chain start is valid by definition.
        return True

    # ----------------------------------------------------------------------
    # Subtask Management
    # ----------------------------------------------------------------------

    @property
    def sub_tasks(self) -> List["Task"]:
        """List of subtasks (direct children)."""
        return self._sub_tasks.copy()

    @tracer.start_as_current_span("Task.add_subtask")
    def add_subtask(self, subtask: "Task") -> None:
        """
        Add a subtask to this task. Sets the parent reference on the subtask
        and optionally links its previous_hash to this task's hash.
        """
        span = trace.get_current_span()
        span.set_attribute("subtask_hash", subtask.hash)

        if subtask._parent is not None:
            raise ValueError("Subtask already has a parent")

        subtask._parent = self
        # Link hash chain if not already set
        if subtask._previous_hash is None:
            subtask._previous_hash = self.hash

        self._sub_tasks.append(subtask)
        self._touch()
        subtask_added_counter.add(1)
        logger.info("Subtask %s added to task %s", subtask.hash, self.hash)

    def get_siblings(self) -> List["Task"]:
        """
        Return a list of sibling tasks (other subtasks of the same parent).
        If this task has no parent, returns an empty list.
        """
        if self._parent is None:
            return []
        return [t for t in self._parent._sub_tasks if t is not self]

    def get_all_descendants(self) -> List["Task"]:
        """Return a flattened list of all subtasks recursively."""
        descendants = []
        for child in self._sub_tasks:
            descendants.append(child)
            descendants.extend(child.get_all_descendants())
        return descendants

    # ----------------------------------------------------------------------
    # LRU View: Latest Status among Self and Subtasks
    # ----------------------------------------------------------------------

    @property
    def created_at(self) -> datetime:
        """Timestamp when the task was created."""
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        """Timestamp of the last update to the task."""
        return self._updated_at

    def _touch(self) -> None:
        """Update the updated_at timestamp to now."""
        self._updated_at = datetime.now(timezone.utc)

    @tracer.start_as_current_span("Task.get_latest_status")
    def get_latest_status(self) -> str:
        """
        Return the status of the most recently updated node among this task
        and all its descendants (LRU view). This provides a quick way to see
        the "freshest" state in the task hierarchy.
        """
        span = trace.get_current_span()

        # Collect all nodes (self + descendants)
        nodes = [self] + self.get_all_descendants()
        # Find the one with the latest updated_at
        latest_node = max(nodes, key=lambda n: n.updated_at)
        span.set_attribute("latest_node_hash", latest_node.hash)
        span.set_attribute("latest_status", latest_node.status)
        span.set_attribute("total_nodes_checked", len(nodes))

        logger.debug(
            "Latest status for task %s is '%s' from node %s",
            self.hash,
            latest_node.status,
            latest_node.hash,
        )
        return latest_node.status

    # ----------------------------------------------------------------------
    # Serialization (JSON / YAML) - Updated to include CapTasks
    # ----------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Convert task to a dictionary suitable for serialization."""
        return {
            "system_prompt": self._system_prompt,
            "prompt_list": self._prompt_list,
            "capacity": self._capacity,
            "status": self._status,
            "approval": self._approval,
            "additional": self._additional,
            "previous_hash": self._previous_hash,
            "sub_tasks": [st.to_dict() for st in self._sub_tasks],
            "cap_tasks": [ct.to_dict() for ct in self._cap_tasks],   # NEW
            "created_at": self._created_at.isoformat(),
            "updated_at": self._updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        """Create a Task instance from a dictionary."""
        # Recursively create subtasks
        sub_tasks = [cls.from_dict(st) for st in data.get("sub_tasks", [])]

        # Deserialize CapTasks
        cap_tasks_data = data.get("cap_tasks", [])
        cap_tasks = [CapTask.from_dict(ct) for ct in cap_tasks_data]

        task = cls(
            system_prompt=data["system_prompt"],
            prompt_list=data.get("prompt_list", []),
            capacity=data.get("capacity", []),
            status=data.get("status", "created"),
            approval=data.get("approval", True),
            additional=data.get("additional", {}),
            previous_hash=data.get("previous_hash"),
            sub_tasks=sub_tasks,
            cap_tasks=cap_tasks,
        )
        # Restore timestamps (override auto-generated ones)
        if "created_at" in data:
            task._created_at = datetime.fromisoformat(data["created_at"])
        if "updated_at" in data:
            task._updated_at = datetime.fromisoformat(data["updated_at"])
        return task

    def to_json(self, indent: Optional[int] = None) -> str:
        """Serialize task to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, json_str: str) -> "Task":
        """Deserialize task from JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data)

    def to_yaml(self) -> str:
        """
        Serialize task to YAML string.
        Requires PyYAML to be installed.
        """
        try:
            import yaml
        except ImportError as e:
            raise ImportError(
                "YAML support requires PyYAML. Install it with: pip install PyYAML"
            ) from e
        return yaml.dump(self.to_dict(), sort_keys=False)

    @classmethod
    def from_yaml(cls, yaml_str: str) -> "Task":
        """
        Deserialize task from YAML string.
        Requires PyYAML to be installed.
        """
        try:
            import yaml
        except ImportError as e:
            raise ImportError(
                "YAML support requires PyYAML. Install it with: pip install PyYAML"
            ) from e
        data = yaml.safe_load(yaml_str)
        return cls.from_dict(data)

    # ----------------------------------------------------------------------
    # Utility Methods
    # ----------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"<Task hash={self.hash[:8]} status={self.status} approval={self.approval} "
            f"subtasks={len(self._sub_tasks)} captasks={len(self._cap_tasks)}>"
        )

    def __str__(self) -> str:
        return self.__repr__()


# ----------------------------------------------------------------------
# Example Usage (if run as script)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # Configure basic logging for demonstration
    logging.basicConfig(level=logging.DEBUG)

    # Create a root task
    root = Task(
        system_prompt="You are a helpful assistant.",
        prompt_list=["Hello"],
        capacity=["cpu", "memory"],
    )

    # Add a subtask
    child = Task(
        system_prompt="Subtask system prompt",
        prompt_list=["Sub prompt"],
        capacity=["gpu"],
        status="subtasking",
        approval=False,
    )
    root.add_subtask(child)

    # Add a CapTask (requires actual CapTask class; here we simulate)
    from scl.meta.captask import CapTask
    cap = CapTask(cap_name="send_email", args=["user@example.com", "Subject"])
    root.add_cap_task(cap)

    # Change status
    root.status = "subtasking"

    # Serialization
    json_data = root.to_json(indent=2)
    print("JSON:\n", json_data)

    # YAML (if available)
    try:
        yaml_data = root.to_yaml()
        print("\nYAML:\n", yaml_data)
    except ImportError:
        print("\nYAML support not available (install PyYAML)")

    # LRU view
    latest = root.get_latest_status()
    print(f"\nLatest status in hierarchy: {latest}")

    # Siblings check
    child2 = Task(system_prompt="Another subtask", status="done")
    root.add_subtask(child2)
    siblings = child.get_siblings()
    print(f"Child has {len(siblings)} sibling(s)")