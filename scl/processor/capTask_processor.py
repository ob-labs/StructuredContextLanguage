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
"""

import logging
import threading
import time
import os
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, Callable

# OpenTelemetry imports
from opentelemetry import trace

# ---- Fallback for project-specific imports ----
try:
    from scl.otel.otel import tracer, meter
except ImportError:
    # No‑op fallbacks (tracing & metrics become silent)
    class _NoOpTracer:
        @staticmethod
        def start_as_current_span(*args, **kwargs):
            class _NoOpSpan:
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def set_attribute(self, *args): pass
                def record_exception(self, *args): pass
                def set_status(self, *args): pass
            return _NoOpSpan()
    tracer = _NoOpTracer()
    class _NoOpMeter:
        def create_counter(self, *args, **kwargs):
            class _NoOpCounter:
                def add(self, *args, **kwargs): pass
            return _NoOpCounter()
        def create_observable_gauge(self, *args, **kwargs): pass
    meter = _NoOpMeter()
    logging.getLogger(__name__).warning("scl.otel not found; OTEL will be no‑op.")

# Import the abstract base (assumed available, but we can fallback)
try:
    from scl.meta.capability import Capability
except ImportError:
    # Provide a minimal base class so the processor can be defined
    class Capability:
        def __init__(self, name: str, *args, **kwargs):
            self.name = name
        def execute(self, args_dict: Dict[str, Any]) -> Any:
            raise NotImplementedError

try:
    from scl.meta.captask import CapTask
except ImportError:
    # Minimal stub for CapTask
    class CapTask:
        def __init__(self, cap_name: str, args: Dict[str, Any] = None, hash: str = None):
            self.cap_name = cap_name
            self.args = args or {}
            self.hash = hash or str(id(self))
            self.status = "pending"

try:
    from scl.queues.capTaskQueues import CapabilityTaskQueues
except ImportError:
    # Mock that just returns None on every consume
    class CapabilityTaskQueues:
        def register_notifier(self, name: str, callback: Callable): pass
        def consume(self, name: str) -> Optional[CapTask]:
            return None

try:
    from scl.config import config
    TODO_WATCH_DIR = config.todo_watch_dir
except ImportError:
    # Fallback to environment variable or temporary directory
    TODO_WATCH_DIR = os.environ.get("TODO_WATCH_DIR", os.path.join(os.getcwd(), "todo_watch_dir"))
    logging.getLogger(__name__).info(f"Using fallback TODO_WATCH_DIR: {TODO_WATCH_DIR}")

# ----------------------------------------------------------------------
# Directory setup
CAP_COMPLETE_DIR = os.path.join(TODO_WATCH_DIR, "CapComplete")
CAP_ERROR_DIR = os.path.join(TODO_WATCH_DIR, "CapError")
for d in [TODO_WATCH_DIR, CAP_COMPLETE_DIR, CAP_ERROR_DIR]:
    Path(d).mkdir(parents=True, exist_ok=True)

# ----------------------------------------------------------------------
# OpenTelemetry instrumentation
logger = logging.getLogger(__name__)

tasks_processed_counter = meter.create_counter(
    "capability_processor.tasks_processed",
    description="Number of CapTask instances processed by the processor"
)
tasks_succeeded_counter = meter.create_counter(
    "capability_processor.tasks_succeeded",
    description="Number of tasks that completed successfully"
)
tasks_failed_counter = meter.create_counter(
    "capability_processor.tasks_failed",
    description="Number of tasks that failed with error"
)


class CapabilityProcessor:
    """
    Example usage:

        from scl.queues.capTaskQueues import CapabilityTaskQueues
        from scl.meta.captask import CapTask
        from scl.meta.capability import Capability      # or a concrete subclass
        from scl.cap_registry import CapRegistry

        # Create registry with some capabilities
        class GreetCap(Capability):
            def execute(self, args_dict: dict):
                return f"Hello, {args_dict['name']}!"

        cap_registry = CapRegistry()
        cap_registry.register("greet", GreetCap("greet"))

        # Setup queues and processor
        queues = CapabilityTaskQueues()
        processor = CapabilityProcessor(name="greet", queue=queues, cap_registry=cap_registry)

        # Register the processor with the queue (optional, can auto‑register)
        processor.register_with_queue()

        # Start background processing
        processor.start_processing()

        # Add a task
        task = CapTask(cap_name="greet", args={"name": "World"}, hash="a1b2c3")
        queues.add(task)

        # Check status
        print(processor.status)   # 'normal' or 'idle'

        # Stop gracefully
        processor.stop()
    """

    def __init__(self, name: str, queue: CapabilityTaskQueues, cap_registry: object):
        """
        Initialize the CapabilityProcessor.

        Args:
            name: The capability name this processor handles.
            queue: The CapabilityTaskQueues instance to consume from.
            cap_registry: An object with a `get_capability(name: str) -> Capability` method.
        """
        with tracer.start_as_current_span("CapabilityProcessor.__init__") as span:
            span.set_attribute("processor.name", name)
            self.name = name
            self.queue = queue
            self.cap_registry = cap_registry

            # Wait time management
            self._wait_time = 1.0          # seconds
            self._max_wait = 300.0         # max sleep time (5 minutes)
            self._idle_threshold = 16.0    # threshold for "idle" status

            # Control flags
            self._running = False
            self._thread: Optional[threading.Thread] = None
            self._wakeup_event = threading.Event()

            # Observable gauge for idle status
            def _idle_status_callback(options):
                from opentelemetry.metrics import Observation
                return [Observation(
                    1 if self.status == "idle" else 0,
                    {"processor.name": self.name}
                )]
            meter.create_observable_gauge(
                "capability_processor.idle_status",
                callbacks=[_idle_status_callback],
                description="Indicates whether processor is idle (1) or normal (0)"
            )

            logger.info(f"CapabilityProcessor '{self.name}' initialized")

    def register_with_queue(self) -> None:
        """Register this processor's notify method as the queue notifier."""
        self.queue.register_notifier(self.name, lambda name, task: self.notify())
        logger.debug(f"Registered notify for '{self.name}' with CapabilityTaskQueues")

    @property
    def status(self) -> str:
        """Return 'idle' if wait time >= 16s, else 'normal'."""
        return "idle" if self._wait_time >= self._idle_threshold else "normal"

    @tracer.start_as_current_span("CapabilityProcessor.start_processing")
    def start_processing(self) -> None:
        """Start the background consumption loop."""
        with trace.get_current_span() as span:
            span.set_attribute("processor.name", self.name)
            if self._running:
                logger.info(f"Processor '{self.name}' is already running.")
                return

            self._running = True
            self._thread = threading.Thread(target=self._consume_loop, daemon=True)
            self._thread.start()
            logger.info(f"Processor '{self.name}' started (initial wait: {self._wait_time}s)")

    def stop(self) -> None:
        """Stop the consumption loop gracefully."""
        if not self._running:
            return
        self._running = False
        self._wakeup_event.set()  # interrupt sleep
        if self._thread:
            self._thread.join(timeout=2.0)
        logger.info(f"Processor '{self.name}' stopped")

    @tracer.start_as_current_span("CapabilityProcessor._consume_loop")
    def _consume_loop(self) -> None:
        """Main loop: fetch tasks, process, adjust wait time."""
        with trace.get_current_span() as span:
            span.set_attribute("processor.name", self.name)

        while self._running:
            task = self._get_task()
            if task is None:
                # Queue empty: double wait time, capped at max
                self._wait_time = min(self._wait_time * 2, self._max_wait)
                logger.debug(f"Queue empty. Wait time increased to {self._wait_time}s")
                self._wakeup_event.wait(timeout=self._wait_time)
                self._wakeup_event.clear()
            else:
                self._process_task(task)
                self._wait_time = 1.0  # reset to minimum after consumption
        logger.debug(f"Consume loop for '{self.name}' exited")

    @tracer.start_as_current_span("CapabilityProcessor._get_task")
    def _get_task(self) -> Optional[CapTask]:
        """Fetch one CapTask from the queue for our capability name."""
        with trace.get_current_span() as span:
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
                logger.error(f"Error consuming task from queue: {e}")
                span.record_exception(e)
                return None

    @tracer.start_as_current_span("CapabilityProcessor._process_task")
    def _process_task(self, task: CapTask) -> None:
        """
        Process a single CapTask.
        Retrieves the corresponding Capability from the registry, executes it,
        and moves the task file to success/error directories.
        """
        with trace.get_current_span() as span:
            span.set_attribute("processor.name", self.name)
            span.set_attribute("task.hash", task.hash)
            span.set_attribute("task.cap_name", task.cap_name)
            span.set_attribute("task.args_count", len(task.args))

        logger.debug(f"Processing CapTask {task.hash} for capability '{self.name}'")
        logger.info(f"Task {task.hash} processing started")

        task_file_path = os.path.join(TODO_WATCH_DIR, task.hash)

        try:
            # Obtain capability from the registry
            capability = self.cap_registry.get_capability(task.cap_name)
            if capability is None:
                error_msg = f"No capability registered for name '{task.cap_name}'"
                logger.error(error_msg)
                if span := trace.get_current_span():
                    span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
                raise ValueError(error_msg)

            if span := trace.get_current_span():
                span.set_attribute("capability.registered", True)

            # Execute (Capability.execute expects a dict)
            result = capability.execute(task.args)   # task.args should be a dict

            # --- Success path ---
            task.status = "success"
            self._safe_move(task_file_path, os.path.join(CAP_COMPLETE_DIR, task.hash), "success")
            tasks_processed_counter.add(1, {"processor.name": self.name})
            tasks_succeeded_counter.add(1, {"processor.name": self.name})
            logger.info(f"Task {task.hash} processed successfully")
            if span := trace.get_current_span():
                span.set_attribute("task.result", str(result))

        except Exception as e:
            # --- Failure path ---
            task.status = "Error"
            self._safe_move(task_file_path, os.path.join(CAP_ERROR_DIR, task.hash), "error")
            logger.error(f"Error processing task {task.hash}: {e}", exc_info=True)
            if span := trace.get_current_span():
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
            tasks_processed_counter.add(1, {"processor.name": self.name})
            tasks_failed_counter.add(1, {"processor.name": self.name})

    def _safe_move(self, src: str, dst: str, context: str):
        """Move a file, logging and gracefully handling missing sources."""
        try:
            shutil.move(src, dst)
            logger.debug(f"Moved task file from {src} to {dst}")
        except FileNotFoundError:
            logger.warning(f"Task file {src} not found during {context} move")
        except Exception as e:
            logger.error(f"Failed to move task file {src}: {e}")

    def notify(self) -> None:
        """
        External notification that new tasks may be available.
        - If current status is 'idle', wake up immediately.
        - If status is 'normal', do nothing (already active).
        """
        current_status = self.status
        logger.debug(f"Notify called. Current status: {current_status}")
        if current_status == "idle":
            logger.info("Processor is idle; waking up to consume new tasks")
            self._wakeup_event.set()
        else:
            logger.debug("Processor is normal; ignoring notification")


# Missing / Future Features (kept as comments for open-source tracking):
# - Dynamic capability registration: The processor uses a global CapRegistry
#   placeholder; in production this would integrate with the actual CapRegistry
#   implementation from the framework.
# - Graceful shutdown integration with queue: When stopping, we might want to
#   finish processing the current task before exiting (currently joins with timeout).
# - Error handling with retry/backoff for transient failures (e.g., capability execution).
# - Metrics for wait time and queue depth.
# - Configuration of wait parameters via external config (env/file).
# - Support for multiple processor instances per capability (worker pool).
# - The current fake CapRegistry and Capability are placeholders; they should be
#   replaced with actual imports once the real APIs are integrated.
# - File locking: For production, file moves might need atomic operations or locking
#   to prevent race conditions with other processes.