"""
awaitCapTasksProcessor module

Design Goals & Features:
- It will consume an AwaitingCapTasksQueue as queue instance.
- It use a while true to consume Task instance from the queue.
- If the item is empty then double the wait time for the queue and the max sleep time is 300s.
- It allows status check, if the wait time equal or over 16s then the status been set to "idle", otherwise set to "normal".
- It has an event method for notification, allows other components invoke.
-   When the status is "idle" and the method been invoked, start a new round of get from queue immediately.
-   When the status is "normal" and the method been invoked, do nothing.
- If the item is not empty then check if all of the CapTasks are in a completed state.
- If all CapTasks are completed then put the item into an TaskQueue instance.
- If not all CapTasks are completed then put the item back into the queue for retry.

Project Constraints:
- Please relay on otel for tracing, metric, logs.
- Please design log for info and debug level.
- Please have example usage as comments after class define, before init function.
- Just impl necessary functions.
"""

import logging
import threading
import time
from typing import Optional

from opentelemetry import trace
from scl.otel.otel import meter, tracer

# Project imports
from scl.queue.awaitingCapTasksQueue import AwaitingCapTasksQueue
from scl.meta.task import Task
from scl.queue.taskQueue import TaskQueue
from scl.meta.captask import CapTask

logger = logging.getLogger(__name__)

# Metrics
tasks_consumed_counter = meter.create_counter(
    "await_cap_processor.tasks_consumed",
    description="Number of Task instances consumed from AwaitingCapTasksQueue"
)
tasks_forwarded_counter = meter.create_counter(
    "await_cap_processor.tasks_forwarded",
    description="Number of Task instances forwarded to TaskQueue (all CapTasks completed)"
)
tasks_requeued_counter = meter.create_counter(
    "await_cap_processor.tasks_requeued",
    description="Number of Task instances put back into AwaitingCapTasksQueue for retry"
)
idle_status_gauge = meter.create_up_down_counter(
    "await_cap_processor.idle_status",
    description="Indicates whether processor is idle (1) or normal (0)"
)


class AwaitCapTasksProcessor:
    """
    A processor that continuously consumes Task instances from an AwaitingCapTasksQueue,
    checks if all associated CapTasks are in a completed state (Processed or Error),
    and forwards them to a TaskQueue for further processing if completed.
    If any CapTask is still in 'created' state, the Task is put back into the
    AwaitingCapTasksQueue for a later retry.

    It implements a backoff sleep when the source queue is empty, doubling wait time
    up to a maximum of 300 seconds. The status becomes "idle" when the wait time
    reaches or exceeds 16 seconds. External components can call notify() to interrupt
    the sleep and trigger an immediate consumption attempt if the processor is idle.

    Example usage:
        from scl.queue.awaitingCapTasksQueue import AwaitingCapTasksQueue
        from scl.queue.taskQueue import TaskQueue
        from scl.processor.awaitCapTasksProcessor import AwaitCapTasksProcessor

        # Setup queues
        source_queue = AwaitingCapTasksQueue()
        target_queue = TaskQueue()

        # Create and start processor
        processor = AwaitCapTasksProcessor(
            source_queue=source_queue,
            target_queue=target_queue
        )
        processor.start()

        # External component can notify when new tasks might be available
        processor.notify()

        # Check status
        print(processor.status)  # 'normal' or 'idle'

        # Graceful shutdown
        processor.stop()
    """

    def __init__(
        self,
        source_queue: AwaitingCapTasksQueue,
        target_queue: TaskQueue,
        name: Optional[str] = None
    ):
        """
        Initialize the processor.

        Args:
            source_queue: The AwaitingCapTasksQueue to consume from.
            target_queue: The TaskQueue to forward completed tasks to.
            name: Optional name for this processor instance (for logging/metrics).
        """
        self.source_queue = source_queue
        self.target_queue = target_queue
        self.name = name or f"processor-{id(self)}"

        # Wait time management
        self._wait_time = 1.0           # seconds
        self._max_wait = 300.0          # 5 minutes
        self._idle_threshold = 16.0     # status becomes idle after this many seconds

        # Control flags
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._wakeup_event = threading.Event()  # used to interrupt sleep

        logger.info(f"AwaitCapTasksProcessor '{self.name}' initialized")

    @property
    def status(self) -> str:
        """Return 'idle' if wait time >= 16s, else 'normal'."""
        return "idle" if self._wait_time >= self._idle_threshold else "normal"

    @tracer.start_as_current_span("AwaitCapTasksProcessor.start")
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

    @tracer.start_as_current_span("AwaitCapTasksProcessor._consume_loop")
    def _consume_loop(self) -> None:
        """Main loop: fetch tasks, check completion, forward or requeue."""
        current_span = trace.get_current_span()
        current_span.set_attribute("processor.name", self.name)

        while self._running:
            # Try to get a task from the source queue
            task = self._get_task()

            if task is None:
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
                # Process the task: check CapTasks completion and route accordingly
                self._process_task(task)
                # Reset wait time to minimum after successful consumption
                self._wait_time = 1.0
                self._update_idle_metric()
                # Immediately proceed to next iteration

        logger.debug(f"Consume loop for '{self.name}' exited")

    @tracer.start_as_current_span("AwaitCapTasksProcessor._get_task")
    def _get_task(self) -> Optional[Task]:
        """Fetch one Task from the source AwaitingCapTasksQueue."""
        current_span = trace.get_current_span()
        current_span.set_attribute("processor.name", self.name)
        try:
            task = self.source_queue.pop()
            if task:
                current_span.set_attribute("task.available", True)
                current_span.set_attribute("task.hash", task.hash)
                tasks_consumed_counter.add(1, {"processor.name": self.name})
                logger.debug(f"Processor '{self.name}' consumed Task {task.hash}")
            else:
                current_span.set_attribute("task.available", False)
            return task
        except Exception as e:
            logger.error(f"Error consuming task from source queue: {e}")
            current_span.record_exception(e)
            return None

    def _all_captasks_completed(self, task: Task) -> bool:
        """
        Check whether all CapTasks of the given Task are in a completed state.
        Completed states are 'Processed' or 'Error'.
        """
        for cap in task.cap_tasks:
            if cap.status not in ("Processed", "Error"):
                return False
        return True

    @tracer.start_as_current_span("AwaitCapTasksProcessor._process_task")
    def _process_task(self, task: Task) -> None:
        """
        Process a consumed Task: if all its CapTasks are completed, forward to
        target TaskQueue; otherwise, put it back into the source AwaitingCapTasksQueue.
        """
        current_span = trace.get_current_span()
        current_span.set_attribute("processor.name", self.name)
        current_span.set_attribute("task.hash", task.hash)

        try:
            if self._all_captasks_completed(task):
                # All CapTasks done: forward to target queue
                self.target_queue.add(task)
                tasks_forwarded_counter.add(1, {"processor.name": self.name})
                logger.info(
                    f"Processor '{self.name}' forwarded Task {task.hash} (all CapTasks completed)"
                )
                current_span.set_attribute("task.forwarded", True)
            else:
                # Not all CapTasks completed: requeue for later retry
                self.source_queue.push(task)
                tasks_requeued_counter.add(1, {"processor.name": self.name})
                logger.debug(
                    f"Processor '{self.name}' requeued Task {task.hash} (CapTasks not all completed)"
                )
                current_span.set_attribute("task.requeued", True)
        except Exception as e:
            logger.error(f"Failed to process Task {task.hash}: {e}", exc_info=True)
            current_span.record_exception(e)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, "Task processing failed"))
            # Attempt to put back into source queue to avoid losing the task
            try:
                self.source_queue.push(task)
                logger.warning(f"Task {task.hash} put back into source queue after processing error")
            except Exception as push_error:
                logger.critical(f"Failed to requeue Task {task.hash} after error: {push_error}")

    def notify(self) -> None:
        """
        External notification that new tasks may be available.
        - If current status is 'idle', wake up immediately to fetch new tasks.
        - If status is 'normal', do nothing (already actively processing).
        """
        current_status = self.status
        logger.debug(f"Notify called on processor '{self.name}'. Current status: {current_status}")
        if current_status == "idle":
            logger.info(f"Processor '{self.name}' is idle; waking up to consume new tasks")
            self._wakeup_event.set()
        else:
            logger.debug(f"Processor '{self.name}' is normal; ignoring notification")

    def _update_idle_metric(self) -> None:
        """Update the idle gauge metric based on current status."""
        value = 1 if self.status == "idle" else 0
        idle_status_gauge.add(value, {"processor.name": self.name})


# Missing / Future Features (kept as comments for open-source tracking):
# - Configurable wait parameters (initial wait, max wait, idle threshold).
# - Metrics for forwarding latency and queue sizes.
# - Automatic re-queue or dead-letter handling on forward failure.
# - Support for multiple target queues (e.g., routing based on Task properties).
# - Integration with external health checks.