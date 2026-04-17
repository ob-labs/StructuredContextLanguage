"""
This module contains the TaskProcessor class, which is responsible for processing Task instance.
- It will consume the TaskQueue, and register itself to the TaskQueue.
- It use a while true to consume Task instance from the queue.
- If the item is empty then double the wait time for the queue and the max sleep time is 300s.
- It allows status check, if the wait time equal or over 16s then the status been set to "idle", otherwise set to "normal".
- It has an event method for notification, allows other components invoke.
-   When the status is "idle" and the method been invoked, start a new round of get from queue immediately.
-   When the status is "normal" and the method been invoked, do nothing.
"""

import logging
import time
from threading import Thread, Event
from queue import Empty
from typing import Optional

from opentelemetry import trace

from scl.meta.taskQueue import TaskQueue
from scl.meta.task import Task  # Task class definition
from scl.otel.otel import tracer, meter


class TaskProcessor:
    """
    Processes Task items from a TaskQueue with exponential backoff on empty queue.
    """

    def __init__(self, input_queue: TaskQueue):
        self.input_queue = input_queue
        self.logger = logging.getLogger(__name__)
        self._running = False
        self.thread: Optional[Thread] = None
        self._current_wait_time = 1.0          # current wait time in seconds
        self._immediate_fetch = Event()         # triggered by notify()
        input_queue.register_processor(self)

        # Metrics
        self.processed_counter = meter.create_counter(
            "task_items_processed",
            description="Number of Task items processed"
        )
        self.processing_error_counter = meter.create_counter(
            "task_processing_errors",
            description="Number of errors during task processing"
        )
        # Gauge for current status: 0 = normal, 1 = idle
        self.status_gauge = meter.create_gauge(
            "processor_status",
            description="Current status of the processor (0=normal, 1=idle)"
        )

        self.logger.info("TaskProcessor initialized and registered with queue")

    def start(self):
        """Start the background processing thread."""
        if self._running:
            self.logger.warning("TaskProcessor already running")
            return
        self._running = True
        self.thread = Thread(target=self._process_loop, daemon=True, name="TaskProcessorThread")
        self.thread.start()
        self.logger.info("TaskProcessor started")

    def stop(self):
        """Signal the processor to stop (graceful shutdown)."""
        self._running = False
        self._immediate_fetch.set()  # wake up thread if sleeping
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5.0)
        self.logger.info("TaskProcessor stopped")

    def get_status(self) -> str:
        """
        Return the current status based on the current wait time.
        - "idle" if wait time >= 16 seconds.
        - "normal" otherwise.
        """
        return "idle" if self._current_wait_time >= 16.0 else "normal"

    def _update_status_metric(self):
        """Set the gauge value according to the current wait time."""
        self.status_gauge.set(1 if self._current_wait_time >= 16.0 else 0)

    def notify(self):
        """
        Notification from external components (e.g., TaskQueue).
        - If status is "idle": trigger an immediate fetch attempt.
        - If status is "normal": do nothing.
        """
        with tracer.start_as_current_span("task_processor_notify") as span:
            status = self.get_status()
            span.set_attribute("processor.status_before", status)
            if status == "idle":
                self.logger.debug("Notified while idle – triggering immediate queue fetch.")
                self._immediate_fetch.set()
            else:
                self.logger.debug("Notified but status is normal – ignoring.")

    def _process_loop(self):
        """Main infinite loop that fetches and processes tasks."""
        wait_time = 1.0          # initial wait time
        max_wait_time = 300.0    # max backoff as required

        while self._running:
            # Update current wait time for external status checks
            self._current_wait_time = wait_time
            self._update_status_metric()

            # If a notification occurred while idle, reset wait_time and clear the event
            if self._immediate_fetch.is_set():
                self.logger.debug("Immediate fetch event set – resetting wait time to 1.0s")
                wait_time = 1.0
                self._current_wait_time = wait_time
                self._update_status_metric()
                self._immediate_fetch.clear()

            try:
                # Block with timeout equal to current wait time
                task = self.input_queue.get(timeout=wait_time)
                # Successfully retrieved a task – reset backoff
                if not task:
                    self.logger.debug("No task retrieved from queue")
                    self.logger.debug(f"Queue empty, increasing wait time from {wait_time}s to {min(wait_time*2, max_wait_time)}s")
                    wait_time = min(wait_time * 2, max_wait_time)
                    continue
                wait_time = 1.0
                self._process_task(task)
            except Empty:
                # Queue empty – double the wait time up to maximum
                self.logger.debug(f"Queue empty, increasing wait time from {wait_time}s to {min(wait_time*2, max_wait_time)}s")
                wait_time = min(wait_time * 2, max_wait_time)
            except Exception as e:
                # Unexpected error – log and continue with a short sleep
                self.logger.exception(f"Unexpected error in processing loop: {e}")
                time.sleep(1)

    @tracer.start_as_current_span("process_task")
    def _process_task(self, task: Task):
        """
        Process a single Task instance.
        (Simulated processing; replace with actual business logic.)
        """
        current_span = trace.get_current_span()
        task_id = getattr(task, 'id', 'unknown')
        task_type = getattr(task, 'type', 'unknown')

        current_span.set_attribute("task.id", str(task_id))
        current_span.set_attribute("task.type", task_type)

        self.logger.info(f"Processing Task: id={task_id}, type={task_type}")

        try:
            # Placeholder: actual task handling goes here
            time.sleep(0.1)  # simulate work
            self.processed_counter.add(1)
            self.logger.debug(f"Task {task_id} processed successfully")
        except Exception as e:
            self.logger.error(f"Error processing task {task_id}: {e}", exc_info=True)
            current_span.record_exception(e)
            self.processing_error_counter.add(1)
            # In a real implementation you might requeue or dead‑letter