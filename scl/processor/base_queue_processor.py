"""
base_queue_processor.py – Abstract base for queue‑consuming processors.

Design Goals (shared by all processors):
  - Infinite loop consuming items from a queue.
  - Exponential backoff: double wait time on empty queue, max 300 s.
  - Status "idle" when wait time ≥ 16 s, otherwise "normal".
  - Notify method: if idle, interrupt sleep and fetch immediately; if normal, ignore.
  - OpenTelemetry tracing, metrics, and structured logging.

Dependencies (pip install):
  - opentelemetry-api
  - opentelemetry-sdk
  - scl-coretools  (optional; if missing, OTEL becomes no‑op)
"""

import abc
import logging
import threading
import time
from typing import Any, Optional

from scl.otel.otel import tracer, meter
from opentelemetry import trace


class BaseQueueProcessor(abc.ABC):
    """
    Abstract processor that fetches items from a queue and processes them
    with exponential backoff on empty queues.

    Subclasses must implement:
        _get_item() -> Optional[Any]   – fetch one item (return None if empty)
        _process_item(item: Any) -> None – process a single item

    Example usage (with a concrete subclass):
        class MyProcessor(BaseQueueProcessor):
            def _get_item(self):
                return self.queue.pop()   # must return None on empty
            def _process_item(self, item):
                # process item
                pass

        proc = MyProcessor(name="my-proc")
        proc.start()
        # ...
        proc.stop()
    """

    def __init__(
        self,
        name: str,
        logger_name: Optional[str] = None,
    ):
        self.name = name
        self.logger = logging.getLogger(logger_name or __name__)

        # Wait‑time / backoff parameters
        self._wait_time = 1.0          # seconds
        self._max_wait = 300.0         # 5 minutes
        self._idle_threshold = 16.0    # status becomes idle after this many seconds

        # Control flags
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._wakeup_event = threading.Event()  # triggered by notify()

        # ----------- Common metrics -----------
        self.items_consumed_counter = meter.create_counter(
            f"{self.name}.items_consumed",
            description="Total items consumed from the queue"
        )

        # Use an ObservableGauge to report the current idle status (0=normal, 1=idle)
        # This gives an accurate instantaneous reading rather than accumulating adds.
        self.idle_gauge = meter.create_observable_gauge(
            f"{self.name}.idle",
            description="Current idle status (1=idle, 0=normal)",
            callbacks=[self._idle_gauge_callback]
        )
        # We need to store the current value so the callback can return it.
        self._idle_value = 0

        self.logger.info("%s initialized", self.name)

    # ------------------------------------------------------------------ Status
    @property
    def status(self) -> str:
        """'idle' if wait_time ≥ 16 s, else 'normal'."""
        return "idle" if self._wait_time >= self._idle_threshold else "normal"

    # ------------------------------------------------------------------ Lifecycle
    @tracer.start_as_current_span("BaseQueueProcessor.start")
    def start(self) -> None:
        """Start the background consumption thread."""
        if self._running:
            self.logger.info("%s already running.", self.name)
            return

        self._running = True
        self._thread = threading.Thread(target=self._consume_loop, daemon=True, name=self.name)
        self._thread.start()
        self.logger.info("%s started (wait=%.1fs)", self.name, self._wait_time)

    def stop(self) -> None:
        """Gracefully stop the processor."""
        if not self._running:
            return
        self._running = False
        self._wakeup_event.set()      # interrupt sleep
        if self._thread:
            self._thread.join(timeout=2.0)
        self.logger.info("%s stopped.", self.name)

    # ------------------------------------------------------------------ Core loop
    @tracer.start_as_current_span("BaseQueueProcessor._consume_loop")
    def _consume_loop(self) -> None:
        """Main fetch‑process cycle with exponential backoff."""
        span = trace.get_current_span()
        span.set_attribute("processor.name", self.name)

        while self._running:
            item = self._get_item()

            if item is None:
                # Queue empty → exponential backoff
                self._wait_time = min(self._wait_time * 2, self._max_wait)
                self.logger.debug("%s: queue empty, wait=%ss", self.name, self._wait_time)
                self._update_idle_metric()
                self._wakeup_event.wait(timeout=self._wait_time)
                self._wakeup_event.clear()
            else:
                self._process_item(item)
                # Reset backoff after successful consumption
                self._wait_time = 1.0
                self._update_idle_metric()
                # Increment items consumed counter
                self.items_consumed_counter.add(1, {"processor.name": self.name})
        self.logger.debug("%s: consume loop exited.", self.name)

    # ------------------------------------------------------------------ Abstract methods
    @abc.abstractmethod
    def _get_item(self) -> Optional[Any]:
        """
        Fetch one item from the queue. Must return None if no item is available.
        """
        ...

    @abc.abstractmethod
    def _process_item(self, item: Any) -> None:
        """Process a single item. Subclass business logic goes here."""
        ...

    # ------------------------------------------------------------------ Notification
    def notify(self) -> None:
        """
        External notification – if currently 'idle', wake up immediately;
        if 'normal', do nothing.
        """
        st = self.status
        self.logger.debug("%s notified, status=%s", self.name, st)
        if st == "idle":
            self.logger.info("%s is idle, waking up.", self.name)
            self._wakeup_event.set()
        else:
            self.logger.debug("%s is normal, ignoring notification.", self.name)

    # ------------------------------------------------------------------ Helper
    def _update_idle_metric(self) -> None:
        """Update the stored idle gauge value based on current status."""
        self._idle_value = 1 if self.status == "idle" else 0

    def _idle_gauge_callback(self, _):
        """Callback for ObservableGauge – returns the current idle value."""
        return self._idle_value