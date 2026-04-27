"""
awaitCapTasksProcessor module

Design Goals & Features:
- Inherits from BaseQueueProcessor for common loop/backoff/status/notify.
- It will consume an AwaitingCapTasksQueue as queue instance.
- It use a while true to consume Task instance from the queue.
- If the item is not empty then check if all of the CapTasks are in a completed state.
- If all CapTasks are completed then find the file in waitingCapTask folder and move it to the file_watch directory.
- If not all CapTasks are completed then put the item back into the queue for retry.

Project Constraints:
- Please relay on otel for tracing, metric, logs.
- Please design log for info and debug level.
- Please have example usage as comments after class define, before init function.   (This constraint is outdated; example is now at module level)
- Just impl necessary functions.

"""

import logging
import os
import shutil
from typing import Optional

from opentelemetry import trace
from scl.otel.otel import meter, tracer

# Project imports
from scl.queue.awaitingCapTasksQueue import AwaitingCapTasksQueue
from scl.meta.task import Task
from scl.processor.base_queue_processor import BaseQueueProcessor

logger = logging.getLogger(__name__)


class AwaitCapTasksProcessor(BaseQueueProcessor):
    """
    A processor that continuously consumes Task instances from an AwaitingCapTasksQueue,
    checks if all associated CapTasks are in a completed state (Processed or Error),
    and if so, moves the corresponding file from the waitingCapTask folder to the
    file_watch directory. If any CapTask is still in 'created' state, the Task is put
    back into the AwaitingCapTasksQueue for a later retry.

    It implements a backoff sleep when the source queue is empty, doubling wait time
    up to a maximum of 300 seconds. The status becomes "idle" when the wait time
    reaches or exceeds 16 seconds. External components can call notify() to interrupt
    the sleep and trigger an immediate consumption attempt if the processor is idle.
    """

    def __init__(
        self,
        source_queue: AwaitingCapTasksQueue,
        waiting_captask_dir: str,
        file_watch_dir: str,
        name: Optional[str] = None
    ):
        """
        Initialize the processor.

        Args:
            source_queue: The AwaitingCapTasksQueue to consume from.
            waiting_captask_dir: Directory where Task files with pending CapTasks are stored.
            file_watch_dir: Destination directory for completed Task files (watched by FileWatcher).
            name: Optional name for this processor instance (for logging/metrics).
        """
        super().__init__(name=name or "await-captask-processor")
        self.source_queue = source_queue
        self.waiting_captask_dir = waiting_captask_dir
        self.file_watch_dir = file_watch_dir

        # Ensure directories exist
        os.makedirs(self.waiting_captask_dir, exist_ok=True)
        os.makedirs(self.file_watch_dir, exist_ok=True)

        # Additional metrics beyond the base class
        self.files_moved_counter = meter.create_counter(
            f"{self.name}.files_moved",
            description="Number of Task files moved from waitingCapTask to file_watch directory"
        )
        self.file_move_errors_counter = meter.create_counter(
            f"{self.name}.file_move_errors",
            description="Number of errors encountered while moving Task files"
        )
        self.tasks_requeued_counter = meter.create_counter(
            f"{self.name}.tasks_requeued",
            description="Number of Task instances put back into the queue for retry"
        )

        logger.info("%s initialized with queue %r", self.name, source_queue)

    # ------------------------------------------------------------------ Abstract method overrides
    @tracer.start_as_current_span("AwaitCapTasksProcessor._get_item")
    def _get_item(self) -> Optional[Task]:
        """
        Fetch one Task from the AwaitingCapTasksQueue.
        Must return None if no item is available (empty queue).
        """
        current_span = trace.get_current_span()
        current_span.set_attribute("processor.name", self.name)
        try:
            task = self.source_queue.pop()
            if task:
                current_span.set_attribute("task.available", True)
                current_span.set_attribute("task.hash", task.hash)
                logger.debug("%s: consumed Task %s from source queue", self.name, task.hash)
            else:
                current_span.set_attribute("task.available", False)
            return task
        except Exception as e:
            logger.error("%s: error consuming task from source queue: %s", self.name, e)
            current_span.record_exception(e)
            return None

    @tracer.start_as_current_span("AwaitCapTasksProcessor._process_item")
    def _process_item(self, item: Task) -> None:
        """
        Process a Task: if all CapTasks are completed move its file, else requeue.
        The base class loop increments the generic items_consumed counter.
        """
        current_span = trace.get_current_span()
        current_span.set_attribute("processor.name", self.name)
        current_span.set_attribute("task.hash", item.hash)

        try:
            if self._all_captasks_completed(item):
                # All CapTasks done: move file to file_watch_dir
                self._move_completed_file(item.hash, current_span)
                current_span.set_attribute("task.completed", True)
            else:
                # Not all CapTasks completed: requeue for later retry
                self.source_queue.push(item)
                self.tasks_requeued_counter.add(1, {"processor.name": self.name})
                logger.debug(
                    "%s: requeued Task %s (CapTasks not all completed)", self.name, item.hash
                )
                current_span.set_attribute("task.requeued", True)
        except Exception as e:
            logger.error("%s: failed to process Task %s: %s", self.name, item.hash, e, exc_info=True)
            current_span.record_exception(e)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, "Task processing failed"))
            # Attempt to put back into source queue to avoid losing the task
            try:
                self.source_queue.push(item)
                logger.warning("%s: Task %s put back into source queue after processing error", self.name, item.hash)
            except Exception as push_error:
                logger.critical("%s: failed to requeue Task %s after error: %s", self.name, item.hash, push_error)

    # ------------------------------------------------------------------ Helper methods
    def _all_captasks_completed(self, task: Task) -> bool:
        """
        Check whether all CapTasks of the given Task are in a completed state.
        Completed states are 'Processed' or 'Error'.
        """
        for cap in task.cap_tasks:
            if cap.status not in ("Processed", "Error"):
                return False
        return True

    def _move_completed_file(self, task_hash: str, span: trace.Span) -> None:
        """
        Locate the file named '{task_hash}.json' in waiting_captask_dir and move it
        to file_watch_dir.
        """
        filename = f"{task_hash}.json"
        src_path = os.path.join(self.waiting_captask_dir, filename)
        dst_path = os.path.join(self.file_watch_dir, filename)

        span.set_attribute("file.src_path", src_path)
        span.set_attribute("file.dst_path", dst_path)

        if not os.path.exists(src_path):
            error_msg = f"Expected file {src_path} not found for completed Task {task_hash}"
            logger.error("%s: %s", self.name, error_msg)
            span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
            self.file_move_errors_counter.add(1, {"processor.name": self.name, "error": "file_not_found"})
            # Task considered processed but file is missing
            return

        try:
            shutil.move(src_path, dst_path)
            self.files_moved_counter.add(1, {"processor.name": self.name})
            logger.info(
                "%s: moved completed Task file %s from %s to %s",
                self.name, filename, self.waiting_captask_dir, self.file_watch_dir
            )
            span.set_attribute("file.moved", True)
        except Exception as e:
            logger.error("%s: failed to move file %s to %s: %s", self.name, src_path, dst_path, e)
            span.record_exception(e)
            self.file_move_errors_counter.add(1, {"processor.name": self.name, "error": "move_failed"})
            raise


# Missing / Future Features (kept as comments for open‑source tracking):
# - Support for other file extensions (e.g., .yaml) if needed.
# - Configurable wait parameters (initial wait, max wait, idle threshold) – currently fixed in base class.
# - Dead‑letter handling for files that repeatedly fail to move.
# - Batch processing support.
# - Integration with external health checks.

"""
Example usage:
    from scl.queue.awaitingCapTasksQueue import AwaitingCapTasksQueue
    from scl.processor.awaitCapTasksProcessor import AwaitCapTasksProcessor

    # Setup queue and folders
    source_queue = AwaitingCapTasksQueue()
    waiting_captask_dir = "/path/to/waitingCapTask"
    file_watch_dir = "/path/to/file_watch"

    # Create and start processor
    processor = AwaitCapTasksProcessor(
        source_queue=source_queue,
        waiting_captask_dir=waiting_captask_dir,
        file_watch_dir=file_watch_dir
    )
    processor.start()

    # External component can notify when new tasks might be available
    processor.notify()

    # Check status
    print(processor.status)  # 'normal' or 'idle'

    # Graceful shutdown
    processor.stop()
"""