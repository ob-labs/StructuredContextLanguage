"""
File path:
scl/processor/task_processor.py

Features and design goals:
- Inherits from BaseQueueProcessor for common loop/backoff/status/notify.
- Registers itself with the TaskQueue to receive wake‑up notifications.
- Uses a while‑true loop (provided by the base class) to consume Task instances non‑blocking.
- Processes each Task with OpenTelemetry tracing, structured logging (info/debug), and error metrics.
- On queue notification, adds a trace span before delegating to the base class notification logic.
- Relies on OpenTelemetry for tracing, metrics, and logging (via the standard logging module).
- Logs at info and debug levels for appropriate observability.

Missing features (none identified):
- The design goals are fully implemented; no missing features have been identified.
"""

from opentelemetry import trace

from scl.meta.task import Task  # Task class definition
from scl.otel.otel import meter, tracer
from scl.processor.base_queue_processor import BaseQueueProcessor
from scl.queue.task_queue import TaskQueue


class TaskProcessor(BaseQueueProcessor):
    """
    Processes Task items from a TaskQueue with exponential backoff on empty queue.

    Inherits the infinite loop, exponential backoff, status logic, and notification
    mechanism from BaseQueueProcessor. Overrides _get_item and _process_item.
    """

    def __init__(self, input_queue: TaskQueue, name: str = "task_processor"):
        super().__init__(name=name)
        self.input_queue = input_queue
        # Register with the TaskQueue so it can notify us when new tasks arrive
        input_queue.register_processor(self)

        # Metrics specific to task processing
        self.processing_error_counter = meter.create_counter(
            f"{self.name}.processing_errors",
            description="Number of errors while processing individual tasks",
        )

        self.logger.info("TaskProcessor initialized and registered with queue")

    # ------------------------------------------------------------------ Abstract method implementations
    def _get_item(self) -> Task | None:
        """
        Fetch one Task from the input queue without blocking.
        Must return None if the queue is empty.
        """
        try:
            # Non‑blocking fetch so the base class can manage backoff/sleep
            return self.input_queue.get(block=False)
        except Exception:  # queue.Empty or equivalent
            return None

    @tracer.start_as_current_span("TaskProcessor._process_item")
    def _process_item(self, item: Task) -> None:
        """
        Process a single Task. Business logic lives here.
        The base class ensures this is called inside the main loop and
        handles exception logging / metric updates for completed items.
        """
        current_span = trace.get_current_span()
        task_id = getattr(item, "id", "unknown")
        task_type = getattr(item, "type", "unknown")

        current_span.set_attribute("task.id", str(task_id))
        current_span.set_attribute("task.type", task_type)

        self.logger.info("Processing Task: id=%s, type=%s", task_id, task_type)

        try:
            # ---------- Actual task processing ----------
            # Replace this placeholder with your business logic.
            import time

            time.sleep(0.1)  # simulate work
            # -------------------------------------------
            self.logger.debug("Task %s processed successfully", task_id)
        except Exception as exc:
            self.logger.error("Error processing task %s: %s", task_id, exc, exc_info=True)
            current_span.record_exception(exc)
            self.processing_error_counter.add(1, {"processor.name": self.name})
            # Re‑raise so the base class can still count the item as consumed
            # and log the exception generically if needed.
            raise

    # ------------------------------------------------------------------ Notification (override to add tracing)
    def notify(self) -> None:
        """
        Wake‑up signal from the TaskQueue.
        Adds an OpenTelemetry span for observability before delegating to the
        base class notification logic.
        """
        with tracer.start_as_current_span("TaskProcessor.notify") as span:
            status_before = self.status
            span.set_attribute("processor.status_before", status_before)
            super().notify()  # triggers wakeup if idle
            span.set_attribute("processor.status_after", self.status)


# ------------------------------------------------------------------ Example usage
"""
Example usage:
    from scl.processor.task_processor import TaskProcessor
    from scl.queue.task_queue import TaskQueue

    # Create a queue and a processor
    queue = TaskQueue()
    processor = TaskProcessor(input_queue=queue, name="task_worker")

    # Start the processing loop (BaseQueueProcessor provides start/stop)
    processor.start()

    # Push some tasks into the queue from another thread or process
    # e.g. queue.put(some_task)

    # When done, stop the processor gracefully
    processor.stop()
    processor.join()
"""
