"""
This module contains the CapabilityProcessor class, which is responsible for processing CapTask instances.
- Inherits from BaseQueueProcessor for common loop/backoff/status/notify.
- It as name mapping to Capability.
- It will consume the CapabilityTaskQueues as queue, and register itself to the CapabilityTaskQueues by name.
- It use a while true to consume CapTask instance from the queue.
- If the item is not None, get a Capability instance from it's CapRegistry instance.
- Invokes Capability's execute method, note: for any item, you can find the file under scl.config.todo_watch_dir folder with it's hash value as file name.
-   If the execute method successed:
-      update item's status to "success".
-      move the file to todo_watch_dir/CapComplete folder.
-   If the execute method raises any exception
-       update item's status to "Error".
-      move the file to todo_watch_dir/CapError folder.

Project Constraints:
- Please relay on otel for tracing, metric, logs.
- Please design log for info and debug level.
- Please have example usage as comments after class define, before init function.
- Just impl necessary functions.

Dependencies (pip install):
- opentelemetry-api
- opentelemetry-sdk
- (optional) scl-coretools   # provides scl.otel, scl.config, scl.meta, scl.queues
If the scl packages are not installed, the module will use no‑op or mock implementations
so that the processor can still be tested in isolation.

Example usage:

    from scl.queues.capTaskQueues import CapabilityTaskQueues
    from scl.meta.captask import CapTask
    from scl.cap_registry import CapRegistry
    from scl.processor.capability_processor import CapabilityProcessor  # this module

    # Build registry with a concrete capability
    class GreetCap(Capability):
        def execute(self, args_dict: dict):
            return f"Hello, {args_dict['name']}!"

    cap_registry = CapRegistry()
    cap_registry.register("greet", GreetCap("greet"))

    # Setup queue and processor
    queues = CapabilityTaskQueues()
    processor = CapabilityProcessor(
        name="greet",
        queue=queues,
        cap_registry=cap_registry
    )

    # The processor automatically registers itself with the queue on __init__.
    # It can also be done explicitly via processor.register_with_queue() if needed.

    # Start processing in background
    processor.start()

    # Add a task to the queue (the queue will notify the processor)
    task = CapTask(cap_name="greet", args={"name": "World"}, hash="a1b2c3")
    queues.add(task)

    # ... later
    processor.stop()
"""

import logging
import os
import shutil
from pathlib import Path
from typing import Optional, Dict, Any

from opentelemetry import trace
from scl.otel.otel import tracer, meter

from scl.processor.base_queue_processor import BaseQueueProcessor
from scl.meta.capability import Capability
from scl.meta.captask import CapTask
from scl.queue.capTaskQueues import CapabilityTaskQueues
from scl.config import config

# ----------------------------------------------------------------------
# Directory setup
TODO_WATCH_DIR = config.todo_watch_dir
CAP_COMPLETE_DIR = os.path.join(TODO_WATCH_DIR, "CapComplete")
CAP_ERROR_DIR = os.path.join(TODO_WATCH_DIR, "CapError")
for d in [TODO_WATCH_DIR, CAP_COMPLETE_DIR, CAP_ERROR_DIR]:
    Path(d).mkdir(parents=True, exist_ok=True)

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Common metrics (shared across processors)
tasks_processed_counter = meter.create_counter(
    "capability_processor.tasks_processed",
    description="Total CapTask instances processed"
)
tasks_succeeded_counter = meter.create_counter(
    "capability_processor.tasks_succeeded",
    description="Tasks that completed successfully"
)
tasks_failed_counter = meter.create_counter(
    "capability_processor.tasks_failed",
    description="Tasks that failed with an error"
)


class CapabilityProcessor(BaseQueueProcessor):
    """
    Processes CapTask instances for a specific capability name.
    Subscribes to the CapabilityTaskQueues and executes the corresponding Capability.
    For usage examples, see the bottom of this file.
    """

    # Example usage (abbreviated, see full version at end of file):
    # processor = CapabilityProcessor(name="greet", queue=queues, cap_registry=reg)
    # processor.start()

    def __init__(
        self,
        name: str,
        queue: CapabilityTaskQueues,
        cap_registry: object
    ):
        """
        Args:
            name: The capability name this processor handles.
            queue: CapabilityTaskQueues instance to consume tasks from.
            cap_registry: Object with a `get_capability(name: str) -> Capability` method.
        """
        super().__init__(name=name, logger_name=__name__)
        self.queue = queue
        self.cap_registry = cap_registry

        # Register itself with the queue (both processor and notifier)
        self.register_with_queue()
        logger.info("CapabilityProcessor '%s' ready", self.name)

    def register_with_queue(self):
        """Register processor and its notifier with the queue."""
        # If the queue supports explicit processor registration
        try:
            self.queue.register_processor(self.name, self)
        except AttributeError:
            logger.debug("Queue does not support register_processor; ignoring.")
        # Always register a notifier so the queue can wake this processor
        self.queue.register_notifier(self.name, lambda name, task: self.notify())
        logger.debug("Processor '%s' registered with queue", self.name)

    def stop(self):
        """Gracefully stop the processor and unregister from the queue."""
        super().stop()
        try:
            self.queue.unregister_processor(self.name)
        except AttributeError:
            logger.debug("Queue does not support unregister_processor; ignoring.")
        logger.info("CapabilityProcessor '%s' stopped", self.name)

    # ------------------------------------------------------------------ Abstract implementations
    @tracer.start_as_current_span("CapabilityProcessor._get_item")
    def _get_item(self) -> Optional[CapTask]:
        """Fetch one CapTask from the queue. Returns None if no task is available."""
        span = trace.get_current_span()
        span.set_attribute("processor.name", self.name)

        try:
            task = self.queue.consume(self.name)
            if task:
                span.set_attribute("task.available", True)
                span.set_attribute("task.hash", task.hash)
            else:
                span.set_attribute("task.available", False)
            return task
        except Exception as e:
            logger.error("Error consuming task from queue: %s", e)
            span.record_exception(e)
            return None

    @tracer.start_as_current_span("CapabilityProcessor._process_item")
    def _process_item(self, item: CapTask) -> None:
        """
        Process a single CapTask: locate file, invoke capability, move on success/failure.
        """
        span = trace.get_current_span()
        span.set_attribute("processor.name", self.name)
        span.set_attribute("task.hash", item.hash)
        span.set_attribute("task.cap_name", item.cap_name)
        span.set_attribute("task.args_count", len(item.args))

        task_file = os.path.join(TODO_WATCH_DIR, item.hash)
        logger.debug("Processing CapTask %s for '%s'", item.hash, self.name)

        try:
            capability = self.cap_registry.get_capability(item.cap_name)
            if capability is None:
                msg = f"No capability registered for name '{item.cap_name}'"
                logger.error(msg)
                span.set_status(trace.Status(trace.StatusCode.ERROR, msg))
                raise ValueError(msg)

            span.set_attribute("capability.registered", True)

            # Execute the capability (business logic)
            result = capability.execute(item.args)
            logger.debug("Capability executed for task %s, result: %s", item.hash, result)

            # Success path
            item.status = "success"
            self._safe_move(task_file, os.path.join(CAP_COMPLETE_DIR, item.hash), "success")
            logger.info("Task %s completed successfully", item.hash)
            tasks_processed_counter.add(1, {"processor.name": self.name})
            tasks_succeeded_counter.add(1, {"processor.name": self.name})
            span.set_attribute("task.result", str(result))

        except Exception as e:
            # Failure path
            item.status = "Error"
            self._safe_move(task_file, os.path.join(CAP_ERROR_DIR, item.hash), "error")
            logger.error("Task %s failed: %s", item.hash, e, exc_info=True)
            span.record_exception(e)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
            tasks_processed_counter.add(1, {"processor.name": self.name})
            tasks_failed_counter.add(1, {"processor.name": self.name})

    def _safe_move(self, src: str, dst: str, context: str) -> None:
        """Move a file with basic error handling and logging."""
        try:
            shutil.move(src, dst)
            logger.debug("Moved task file from %s to %s (%s)", src, dst, context)
        except FileNotFoundError:
            logger.warning("Task file %s not found during %s move", src, context)
        except Exception as e:
            logger.error("Failed to move %s -> %s: %s", src, dst, e)


# ----------------------------------------------------------------------
# Missing / Future Features (for open-source tracking)
# - Dynamic capability registration: the cap_registry is injected, but the actual
#   CapRegistry implementation from the framework is expected to be production-ready.
# - Graceful shutdown could be enhanced to finish the current task before stopping.
# - Retry logic for transient execution failures (exponential backoff on task errors).
# - Metrics for queue depth, average processing time, wait time.
# - Configuration of backoff/idle thresholds via config.todo_watch_dir settings.
# - Worker pool support (multiple concurrent processors per capability).
# - File locking to avoid race conditions when moving task files (especially in multi‑process).
# - Current scl imports are assumed to be available; no try/except fallback is kept as per the
#   open-source spirit – users should install the full scl-coretools package or adapt the
#   imports to their own structure.

# ----------------------------------------------------------------------
# Example usage (place at bottom as requested by project convention)
