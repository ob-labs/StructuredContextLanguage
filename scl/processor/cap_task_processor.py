"""
CapabilityProcessor Module

Design Goals & Features:
------------------------
- Inherits from BaseQueueProcessor for common loop/backoff/status/notify.
- Bound to a specific capability name; processes tasks for that name only.
- Registers itself with a CapabilityTaskQueues instance to consume tasks.
- Uses an infinite loop (implemented in BaseQueueProcessor) to fetch and process CapTask items.
- For each CapTask:
    - Locates the file in scl.config.todo_watch_dir (named <hash>.json).
    - Obtains the corresponding Capability from a CapRegistry.
    - Invokes Capability.execute with the task arguments.
    - On success:
        - Updates the task's status to "Processed".
        - Saves the execution result in full_result (and result via its property).
        - Moves the file to todo_watch_dir/CapComplete/<hash>.json.
    - On failure (exception):
        - Updates the task's status to "Error".
        - Moves the file to todo_watch_dir/CapError/<hash>.json.

Project Constraints:
-------------------
- OpenTelemetry integrated for tracing, metrics, and structured logging.
- Logger provides info and debug levels.
- Dependencies are documented as `pip install` commands, not requirements.txt.

Installation:
    pip install opentelemetry-api opentelemetry-sdk
    (optional) pip install scl-coretools
"""

import logging
import os
import shutil
from pathlib import Path

from opentelemetry import trace

from scl.config import config
from scl.meta.captask import CapTask
from scl.otel.otel import meter, tracer
from scl.processor.base_queue_processor import BaseQueueProcessor
from scl.queue.cap_task_queues import CapabilityTaskQueues

# ----------------------------------------------------------------------
# Directory setup
TODO_WATCH_DIR = config.todo_watch_dir
CAP_COMPLETE_DIR = os.path.join(TODO_WATCH_DIR, "CapComplete")
CAP_ERROR_DIR = os.path.join(TODO_WATCH_DIR, "CapError")
for d in [TODO_WATCH_DIR, CAP_COMPLETE_DIR, CAP_ERROR_DIR]:
    Path(d).mkdir(parents=True, exist_ok=True)

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Common metrics
tasks_processed_counter = meter.create_counter(
    "capability_processor.tasks_processed", description="Total CapTask instances processed"
)
tasks_succeeded_counter = meter.create_counter(
    "capability_processor.tasks_succeeded",
    description="Tasks that completed successfully (status set to Processed)",
)
tasks_failed_counter = meter.create_counter(
    "capability_processor.tasks_failed", description="Tasks that failed with an error"
)


class CapabilityProcessor(BaseQueueProcessor):
    """
    Processes CapTask instances for a specific capability name.

    Inherits the run loop, backoff, and graceful shutdown from BaseQueueProcessor.
    Full usage examples are provided at the bottom of the file.
    """

    # Abbreviated usage (see bottom for full example):
    #   processor = CapabilityProcessor(name="greet", queue=queues, cap_registry=reg)
    #   processor.start()

    def __init__(self, name: str, queue: CapabilityTaskQueues, cap_registry: object):
        """
        Args:
            name: The capability name this processor handles.
            queue: CapabilityTaskQueues instance to consume tasks from.
            cap_registry: Object with a get_capability(name: str) -> Capability method.
        """
        super().__init__(name=name, logger_name=__name__)
        self.queue = queue
        self.cap_registry = cap_registry

        # Register itself with the queue (both processor and notifier)
        self.register_with_queue()
        logger.info("CapabilityProcessor '%s' ready", self.name)

    def register_with_queue(self):
        """Register processor and its notifier with the queue."""
        try:
            self.queue.register_processor(self.name, self)
        except AttributeError:
            logger.debug("Queue does not support register_processor; ignoring.")

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
    def _get_item(self) -> CapTask | None:
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
        Process a single CapTask:
        1. Locate its file (<hash>.json) in the watch directory.
        2. Retrieve the capability from the registry.
        3. Execute and store result / handle errors.
        4. Move the file to the appropriate completion/error directory.
        """
        span = trace.get_current_span()
        span.set_attribute("processor.name", self.name)
        span.set_attribute("task.hash", item.hash)
        span.set_attribute("task.cap_name", item.cap_name)
        span.set_attribute("task.args_count", len(item.args))

        # Filename matches the pattern written by CapTask.__post_init__
        task_file = os.path.join(TODO_WATCH_DIR, f"{item.hash}.json")
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
            result_str = str(result) if result is not None else ""
            logger.debug(
                "Capability executed for task %s, result length: %d", item.hash, len(result_str)
            )

            # Success path: update status and store result
            # Note: CapTask uses "Processed" for successful completion, not "success"
            item.status = "Processed"
            item.full_result = result_str

            # Move the file to CapComplete directory
            dest_file = os.path.join(CAP_COMPLETE_DIR, f"{item.hash}.json")
            self._safe_move(task_file, dest_file, "success")

            logger.info("Task %s completed successfully", item.hash)
            span.set_attribute("task.result_length", len(result_str))

            tasks_processed_counter.add(1, {"processor.name": self.name})
            tasks_succeeded_counter.add(1, {"processor.name": self.name})

        except Exception as e:
            # Failure path
            item.status = "Error"
            item.full_result = ""
            dest_file = os.path.join(CAP_ERROR_DIR, f"{item.hash}.json")
            self._safe_move(task_file, dest_file, "error")

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

# ----------------------------------------------------------------------
# Example usage
# =============
"""
Example usage (how other parts of the system invoke/reference this module):

    from scl.queue.cap_task_queues import CapabilityTaskQueues
    from scl.meta.captask import CapTask
    from scl.cap_registry import CapRegistry
    from scl.processor.capability_processor import CapabilityProcessor  # this module
    from scl.meta.capability import Capability

    # 1. Define a concrete capability
    class GreetCap(Capability):
        def execute(self, args):
            # CapTask passes args as a list, so we unpack accordingly
            name = args[0] if args else "stranger"
            return f"Hello, {name}!"

    # 2. Register it
    cap_registry = CapRegistry()
    cap_registry.register("greet", GreetCap("greet"))

    # 3. Create the queue and processor
    queues = CapabilityTaskQueues()
    processor = CapabilityProcessor(
        name="greet",
        queue=queues,
        cap_registry=cap_registry
    )

    # 4. Start processing in a background thread
    processor.start()

    # 5. Add a task (the processor will pick it up automatically)
    task = CapTask(
        cap_name="greet",
        args=["World"],            # must be a list
        task_hash="workflow-001",
        approval=True
    )
    queues.add(task)

    # 6. (Optional) Stop the processor gracefully
    processor.stop()
"""
