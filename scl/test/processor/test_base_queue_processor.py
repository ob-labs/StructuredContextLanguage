"""
Unit tests for BaseQueueProcessor.
"""

import threading
from unittest.mock import Mock, patch, call, ANY

import pytest

from scl.processor.base_queue_processor import BaseQueueProcessor


# ---------------------------------------------------------------------------
# Concrete subclass for testing
# ---------------------------------------------------------------------------
class TestProcessor(BaseQueueProcessor):
    """Concrete implementation that returns items from a predefined list."""

    __test__ = False  # prevent pytest from collecting as a test class

    def __init__(self, name="test-proc", items=None):
        super().__init__(name)
        self.items = list(items or [])
        self.processed = []  # record processed items

    def _get_item(self):
        if self.items:
            return self.items.pop(0)
        return None

    def _process_item(self, item):
        self.processed.append(item)


class StoppingProcessor(TestProcessor):
    """Processor that stops after processing the first item."""
    def _process_item(self, item):
        super()._process_item(item)
        self._running = False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def mock_otel():
    """Mock OpenTelemetry dependencies so we don't need a real SDK."""
    with patch("scl.processor.base_queue_processor.tracer") as mock_tracer, \
         patch("scl.processor.base_queue_processor.meter") as mock_meter, \
         patch("scl.processor.base_queue_processor.trace") as mock_trace:

        # Make tracer.start_as_current_span work as a simple decorator
        def start_as_current_span(name):
            def decorator(f):
                def wrapper(*args, **kwargs):
                    return f(*args, **kwargs)
                return wrapper
            return decorator

        mock_tracer.start_as_current_span.side_effect = start_as_current_span
        mock_trace.get_current_span.return_value = Mock()

        # Meter returns mocks that we can inspect
        mock_counter = Mock()
        mock_gauge = Mock()
        mock_meter.create_counter.return_value = mock_counter
        mock_meter.create_observable_gauge.return_value = mock_gauge

        yield mock_tracer, mock_meter, mock_trace, mock_counter, mock_gauge


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------
class TestBaseQueueProcessor:

    def test_initialization(self, mock_otel):
        """Verify constructor sets default values."""
        proc = TestProcessor(name="my-proc")
        assert proc.name == "my-proc"
        assert proc._wait_time == 1.0
        assert proc._max_wait == 300.0
        assert proc._idle_threshold == 16.0
        assert proc._running is False
        assert proc._thread is None
        assert isinstance(proc._wakeup_event, threading.Event)
        assert proc._idle_value == 0

        # Metrics should have been created
        _, mock_meter, _, mock_counter, mock_gauge = mock_otel
        mock_meter.create_counter.assert_called_once_with(
            "my-proc.items_consumed", description=ANY
        )
        mock_meter.create_observable_gauge.assert_called_once_with(
            "my-proc.idle", description=ANY, callbacks=[proc._idle_gauge_callback]
        )

    def test_status_property_normal(self):
        proc = TestProcessor()
        proc._wait_time = 1.0
        assert proc.status == "normal"

    def test_status_property_idle_equal_threshold(self):
        proc = TestProcessor()
        proc._wait_time = 16.0
        assert proc.status == "idle"

    def test_status_property_idle_above_threshold(self):
        proc = TestProcessor()
        proc._wait_time = 300.0
        assert proc.status == "idle"

    def test_notify_idle_triggers_wakeup(self):
        proc = TestProcessor()
        proc._wait_time = 20.0  # idle

        proc._wakeup_event = Mock()
        proc.notify()
        proc._wakeup_event.set.assert_called_once()

    def test_notify_normal_does_nothing(self):
        proc = TestProcessor()
        proc._wait_time = 1.0  # normal

        proc._wakeup_event = Mock()
        proc.notify()
        proc._wakeup_event.set.assert_not_called()

    def test_update_idle_metric(self):
        proc = TestProcessor()
        proc._wait_time = 20.0  # idle
        proc._update_idle_metric()
        assert proc._idle_value == 1

        proc._wait_time = 1.0
        proc._update_idle_metric()
        assert proc._idle_value == 0

    def test_idle_gauge_callback(self):
        proc = TestProcessor()
        proc._idle_value = 1
        assert proc._idle_gauge_callback(None) == 1

    def test_start_creates_thread_and_sets_running(self, mock_otel):
        proc = TestProcessor()
        with patch("threading.Thread") as mock_thread:
            proc.start()
            mock_thread.assert_called_once_with(
                target=proc._consume_loop, daemon=True, name=proc.name
            )
            mock_thread.return_value.start.assert_called_once()
            assert proc._running is True
            assert proc._thread is not None

    def test_start_already_running_does_nothing(self, mock_otel):
        proc = TestProcessor()
        proc._running = True
        with patch("threading.Thread") as mock_thread:
            proc.start()
            mock_thread.assert_not_called()

    def test_stop_graceful_shutdown(self):
        proc = TestProcessor()
        proc._running = True
        proc._wakeup_event = Mock()
        mock_thread = Mock()
        proc._thread = mock_thread

        proc.stop()

        assert proc._running is False
        proc._wakeup_event.set.assert_called_once()
        mock_thread.join.assert_called_once_with(timeout=2.0)

    def test_stop_when_not_running_does_nothing(self):
        proc = TestProcessor()
        proc._wakeup_event = Mock()
        proc.stop()
        proc._wakeup_event.set.assert_not_called()

    # -----------------------------------------------------------------------
    # Consume loop tests (run synchronously)
    # -----------------------------------------------------------------------
    def test_consume_loop_processes_items(self, mock_otel):
        *_, mock_counter, _ = mock_otel

        items = ["a", "b", "c"]
        proc = TestProcessor(items=items)
        proc._running = True

        proc._wakeup_event = Mock()

        original_process = proc._process_item
        def process_item_and_stop(item):
            original_process(item)
            if not proc.items:   # no more items
                proc._running = False
        proc._process_item = process_item_and_stop

        proc._consume_loop()

        assert proc.processed == items
        assert mock_counter.add.call_count == 3
        mock_counter.add.assert_has_calls([
            call(1, {"processor.name": "test-proc"}),
            call(1, {"processor.name": "test-proc"}),
            call(1, {"processor.name": "test-proc"}),
        ])

    def test_consume_loop_exponential_backoff(self, mock_otel):
        """When queue is empty, wait_time doubles before each wait."""
        proc = TestProcessor(items=[])  # always empty
        proc._running = True

        proc._wakeup_event = Mock()
        iteration = [0]

        original_get = proc._get_item
        def limited_get():
            iteration[0] += 1
            if iteration[0] >= 5:   # stop after 5 empty cycles
                proc._running = False
            return original_get()
        proc._get_item = limited_get

        wait_spy = Mock()
        def wait_side_effect(timeout):
            wait_spy(timeout)
        proc._wakeup_event.wait.side_effect = wait_side_effect

        proc._consume_loop()

        expected_timeouts = [2.0, 4.0, 8.0, 16.0, 32.0]
        wait_calls = [call[0][0] for call in wait_spy.call_args_list]
        assert wait_calls == expected_timeouts
        assert proc._wait_time == 32.0

    def test_consume_loop_backoff_capped_at_max(self, mock_otel):
        """Wait time should never exceed _max_wait (300s)."""
        proc = TestProcessor(items=[])
        proc._running = True
        proc._wait_time = 200.0

        proc._wakeup_event = Mock()
        wait_spy = Mock()
        proc._wakeup_event.wait.side_effect = lambda timeout: wait_spy(timeout)

        iteration = [0]
        original_get = proc._get_item
        def limited_get():
            iteration[0] += 1
            if iteration[0] >= 3:   # 3 waits
                proc._running = False
            return original_get()
        proc._get_item = limited_get

        proc._consume_loop()

        expected = [300.0, 300.0, 300.0]
        actual = [call[0][0] for call in wait_spy.call_args_list]
        assert actual == expected

    def test_consume_loop_resets_wait_time_after_item(self, mock_otel):
        """After a successful consumption, wait_time should reset to 1.0."""
        items = ["x"]
        proc = StoppingProcessor(items=items)
        proc._running = True          # ← required for the loop to run
        proc._wait_time = 30.0

        proc._wakeup_event = Mock()

        proc._consume_loop()

        assert proc._wait_time == 1.0
        assert proc.processed == items

    def test_consume_loop_calls_wakeup_event_wait_on_empty(self, mock_otel):
        """Ensure wakeup_event.wait() is called with the (doubled) current wait_time."""
        proc = TestProcessor(items=[])
        proc._running = True
        proc._wait_time = 5.0

        proc._wakeup_event = Mock()
        wait_spy = Mock()
        proc._wakeup_event.wait.side_effect = lambda timeout: wait_spy(timeout)

        # Run only one iteration
        def one_iteration():
            proc._running = False
            return None
        proc._get_item = one_iteration

        proc._consume_loop()

        wait_spy.assert_called_once_with(10.0)
        assert proc._wait_time == 10.0

    def test_consume_loop_clears_event_after_wait(self, mock_otel):
        """After wakeup_event.wait(), clear() must be called."""
        proc = TestProcessor(items=[])
        proc._running = True

        proc._wakeup_event = Mock()
        wait_spy = Mock()
        proc._wakeup_event.wait.side_effect = lambda timeout: wait_spy(timeout)

        def get_item_and_stop():
            proc._running = False
            return None
        proc._get_item = get_item_and_stop

        proc._consume_loop()

        proc._wakeup_event.clear.assert_called()

    def test_consume_loop_updates_idle_metric(self, mock_otel):
        """Verify _update_idle_metric is called after each fetch (empty or not)."""
        proc = StoppingProcessor(items=["a"])
        proc._running = True

        proc._wakeup_event = Mock()
        original_update = proc._update_idle_metric
        proc._update_idle_metric = Mock(side_effect=original_update)

        proc._consume_loop()
        assert proc._update_idle_metric.call_count == 1

        proc._running = True
        proc.items = []
        def get_item_and_stop():
            proc._running = False
            return None
        proc._get_item = get_item_and_stop

        proc._consume_loop()
        assert proc._update_idle_metric.call_count == 2