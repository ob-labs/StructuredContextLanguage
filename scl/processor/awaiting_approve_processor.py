"""
awaiting_approve_processor Module

Design Goals & Features:
- Inherits from BaseQueueProcessor for common loop/backoff/status/notify.
- It will consume an AwaitingApproveQueue as queue instance.
- It use a while true to consume Task or CapTask instance from the queue.
- If the item is not empty then check if it's approval status is True.
- If it not got approval, return it back to AwaitingApproveQueue.
- If it(either task or CapTask) got approval invoke find the file in waitingapproval folder and move it to the file_watch directory.

Project Constraints:
- Please relay on otel for tracing, metric, logs.
- Please design log for info and debug level.
- Please have example usage as comments after class define, before init function.
- Just impl necessary functions.
"""

import logging
import os
import shutil

from opentelemetry import trace

from scl.meta.captask import CapTask
from scl.meta.task import Task
from scl.otel.otel import meter, tracer
from scl.processor.base_queue_processor import BaseQueueProcessor
from scl.queue.awaiting_approve_queue import AwaitingApproveQueue

logger = logging.getLogger(__name__)


class AwaitingApproveProcessor(BaseQueueProcessor):
    """
    A processor that continuously consumes items (Task or CapTask) from an
    AwaitingApproveQueue, checks their approval status, and if approved,
    moves the corresponding file from the waitingapproval folder to the
    file_watch directory. Unapproved items are placed back into the queue.

    Inherits the exponential backoff loop, idle status notification, and
    common metrics from BaseQueueProcessor.
    """

    def __init__(
        self,
        source_queue: AwaitingApproveQueue,
        waiting_approval_dir: str,
        file_watch_dir: str,
        name: str | None = None,
    ):
        """
        Initialize the processor.

        Args:
            source_queue: The AwaitingApproveQueue to consume from.
            waiting_approval_dir: Directory where files of unapproved items are stored.
            file_watch_dir: Destination directory for approved items (watched by FileWatcher).
            name: Optional name for this processor instance (for logging/metrics).
        """
        super().__init__(name=name or f"processor-{id(self)}", logger_name=__name__)
        self.source_queue = source_queue
        self.waiting_approval_dir = waiting_approval_dir
        self.file_watch_dir = file_watch_dir

        # Ensure directories exist
        os.makedirs(self.waiting_approval_dir, exist_ok=True)
        os.makedirs(self.file_watch_dir, exist_ok=True)

        # Processor‑specific metrics
        self.items_fetched_by_type_counter = meter.create_counter(
            f"{self.name}.items_fetched_by_type",
            description="Number of items fetched from AwaitingApproveQueue, broken down by item type",
        )
        self.items_requeued_counter = meter.create_counter(
            f"{self.name}.items_requeued",
            description="Number of items returned to AwaitingApproveQueue (not yet approved)",
        )
        self.files_moved_counter = meter.create_counter(
            f"{self.name}.files_moved",
            description="Number of files moved from waitingapproval to file_watch directory",
        )
        self.file_move_errors_counter = meter.create_counter(
            f"{self.name}.file_move_errors",
            description="Number of errors encountered while moving files",
        )

        self.logger.info("AwaitingApproveProcessor '%s' initialized", self.name)

    # ------------------------------------------------------------------ Core logic overrides
    @tracer.start_as_current_span("AwaitingApproveProcessor._get_item")
    def _get_item(self) -> Task | CapTask | None:
        """
        Fetch one item from the source AwaitingApproveQueue.
        Returns None if the queue is empty or an error occurs.
        """
        span = trace.get_current_span()
        span.set_attribute("processor.name", self.name)
        try:
            item = self.source_queue.get()
            if item:
                item_hash = getattr(item, "hash", "unknown")
                item_type = type(item).__name__
                span.set_attribute("item.available", True)
                span.set_attribute("item.type", item_type)
                span.set_attribute("item.hash", item_hash)
                self.items_fetched_by_type_counter.add(
                    1, {"processor.name": self.name, "item_type": item_type}
                )
                self.logger.debug("'%s' consumed %s %s", self.name, item_type, item_hash)
            else:
                span.set_attribute("item.available", False)
            return item
        except Exception as e:
            self.logger.error("Error consuming item from source queue: %s", e)
            span.record_exception(e)
            return None

    @tracer.start_as_current_span("AwaitingApproveProcessor._process_item")
    def _process_item(self, item: Task | CapTask) -> None:
        """
        Process a consumed item.
        If approval is True, move the corresponding file from waiting_approval_dir
        to file_watch_dir; otherwise, return it to the source queue.
        """
        span = trace.get_current_span()
        item_hash = getattr(item, "hash", None)
        if item_hash is None:
            self.logger.error("Item missing 'hash' attribute, cannot process")
            span.set_status(trace.Status(trace.StatusCode.ERROR, "Missing hash"))
            return

        item_type = type(item).__name__
        span.set_attribute("processor.name", self.name)
        span.set_attribute("item.type", item_type)
        span.set_attribute("item.hash", item_hash)
        span.set_attribute("item.approval", item.approval)

        try:
            if not item.approval:
                # Not yet approved: return to source queue
                self.source_queue.add(item)
                self.items_requeued_counter.add(
                    1, {"processor.name": self.name, "item_type": item_type}
                )
                self.logger.debug(
                    "'%s' returned unapproved %s %s to source queue",
                    self.name,
                    item_type,
                    item_hash,
                )
                span.set_attribute("item.routed_to", "source_queue")
            else:
                # Approved: move file to file_watch_dir
                self._move_approved_file(item_hash, item_type, span)
                span.set_attribute("item.routed_to", "file_watch_dir")
        except Exception as e:
            self.logger.error("Failed to process %s %s: %s", item_type, item_hash, e, exc_info=True)
            span.record_exception(e)
            span.set_status(trace.Status(trace.StatusCode.ERROR, "Item processing failed"))
            # Attempt to put back into source queue to avoid losing the item
            try:
                self.source_queue.add(item)
                self.logger.warning(
                    "%s %s put back into source queue after processing error", item_type, item_hash
                )
            except Exception as push_error:
                self.logger.critical(
                    "Failed to requeue %s %s after error: %s", item_type, item_hash, push_error
                )

    # ------------------------------------------------------------------ File handling
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
            self.logger.error(error_msg)
            span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
            self.file_move_errors_counter.add(
                1, {"processor.name": self.name, "error": "file_not_found"}
            )
            # Still consider the item processed, but the file is missing.
            # Could be a race condition or manual cleanup; log and continue.
            return

        try:
            shutil.move(src_path, dst_path)
            self.files_moved_counter.add(1, {"processor.name": self.name, "item_type": item_type})
            self.logger.info(
                "'%s' moved approved %s file %s from %s to %s",
                self.name,
                item_type,
                filename,
                self.waiting_approval_dir,
                self.file_watch_dir,
            )
            span.set_attribute("file.moved", True)
        except Exception as e:
            self.logger.error("Failed to move file %s to %s: %s", src_path, dst_path, e)
            span.record_exception(e)
            self.file_move_errors_counter.add(
                1, {"processor.name": self.name, "error": "move_failed"}
            )
            raise


# ------------------------------------------------------------------ Example usage
"""
Example usage:
    from scl.queue.awaiting_approve_queue import AwaitingApproveQueue
    from scl.processor.awaiting_approve_processor import AwaitingApproveProcessor

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

# ------------------------------------------------------------------ Missing / Future Features (kept for open-source tracking)
# - Support for other file extensions (e.g., .yaml) if needed.
# - Configurable wait parameters (initial wait, max wait, idle threshold).
# - Dead-letter handling for files that repeatedly fail to move.
# - Batch processing support.
# - Integration with external health checks.
