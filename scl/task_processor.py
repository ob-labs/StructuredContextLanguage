"""
This module contains the TaskProcessor class, which is responsible for processing todo items.
- It will consume the TaskQueue, and register itself to the TaskQueue
- It use a while true to consume items from the queue
- If the item is empty then double the wait time for the queue and the max sleep time is 300s
- It allows status check, if the wait time equal or over 16s then the status been set to "idle", otherwise set to "normal"
- It has an event method for notification, allows other components invoke.
-   When the status is "idle" and the method been invoked, start a new round of get from queue immediately.
-   When the status is "normal" and the method been invoked, do nothing.
"""

import logging
import time
from threading import Thread, Event
from queue import Empty

from scl.meta.taskQueue import TaskQueue
from scl.otel.otel import tracer, meter
from opentelemetry import trace


class TaskProcessor:
    """Processes todo items from a TaskQueue with exponential backoff on empty queue."""

    def __init__(self, input_queue: TaskQueue):
        self.input_queue = input_queue
        self.logger = logging.getLogger(__name__)
        self.thread = None
        self._current_wait_time = 1.0        # Track current wait time for status checks
        self._immediate_fetch = Event()      # Event for notification-triggered immediate fetch
        input_queue.register_processor(self)

        # Metrics
        self.processed_counter = meter.create_counter(
            "task_items_processed",
            description="Number of task items processed"
        )
        # Gauge to report current status: 0 = normal, 1 = idle
        self.status_gauge = meter.create_gauge(
            "processor_status",
            description="Current status of the processor (0=normal, 1=idle)"
        )

    def start(self):
        """Start processing thread."""
        self.thread = Thread(target=self._process_loop, daemon=True)
        self.thread.start()

    def get_status(self) -> str:
        """
        Return the current status of the processor.
        - "idle" if current wait time exceeds 16 seconds.
        - "normal" otherwise.
        """
        if self._current_wait_time >= 16.0:
            return "idle"
        return "normal"

    def _update_status_metric(self):
        """Update the status gauge based on current wait time."""
        if self._current_wait_time >= 16.0:
            self.status_gauge.set(1)  # idle
        else:
            self.status_gauge.set(0)  # normal

    def notify(self):
        """
        Event method for external components to trigger immediate queue fetch.
        If status is "idle", initiate a new round of queue retrieval without waiting.
        If status is "normal", this call does nothing.
        """
        if self.get_status() == "idle":
            self.logger.debug("Notified while idle: triggering immediate queue fetch.")
            self._immediate_fetch.set()
            # The wait time will be reset in the next loop iteration, so update metric now
            self._update_status_metric()
        else:
            self.logger.debug("Notified but status is normal, ignoring.")

    def _process_loop(self):
        """Main loop that consumes items from queue with exponential backoff."""
        wait_time = 1.0          # initial wait time 1 second
        max_wait_time = 300.0    # maximum wait time 300 seconds

        while True:
            # Update tracked wait time for status reporting
            self._current_wait_time = wait_time
            self._update_status_metric()

            # If a notification requested immediate fetch, reset wait_time to minimal value
            if self._immediate_fetch.is_set():
                self.logger.debug("Immediate fetch triggered, resetting wait_time to 1.0")
                wait_time = 1.0
                self._current_wait_time = wait_time
                self._update_status_metric()
                self._immediate_fetch.clear()

            try:
                with tracer.start_as_current_span("todo_queue_operation") as parent_span:
                    item = self.input_queue.get(timeout=wait_time)
                    # Successfully retrieved an item, reset wait time
                    if item is not None:
                        wait_time = 1.0
                        self._current_wait_time = wait_time
                        self._update_status_metric()
                        self._process_item(item)
                    else:
                        wait_time = min(wait_time * 2, max_wait_time)
            except Empty:
                # Queue empty, double the wait time up to the maximum
                self.logger.debug(f"Queue empty, backing off for {wait_time}s")
                wait_time = min(wait_time * 2, max_wait_time)
            except Exception as e:
                # Log unexpected errors and continue after a short sleep
                self.logger.exception(f"Unexpected error in processing loop: {e}")
                time.sleep(1)

    @tracer.start_as_current_span("process todo item from queue")
    def _process_item(self, item):
        """Process a single todo item."""
        current_span = trace.get_current_span()
        # The item may be a dictionary with a "source" key; set attribute accordingly
        source = item.get("source", "unknown") if isinstance(item, dict) else str(item)
        current_span.set_attribute("todo.item.source", source)
        self.logger.info(f"Processing todo item: {item}")
        # Simulate processing logic
        time.sleep(0.1)
        self.processed_counter.add(1)
        # Placeholder: future work may generate new todos and put back into queue