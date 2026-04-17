"""
Internal Watcher for Task Items
Adds Task instances to TaskQueue from internal sources (e.g., scheduled jobs, programmatic events).
"""

import logging
from typing import TYPE_CHECKING

from opentelemetry import trace

from scl.meta.taskQueue import TaskQueue
from scl.meta.task import Task  # Import Task class for type checking
from scl.otel.otel import tracer, meter

if TYPE_CHECKING:
    # For type hints without circular imports
    pass

logger = logging.getLogger(__name__)


class InternalWatcher:
    """
    A simple internal watcher that accepts Task instances and enqueues them
    for processing by a TaskProcessor.
    """

    def __init__(self, todo_queue: TaskQueue):
        """
        Initialize the internal watcher with a reference to the task queue.

        :param todo_queue: The TaskQueue instance where tasks will be added.
        """
        self.todo_queue = todo_queue
        self.logger = logging.getLogger(__name__)

        # Metrics
        self.internal_task_counter = meter.create_counter(
            "internal_task_add",
            description="Number of internal tasks added to the queue"
        )
        self.internal_task_error_counter = meter.create_counter(
            "internal_task_error",
            description="Number of errors while adding internal tasks"
        )

        self.logger.info("InternalWatcher initialized")

    @tracer.start_as_current_span("internal_watcher_add_task")
    def add(self, task: Task) -> None:
        """
        Add a Task instance to the queue.

        :param task: The Task object to enqueue.
        :raises TypeError: If the provided item is not a Task instance.
        :raises Exception: If the queue operation fails (logged and re‑raised).
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

        # Enrich span with task metadata
        task_id = getattr(task, 'id', 'unknown')
        task_type = getattr(task, 'type', 'unknown')
        current_span.set_attribute("task.id", str(task_id))
        current_span.set_attribute("task.type", task_type)

        self.logger.debug(f"Internally generated task received: id={task_id}, type={task_type}")

        try:
            self.todo_queue.add(task)
            self.internal_task_counter.add(1)
            self.logger.info(f"Internal task {task_id} added to queue successfully")
        except Exception as e:
            self.logger.error(f"Failed to add internal task {task_id} to queue: {e}", exc_info=True)
            current_span.record_exception(e)
            self.internal_task_error_counter.add(1)
            raise