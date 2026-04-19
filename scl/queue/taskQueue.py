"""
Task definition module for the Structured Context Language project
It's a queue to hold tasks for processing
It has method for just receive and return the tasks
If the queue is empty, return None
It can be registered by TaskProcessor instance to process tasks.
if TaskProcessor instance been registered, it will call task_processor.notify() after new item into queue.
TaskProcessor been impls in other class already
Task Queue ensure dequeue as Task instance
"""

import logging
import queue
from typing import Optional, TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.metrics import Observation

from scl.otel.otel import meter, tracer
from scl.meta.task import Task  # Actual Task class import

if TYPE_CHECKING:
    from scl.processor.task_processor import TaskProcessor

logger = logging.getLogger(__name__)


class TaskQueue:
    """
    A thread-safe queue wrapper for Task instances with tracing, metrics,
    and automatic notification of a registered TaskProcessor.
    """

    def __init__(self):
        """
        Initialize the queue, metrics, and processor reference.
        """
        self._queue: queue.Queue[Task] = queue.Queue()
        self._processor: Optional["TaskProcessor"] = None

        # Metrics
        self.task_enqueue_counter = meter.create_counter(
            "task_enqueue",
            description="Number of tasks added to the queue"
        )
        self.task_dequeue_counter = meter.create_counter(
            "task_dequeue",
            description="Number of tasks removed from the queue"
        )
        self.queue_size_gauge = meter.create_observable_gauge(
            "task_queue_size",
            callbacks=[self._get_queue_size],
            description="Current number of tasks in the queue"
        )

        logger.info("TaskQueue initialized")

    def _get_queue_size(self, options):
        """Callback for observable gauge to report current queue size."""
        yield Observation(self._queue.qsize())

    def register_processor(self, processor: "TaskProcessor") -> None:
        """
        Register a TaskProcessor instance that will be notified when new tasks are added.

        :param processor: An object that implements a notify() method.
        """
        self._processor = processor
        logger.info(f"TaskProcessor registered: {processor.__class__.__name__}")

    @tracer.start_as_current_span("task_queue_add")
    def add(self, task: Task) -> None:
        """
        Add a Task instance to the queue, record metrics, and notify the registered processor.

        :param task: The Task instance to enqueue.
        :raises TypeError: If the item is not a Task instance.
        """
        if not isinstance(task, Task):
            raise TypeError(f"Expected Task instance, got {type(task).__name__}")

        current_span = trace.get_current_span()
        current_span.set_attribute("task.id", str(getattr(task, 'id', 'unknown')))
        current_span.set_attribute("task.type", getattr(task, 'type', 'unknown'))

        self._queue.put(task)
        self.task_enqueue_counter.add(1)

        queue_size = self._queue.qsize()
        current_span.set_attribute("queue.size.after_add", queue_size)

        logger.debug(f"Task {getattr(task, 'id', '?')} added to queue. Current size: {queue_size}")

        # Notify the registered processor, if any
        if self._processor is not None:
            logger.debug("Notifying registered TaskProcessor")
            try:
                self._processor.notify()
            except Exception as e:
                logger.error(f"Error while notifying TaskProcessor: {e}", exc_info=True)
                current_span.record_exception(e)
        else:
            logger.debug("No TaskProcessor registered; skipping notification")

    @tracer.start_as_current_span("task_queue_get")
    def get(self, block: bool = True, timeout: Optional[float] = None) -> Optional[Task]:
        """
        Retrieve a Task from the queue. Returns None if the queue is empty.

        :param block: Whether to block until a task is available.
        :param timeout: Maximum time to wait (seconds) if blocking.
        :return: The Task instance, or None if the queue is empty.
        """
        current_span = trace.get_current_span()
        try:
            task = self._queue.get(block=block, timeout=timeout)
            current_span.set_attribute("task.id", str(getattr(task, 'id', 'unknown')))
            current_span.set_attribute("task.type", getattr(task, 'type', 'unknown'))
            self.task_dequeue_counter.add(1)

            queue_size = self._queue.qsize()
            current_span.set_attribute("queue.size.after_get", queue_size)

            logger.debug(f"Task {getattr(task, 'id', '?')} retrieved from queue. Remaining size: {queue_size}")
            return task

        except queue.Empty:
            current_span.set_attribute("queue.empty", True)
            logger.debug("Queue is empty, returning None")
            return None

    def qsize(self) -> int:
        """Return the current size of the queue (non-blocking)."""
        return self._queue.qsize()