"""
Task definition module for the Structured Context Language project
It's a queue to hold tasks for processing
It has method for just receive and return the tasks
If the queue is empty, return None
It can be registered by TaskProcessor instance to process tasks.
if TaskProcessor instance been registered, it will call task_processor.notify() after new item into queue.
TaskProcessor been impls in other class already
"""

import logging
import queue
from typing import Any, Optional

from opentelemetry import trace
from scl.otel.otel import meter, tracer

logger = logging.getLogger(__name__)


class TaskQueue:
    """A queue wrapper with tracing, metrics, and processor notification."""

    def __init__(self):
        """
        Initialize the queue, metrics, and processor reference.
        """
        self._queue = queue.Queue()
        self._processor: Optional[Any] = None  # Will hold a TaskProcessor instance

        self.task_enqueue_counter = meter.create_counter(
            "task_enqueue", description="Number of items added to the queue"
        )

        self.task_dequeue_counter = meter.create_counter(
            "task_dequeue", description="Number of items removed from the queue"
        )

        logger.info("TaskQueue initialized")

    def register_processor(self, processor: Any) -> None:
        """
        Register a TaskProcessor instance that will be notified when new items are added.

        :param processor: An object that implements a notify() method.
        """
        self._processor = processor
        logger.info(f"TaskProcessor registered: {processor.__class__.__name__}")

    @tracer.start_as_current_span("add task to queue")
    def add(self, item: Any) -> None:
        """
        Add an item to the queue, record metrics, and notify the registered processor.

        :param item: The item to add.
        """
        current_span = trace.get_current_span()
        current_span.set_attribute("queue.item", str(item))

        self._queue.put(item)
        self.task_enqueue_counter.add(1)

        queue_size = self._queue.qsize()
        current_span.set_attribute("queue.size.after_add", queue_size)

        logger.debug(f"Item added to queue. Current size: {queue_size}")

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

    @tracer.start_as_current_span("get task from queue")
    def get(self, block: bool = True, timeout: Optional[float] = None) -> Optional[Any]:
        """
        Retrieve an item from the queue. Returns None if the queue is empty.

        :param block: Whether to block until an item is available.
        :param timeout: Maximum time to wait (seconds) if blocking.
        :return: The item, or None if the queue is empty.
        """
        current_span = trace.get_current_span()
        try:
            item = self._queue.get(block=block, timeout=timeout)
            current_span.set_attribute("queue.item", str(item))
            self.task_dequeue_counter.add(1)

            queue_size = self._queue.qsize()
            current_span.set_attribute("queue.size.after_get", queue_size)

            logger.debug(f"Item retrieved from queue. Remaining size: {queue_size}")
            return item
        except queue.Empty:
            current_span.set_attribute("queue.empty", True)
            logger.debug("Queue is empty, returning None")
            return None