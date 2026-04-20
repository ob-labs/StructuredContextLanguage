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
- If it(either task or CapTask) got approal invoke find the file in waitingapproval folder and move it to the file_watch directory.

Project Constraints:
- Please relay on otel for tracing, metric, logs.
- Please design log for info and debug level.
- Please have example usage as comments after class define, before init function.
- Just impl necessary functions.
"""

import logging
import os
import shutil
import threading
import time
from typing import Optional, Union

from opentelemetry import trace
from scl.otel.otel import meter, tracer

# Project imports
from scl.queue.awaitingApproveQueue import AwaitingApproveQueue
from scl.meta.task import Task
from scl.meta.captask import CapTask

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
files_moved_counter = meter.create_counter(
    "awaiting_approve_processor.files_moved",
    description="Number of files moved from waitingapproval to file_watch directory"
)
file_move_errors_counter = meter.create_counter(
    "awaiting_approve_processor.file_move_errors",
    description="Number of errors encountered while moving files"
)
idle_status_gauge = meter.create_up_down_counter(
    "awaiting_approve_processor.idle_status",
    description="Indicates whether processor is idle (1) or normal (0)"
)


class AwaitingApproveProcessor:
    """
    A processor that continuously consumes items (Task or CapTask) from an
    AwaitingApproveQueue, checks their approval status, and if approved,
    moves the corresponding file from the waitingapproval folder to the
    file_watch directory. Unapproved items are placed back into the queue.

    It implements a backoff sleep when the source queue is empty, doubling wait time
    up to a maximum of 300 seconds. The status becomes "idle" when the wait time
    reaches or exceeds 16 seconds. External components can call notify() to interrupt
    the sleep and trigger an immediate consumption attempt if the processor is idle.

    Example usage:
        from scl.queue.awaitingApproveQueue import AwaitingApproveQueue
        from scl.processor.awaitingApproveProcessor import AwaitingApproveProcessor

        # Setup queue and folders
        approve_queue = AwaitingApproveQueue()
        waiting_approval_dir = "/path/to/waitingapproval"
        file_watch_dir = "/path/to/file_watch"

        # Create and start processor
        processor = AwaitingApproveProcessor(
            source_queue=approve_queue,
            waiting_approval_dir=waiting_approval_dir,
            file_watch_dir=file_watch_dir
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
        waiting_approval_dir: str,
        file_watch_dir: str,
        name: Optional[str] = None
    ):
        """
        Initialize the processor.

        Args:
            source_queue: The AwaitingApproveQueue to consume from.
            waiting_approval_dir: Directory where files of unapproved items are stored.
            file_watch_dir: Destination directory for approved items (watched by FileWatcher).
            name: Optional name for this processor instance (for logging/metrics).
        """
        self.source_queue = source_queue
        self.waiting_approval_dir = waiting_approval_dir
        self.file_watch_dir = file_watch_dir
        self.name = name or f"processor-{id(self)}"

        # Ensure directories exist
        os.makedirs(self.waiting_approval_dir, exist_ok=True)
        os.makedirs(self.file_watch_dir, exist_ok=True)

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
        """Main loop: fetch items, check approval, and move files if approved."""
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
                # Process the item: check approval and move file
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
        Process a consumed item. If approval is True, move the corresponding file
        from waiting_approval_dir to file_watch_dir; otherwise, return it to the source queue.
        """
        current_span = trace.get_current_span()
        item_hash = getattr(item, 'hash', None)
        if item_hash is None:
            logger.error("Item missing 'hash' attribute, cannot process")
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, "Missing hash"))
            return

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
                # Approved: move file from waiting_approval_dir to file_watch_dir
                self._move_approved_file(item_hash, item_type, current_span)
                current_span.set_attribute("item.routed_to", "file_watch_dir")

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

    def _move_approved_file(self, item_hash: str, item_type: str, span: trace.Span) -> None:
        """
        Locate the file named '{item_hash}.json' in waiting_approval_dir and move it to file_watch_dir.
        """
        filename = f"{item_hash}.json"
        src_path = os.path.join(self.waiting_approval_dir, filename)
        dst_path = os.path.join(self.file_watch_dir, filename)

        span.set_attribute("file.src_path", src_path)
        span.set_attribute("file.dst_path", dst_path)

        if not os.path.exists(src_path):
            error_msg = f"Expected file {src_path} not found for approved {item_type} {item_hash}"
            logger.error(error_msg)
            span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
            file_move_errors_counter.add(1, {"processor.name": self.name, "error": "file_not_found"})
            # We still consider the item processed, but the file is missing.
            # Could be a race condition or manual cleanup; log and continue.
            return

        try:
            shutil.move(src_path, dst_path)
            files_moved_counter.add(1, {"processor.name": self.name, "item_type": item_type})
            logger.info(
                f"Processor '{self.name}' moved approved {item_type} file {filename} "
                f"from {self.waiting_approval_dir} to {self.file_watch_dir}"
            )
            span.set_attribute("file.moved", True)
        except Exception as e:
            logger.error(f"Failed to move file {src_path} to {dst_path}: {e}")
            span.record_exception(e)
            file_move_errors_counter.add(1, {"processor.name": self.name, "error": "move_failed"})
            raise

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
# - Support for other file extensions (e.g., .yaml) if needed.
# - Configurable wait parameters (initial wait, max wait, idle threshold).
# - Dead-letter handling for files that repeatedly fail to move.
# - Batch processing support.
# - Integration with external health checks.