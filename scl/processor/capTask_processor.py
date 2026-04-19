"""
This module contains the CapabilityProcessor class, which is responsible for processing CapTask instance.
- It as name mapping to Capability.
- It will consume the CapabilityTaskQueues as queue, and register itself to the CapabilityTaskQueues by name.
- It use a while true to consume CapTask instance from the queue.
- If the item is empty then double the wait time for the queue and the max sleep time is 300s.
- It allows status check, if the wait time equal or over 16s then the status been set to "idle", otherwise set to "normal".
- It has an event method for notification, allows other components invoke.
-   When the status is "idle" and the method been invoked, start a new round of get from queue immediately.
-   When the status is "normal" and the method been invoked, do nothing.

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

# OpenTelemetry imports
from opentelemetry import trace
from scl.otel.otel import tracer, meter

# Project imports (assumed to be available)
from scl.meta.captask import CapTask
from scl.queues.capTaskQueues import CapabilityTaskQueues

logger = logging.getLogger(__name__)

# Metrics
tasks_processed_counter = meter.create_counter(
    "capability_processor.tasks_processed",
    description="Number of CapTask instances processed by the processor"
)
idle_status_gauge = meter.create_up_down_counter(
    "capability_processor.idle_status",
    description="Indicates whether processor is idle (1) or normal (0)"
)


class CapabilityProcessor:
    """
    Example usage:
        from capability_task_queues import CapabilityTaskQueues
        from scl.meta.captask import CapTask

        # Setup queues and processor
        queues = CapabilityTaskQueues()
        processor = CapabilityProcessor(name="send_email", queue=queues)

        # Register the processor's notify method as the queue notifier
        queues.register_notifier("send_email", lambda name, task: processor.notify())

        # Start processing loop (usually in a background thread)
        processor.start_processing()

        # Add tasks to the queue
        task = CapTask(cap_name="send_email", args=["user@example.com", "Hello!"])
        queues.add(task)

        # Later, check status
        print(processor.status)  # 'normal' or 'idle'

        # Graceful shutdown (optional)
        processor.stop()
    """

    def __init__(self, name: str, queue: CapabilityTaskQueues):
        """
        Initialize the CapabilityProcessor.

        Args:
            name: The capability name this processor handles.
            queue: The CapabilityTaskQueues instance to consume from.
        """
        self.name = name
        self.queue = queue

        # Wait time management
        self._wait_time = 1.0          # seconds
        self._max_wait = 300.0         # max sleep time (5 minutes)
        self._idle_threshold = 16.0    # threshold for "idle" status

        # Control flags
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._wakeup_event = threading.Event()  # used for immediate wake-up on notify

        logger.info(f"CapabilityProcessor '{self.name}' initialized")

    @property
    def status(self) -> str:
        """Return 'idle' if wait time >= 16s, else 'normal'."""
        return "idle" if self._wait_time >= self._idle_threshold else "normal"

    @tracer.start_as_current_span("CapabilityProcessor.start_processing")
    def start_processing(self) -> None:
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
        (Not required by spec, but useful for clean shutdown)
        """
        if not self._running:
            return

        self._running = False
        self._wakeup_event.set()  # interrupt any sleep
        if self._thread:
            self._thread.join(timeout=2.0)
        logger.info(f"Processor '{self.name}' stopped")

    @tracer.start_as_current_span("CapabilityProcessor._consume_loop")
    def _consume_loop(self) -> None:
        """Main loop: fetch tasks, process, adjust wait time."""
        current_span = trace.get_current_span()
        current_span.set_attribute("processor.name", self.name)

        while self._running:
            # Try to get a task from the queue
            task = self._get_task()

            if task is None:
                # Queue empty: double wait time, capped at max
                self._wait_time = min(self._wait_time * 2, self._max_wait)
                logger.debug(f"Queue empty. Wait time increased to {self._wait_time}s")
                self._update_idle_metric()
                # Sleep with interrupt capability
                self._wakeup_event.wait(timeout=self._wait_time)
                self._wakeup_event.clear()
            else:
                # Task received: process it
                self._process_task(task)
                # Reset wait time to minimum after successful consumption
                self._wait_time = 1.0
                self._update_idle_metric()
                # Immediately proceed to next iteration

        logger.debug(f"Consume loop for '{self.name}' exited")

    @tracer.start_as_current_span("CapabilityProcessor._get_task")
    def _get_task(self) -> Optional[CapTask]:
        """Fetch one CapTask from the queue for our capability name."""
        current_span = trace.get_current_span()
        current_span.set_attribute("processor.name", self.name)
        try:
            task = self.queue.consume(self.name)
            if task:
                current_span.set_attribute("task.available", True)
                current_span.set_attribute("task.hash", task.hash)
            else:
                current_span.set_attribute("task.available", False)
            return task
        except Exception as e:
            logger.error(f"Error consuming task from queue: {e}")
            current_span.record_exception(e)
            return None

    @tracer.start_as_current_span("CapabilityProcessor._process_task")
    def _process_task(self, task: CapTask) -> None:
        """
        Process a single CapTask.
        In a real system, this would invoke the actual capability logic.
        """
        current_span = trace.get_current_span()
        current_span.set_attribute("processor.name", self.name)
        current_span.set_attribute("task.hash", task.hash)
        current_span.set_attribute("task.cap_name", task.cap_name)
        current_span.set_attribute("task.args_count", len(task.args))

        logger.debug(f"Processing CapTask {task.hash} for capability '{self.name}'")
        logger.info(f"Task {task.hash} processing started")

        try:
            # Fake capability execution
            # In a real implementation, you would look up the capability by name
            # and execute it with the provided arguments.
            # Example:
            #   capability = get_capability(task.cap_name)
            #   result = capability(*task.args)
            time.sleep(0.1)  # Simulate work

            tasks_processed_counter.add(1, {"capability.name": self.name})
            logger.info(f"Task {task.hash} processed successfully")
        except Exception as e:
            logger.error(f"Error processing task {task.hash}: {e}", exc_info=True)
            current_span.record_exception(e)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
            # In a real system, you might requeue the task or log to DLQ

    def notify(self) -> None:
        """
        External notification that new tasks may be available.
        - If current status is 'idle', wake up immediately to fetch new tasks.
        - If status is 'normal', do nothing (already actively processing).
        """
        current_status = self.status
        logger.debug(f"Notify called. Current status: {current_status}")
        if current_status == "idle":
            logger.info("Processor is idle; waking up to consume new tasks")
            self._wakeup_event.set()
        else:
            logger.debug("Processor is normal; ignoring notification")

    def _update_idle_metric(self) -> None:
        """Update the idle gauge metric based on current status."""
        value = 1 if self.status == "idle" else 0
        idle_status_gauge.add(value, {"processor.name": self.name})


# Missing / Future Features (kept as comments for open-source tracking):
# - Dynamic capability registration: The processor currently does not invoke
#   actual capability functions; it only simulates work. A real implementation
#   would require a registry mapping cap_name to callable.
# - Graceful shutdown integration with queue: When stopping, we might want to
#   finish processing the current task before exiting.
# - Error handling with retry/backoff for transient failures.
# - Metrics for wait time and queue depth.
# - Configuration of wait parameters via external config (env/file).
# - Support for multiple processor instances per capability (worker pool).