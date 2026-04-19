"""
Capability Task Queues Module

Design Goals & Features:
------------------------
1. Reference CapTask from scl.meta.CapTask (for type awareness).
2. A hash map based class, keyed by capability name.
3. Expose an add(task) method for other code to queue invocation data.
4. For each queue, able to be registered with a notice function.
5. Once added, if there is a notice function, then it will be invoked.
6. Allow consumption of queued tasks by capability name.
7. During consumption, if the queue is empty, return None.

Project Constraints Applied:
----------------------------
- OpenTelemetry integrated for tracing, metrics, and structured logging.
- Logger provides info and debug levels.
- Dependencies are documented as `pip install` commands, not requirements.txt.

Installation:
    pip install opentelemetry-api opentelemetry-sdk
"""
import logging
from collections import defaultdict
from threading import Lock
from typing import Optional, Any, Dict, List, Callable

# OpenTelemetry imports
from opentelemetry import trace
from scl.otel.otel import tracer, meter

# Reference CapTask for type awareness
from scl.meta.captask import CapTask

# Setup logger
logger = logging.getLogger(__name__)

# Setup metrics
task_added_counter = meter.create_counter(
    "capability_task.added",
    description="Number of CapTasks added to CapabilityTaskQueues"
)
task_consumed_counter = meter.create_counter(
    "capability_task.consumed",
    description="Number of CapTasks consumed from CapabilityTaskQueues"
)
notifier_registered_counter = meter.create_counter(
    "capability_task.notifier.registered",
    description="Number of notifier functions registered"
)
notifier_invoked_counter = meter.create_counter(
    "capability_task.notifier.invoked",
    description="Number of times a notifier function was invoked"
)


class CapabilityTaskQueues:
    """
    A thread‑safe hashmap‑based queue for CapTask objects, keyed by capability name.
    Supports registering a notifier function per capability name, which will be
    called whenever a new CapTask is added to that queue.

    Example usage:
        queues = CapabilityTaskQueues()
        queues.register_notifier("send_email", lambda name, task: print(f"New task: {task}"))
        task = CapTask(cap_name="send_email", args=["user@example.com", "Subject"])
        queues.add(task)
        consumed = queues.consume("send_email")  # returns the CapTask
    """

    def __init__(self):
        # Internal storage: dict of lists, each list holds CapTask objects for a capability name.
        self._queues: Dict[str, List[CapTask]] = defaultdict(list)
        # Notifier functions per capability name: signature (name: str, task: CapTask) -> None
        self._notifiers: Dict[str, Callable[[str, CapTask], None]] = {}
        self._lock = Lock()

        logger.info("CapabilityTaskQueues initialized")

    @tracer.start_as_current_span("CapabilityTaskQueues.register_notifier")
    def register_notifier(self, name: str, callback: Callable[[str, CapTask], None]) -> None:
        """
        Register a notifier function for a specific capability name.
        The callback will be invoked whenever a new CapTask is added to the queue
        of that capability.

        Args:
            name: Capability name to associate the notifier with.
            callback: A callable that takes two arguments: (name, task).
        """
        current_span = trace.get_current_span()
        current_span.set_attribute("capability.name", name)

        with self._lock:
            self._notifiers[name] = callback
            notifier_registered_counter.add(1, {"capability.name": name})

        logger.debug(f"Registered notifier for capability '{name}'")
        logger.info(f"Notifier registered for capability '{name}'")

    @tracer.start_as_current_span("CapabilityTaskQueues.unregister_notifier")
    def unregister_notifier(self, name: str) -> bool:
        """
        Remove the notifier function for a given capability name.

        Args:
            name: Capability name.

        Returns:
            True if a notifier was removed, False if none existed.
        """
        current_span = trace.get_current_span()
        current_span.set_attribute("capability.name", name)

        with self._lock:
            removed = self._notifiers.pop(name, None) is not None

        if removed:
            logger.debug(f"Unregistered notifier for capability '{name}'")
            logger.info(f"Notifier unregistered for capability '{name}'")
        else:
            logger.debug(f"No notifier found to unregister for capability '{name}'")

        current_span.set_attribute("capability.notifier.removed", removed)
        return removed

    @tracer.start_as_current_span("CapabilityTaskQueues.add")
    def add(self, task: CapTask) -> None:
        """
        Add a new CapTask to the queue corresponding to its capability name.
        If a notifier function is registered for this capability, it will be
        called immediately after the task is queued.

        Args:
            task: The CapTask instance to enqueue.
        """
        name = task.cap_name
        current_span = trace.get_current_span()
        current_span.set_attribute("capability.name", name)
        current_span.set_attribute("cap_task.hash", task.hash)
        current_span.set_attribute("cap_task.args_count", len(task.args))

        notifier: Optional[Callable] = None
        with self._lock:
            self._queues[name].append(task)
            task_added_counter.add(1, {"capability.name": name})
            queue_length = len(self._queues[name])
            # Retrieve notifier (if any) to invoke outside the lock to avoid deadlocks
            notifier = self._notifiers.get(name)

        logger.debug(f"Added CapTask {task.hash} to queue for capability '{name}' (queue length: {queue_length})")

        # Invoke notifier if present, outside the lock for safety
        if notifier:
            try:
                with tracer.start_as_current_span("CapabilityTaskQueues.notifier.invoke") as notify_span:
                    notify_span.set_attribute("capability.name", name)
                    notify_span.set_attribute("cap_task.hash", task.hash)
                    notifier_invoked_counter.add(1, {"capability.name": name})
                    logger.debug(f"Invoking notifier for capability '{name}' with task {task.hash}")
                    notifier(name, task)
            except Exception as e:
                logger.error(f"Notifier for capability '{name}' raised an exception: {e}", exc_info=True)
                # Do not re-raise to avoid disrupting the add operation
                current_span.record_exception(e)
                current_span.set_attribute("capability.notifier.error", True)

    @tracer.start_as_current_span("CapabilityTaskQueues.consume")
    def consume(self, name: str) -> Optional[CapTask]:
        """
        Consume (pop) the oldest pending CapTask for the given capability name.

        Args:
            name: The capability name to consume from.

        Returns:
            The earliest CapTask added, or None if no tasks are queued for this name.
        """
        current_span = trace.get_current_span()
        current_span.set_attribute("capability.name", name)

        with self._lock:
            queue = self._queues.get(name)
            if not queue:
                logger.debug(f"No tasks available for capability '{name}'")
                current_span.set_attribute("capability.task.available", False)
                return None

            task = queue.pop(0)
            task_consumed_counter.add(1, {"capability.name": name})
            remaining = len(queue)
            # Clean up empty list to keep memory tidy
            if not queue:
                del self._queues[name]

        logger.debug(f"Consumed CapTask {task.hash} for capability '{name}' (remaining: {remaining})")
        current_span.set_attribute("capability.task.available", True)
        current_span.set_attribute("capability.task.remaining", remaining)
        current_span.set_attribute("cap_task.hash", task.hash)
        return task