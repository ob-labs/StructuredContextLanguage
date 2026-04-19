"""
awaitingApproveProcessor Module

Design Goals & Features:
- It will consume an AwaitingApproveQueue as queue instance.
- It use a while true to consume Task or CapTask instance from the queue.
- If the item is empty then double the wait time for the queue and the max sleep time is 300s.
- It allows status check, if the wait time equal or over 16s then the status been set to "idle", otherwise set to "normal".
- It has an event method for notification, allows other components invoke.
-   When the status is "idle" and the method been invoked, start a new round of get from queue immediately.
-   When the status is "normal" and the method been invoked, do nothing.
- If the item is not empty then check if it's approal status is True.
- if it not got approal, return it back to AwaitingApproveQueue.
- If it got approal and it's task, move it to TaskQueue.
- If it got approal and it's CapTask, move it to CapabilityTaskQueues.

Project Constraints:
- Please relay on otel for tracing, metric, logs.
- Please design log for info and debug level.
- Please have example usage as comments after class define, before init function.
- Just impl necessary functions.
"""

import logging
import threading
import time
from typing import Optional, Union

from opentelemetry import trace
from scl.otel.otel import meter, tracer

# Project imports
from scl.queue.awaitingApproveQueue import AwaitingApproveQueue
from scl.meta.task import Task
from scl.meta.captask import CapTask
from scl.queue.taskQueue import TaskQueue
from scl.queue.capabilityTaskQueues import CapabilityTaskQueues

logger = logging.getLogger(__name__)

# Metrics
items_consumed_counter = meter.create_counter(
    "awaiting_approve_processor.items_consumed",
    description="Number of items consumed from AwaitingApproveQueue"
)
items_requeued_counter = meter.create_counter(
    "awaiting_approve_processor.items_requeued",
    description="Number of items returned to AwaitingApproveQueue (not yet approved)"
)
tasks_forwarded_counter = meter.create_counter(
    "awaiting_approve_processor.tasks_forwarded",
    description="Number of approved Tasks forwarded to TaskQueue"
)
captasks_forwarded_counter = meter.create_counter(
    "awaiting_approve_processor.captasks_forwarded",
    description="Number of approved CapTasks forwarded to CapabilityTaskQueues"
)
idle_status_gauge = meter.create_up_down_counter(
    "awaiting_approve_processor.idle_status",
    description="Indicates whether processor is idle (1) or normal (0)"
)


class AwaitingApproveProcessor:
    """
    A processor that continuously consumes items (Task or CapTask) from an
    AwaitingApproveQueue, checks their approval status, and routes them to the
    appropriate downstream queue if approved. If not approved, the item is
    placed back into the AwaitingApproveQueue for later approval.

    It implements a backoff sleep when the source queue is empty, doubling wait time
    up to a maximum of 300 seconds. The status becomes "idle" when the wait time
    reaches or exceeds 16 seconds. External components can call notify() to interrupt
    the sleep and trigger an immediate consumption attempt if the processor is idle.

    Example usage:
        from scl.queue.awaitingApproveQueue import AwaitingApproveQueue
        from scl.queue.taskQueue import TaskQueue
        from scl.queue.capabilityTaskQueues import CapabilityTaskQueues
        from scl.processor.awaitingApproveProcessor import AwaitingApproveProcessor

        # Setup queues
        approve_queue = AwaitingApproveQueue()
        task_queue = TaskQueue()
        cap_queue = CapabilityTaskQueues()

        # Create and start processor
        processor = AwaitingApproveProcessor(
            source_queue=approve_queue,
            task_queue=task_queue,
            captask_queue=cap_queue
        )
        processor.start()

        # External component can notify when new items might be available
        processor.notify()

        # Check status
        print(processor.status)  # 'normal' or 'idle'

        # Graceful shutdown
        processor.stop()
    """

    def __init__(
        self,
        source_queue: AwaitingApproveQueue,
        task_queue: TaskQueue,
        captask_queue: CapabilityTaskQueues,
        name: Optional[str] = None
    ):
        """
        Initialize the processor.

        Args:
            source_queue: The AwaitingApproveQueue to consume from.
            task_queue: The TaskQueue to forward approved Tasks to.
            captask_queue: The CapabilityTaskQueues to forward approved CapTasks to.
            name: Optional name for this processor instance (for logging/metrics).
        """
        self.source_queue = source_queue
        self.task_queue = task_queue
        self.captask_queue = captask_queue
        self.name = name or f"processor-{id(self)}"

        # Wait time management
        self._wait_time = 1.0           # seconds
        self._max_wait = 300.0          # 5 minutes
        self._idle_threshold = 16.0     # status becomes idle after this many seconds

        # Control flags
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._wakeup_event = threading.Event()  # used to interrupt sleep

        logger.info(f"AwaitingApproveProcessor '{self.name}' initialized")

    @property
    def status(self) -> str:
        """Return 'idle' if wait time >= 16s, else 'normal'."""
        return "idle" if self._wait_time >= self._idle_threshold else "normal"

    @tracer.start_as_current_span("AwaitingApproveProcessor.start")
    def start(self) -> None:
        """
        Start the background consumption loop.
        """
        if self._running:
            logger.info(f"Processor '{self.name}' is already running.")
            return

        self._running = True
        self._thread = threading.Thread(target=self._consume_loop, daemon=True)
        self._thread.start()
        logger.info(f"Processor '{self.name}' started (initial wait: {self._wait_time}s)")

    def stop(self) -> None:
        """
        Stop the consumption loop gracefully.
        """
        if not self._running:
            return

        self._running = False
        self._wakeup_event.set()  # interrupt any ongoing sleep
        if self._thread:
            self._thread.join(timeout=2.0)
        logger.info(f"Processor '{self.name}' stopped")

    @tracer.start_as_current_span("AwaitingApproveProcessor._consume_loop")
    def _consume_loop(self) -> None:
        """Main loop: fetch items, check approval, and route accordingly."""
        current_span = trace.get_current_span()
        current_span.set_attribute("processor.name", self.name)

        while self._running:
            # Try to get an item from the source queue
            item = self._get_item()

            if item is None:
                # Queue empty: double wait time, capped at max
                self._wait_time = min(self._wait_time * 2, self._max_wait)
                logger.debug(
                    f"Processor '{self.name}': queue empty, wait time increased to {self._wait_time}s"
                )
                self._update_idle_metric()
                # Sleep with interrupt capability
                self._wakeup_event.wait(timeout=self._wait_time)
                self._wakeup_event.clear()
            else:
                # Process the item: check approval and route
                self._process_item(item)
                # Reset wait time to minimum after successful consumption
                self._wait_time = 1.0
                self._update_idle_metric()
                # Immediately proceed to next iteration

        logger.debug(f"Consume loop for '{self.name}' exited")

    @tracer.start_as_current_span("AwaitingApproveProcessor._get_item")
    def _get_item(self) -> Optional[Union[Task, CapTask]]:
        """Fetch one item from the source AwaitingApproveQueue."""
        current_span = trace.get_current_span()
        current_span.set_attribute("processor.name", self.name)
        try:
            item = self.source_queue.get()
            if item:
                item_hash = getattr(item, 'hash', 'unknown')
                item_type = type(item).__name__
                current_span.set_attribute("item.available", True)
                current_span.set_attribute("item.type", item_type)
                current_span.set_attribute("item.hash", item_hash)
                items_consumed_counter.add(1, {"processor.name": self.name, "item_type": item_type})
                logger.debug(f"Processor '{self.name}' consumed {item_type} {item_hash}")
            else:
                current_span.set_attribute("item.available", False)
            return item
        except Exception as e:
            logger.error(f"Error consuming item from source queue: {e}")
            current_span.record_exception(e)
            return None

    @tracer.start_as_current_span("AwaitingApproveProcessor._process_item")
    def _process_item(self, item: Union[Task, CapTask]) -> None:
        """
        Process a consumed item. If approval is True, forward to appropriate
        queue; otherwise, return it to the source AwaitingApproveQueue.
        """
        current_span = trace.get_current_span()
        item_hash = getattr(item, 'hash', 'unknown')
        item_type = type(item).__name__
        current_span.set_attribute("processor.name", self.name)
        current_span.set_attribute("item.type", item_type)
        current_span.set_attribute("item.hash", item_hash)
        current_span.set_attribute("item.approval", item.approval)

        try:
            if not item.approval:
                # Not yet approved: return to source queue
                self.source_queue.add(item)
                items_requeued_counter.add(1, {"processor.name": self.name, "item_type": item_type})
                logger.debug(
                    f"Processor '{self.name}' returned unapproved {item_type} {item_hash} to source queue"
                )
                current_span.set_attribute("item.routed_to", "source_queue")
            else:
                # Approved: route to appropriate downstream queue
                if isinstance(item, Task):
                    self.task_queue.add(item)
                    tasks_forwarded_counter.add(1, {"processor.name": self.name})
                    logger.info(
                        f"Processor '{self.name}' forwarded approved Task {item.hash} to TaskQueue"
                    )
                    current_span.set_attribute("item.routed_to", "task_queue")
                elif isinstance(item, CapTask):
                    self.captask_queue.add(item)
                    captasks_forwarded_counter.add(1, {"processor.name": self.name})
                    logger.info(
                        f"Processor '{self.name}' forwarded approved CapTask {item.hash} to CapabilityTaskQueues"
                    )
                    current_span.set_attribute("item.routed_to", "captask_queue")
                else:
                    # Unknown type, should not happen
                    logger.error(f"Unknown item type: {item_type}, cannot route")
                    current_span.set_status(trace.Status(trace.StatusCode.ERROR, "Unknown item type"))
        except Exception as e:
            logger.error(f"Failed to process {item_type} {item_hash}: {e}", exc_info=True)
            current_span.record_exception(e)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, "Item processing failed"))
            # Attempt to put back into source queue to avoid losing the item
            try:
                self.source_queue.add(item)
                logger.warning(f"{item_type} {item_hash} put back into source queue after processing error")
            except Exception as push_error:
                logger.critical(f"Failed to requeue {item_type} {item_hash} after error: {push_error}")

    def notify(self) -> None:
        """
        External notification that new items may be available.
        - If current status is 'idle', wake up immediately to fetch new items.
        - If status is 'normal', do nothing (already actively processing).
        """
        current_status = self.status
        logger.debug(f"Notify called on processor '{self.name}'. Current status: {current_status}")
        if current_status == "idle":
            logger.info(f"Processor '{self.name}' is idle; waking up to consume new items")
            self._wakeup_event.set()
        else:
            logger.debug(f"Processor '{self.name}' is normal; ignoring notification")

    def _update_idle_metric(self) -> None:
        """Update the idle gauge metric based on current status."""
        value = 1 if self.status == "idle" else 0
        idle_status_gauge.add(value, {"processor.name": self.name})


# Missing / Future Features (kept as comments for open-source tracking):
# - Configurable wait parameters (initial wait, max wait, idle threshold).
# - Metrics for routing latency and downstream queue sizes.
# - Dead-letter queue for items that repeatedly fail processing.
# - Support for batch processing.
# - Integration with external health checks.