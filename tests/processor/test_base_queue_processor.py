"""
Tests for base_queue_processor.py
"""

import threading
import time
import unittest
from unittest.mock import (
    MagicMock,
    Mock,
    PropertyMock,
    call,
    patch,
)

from scl.processor.base_queue_processor import BaseQueueProcessor


# ---------------------------------------------------------------------------
# Concrete minimal implementations for testing
# ---------------------------------------------------------------------------
class TestProcessor(BaseQueueProcessor):
    """A concrete processor that returns items from a list."""

    def __init__(self, items=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.items = list(items) if items else []
        self.processed = []

    def _get_item(self):
        if not self.items:
            return None
        return self.items.pop(0)

    def _process_item(self, item):
        self.processed.append(item)
        time.sleep(0.001)  # simulate work


class StoppingProcessor(TestProcessor):
    """A processor that stops itself after processing the last item."""

    def _process_item(self, item):
        super()._process_item(item)
        if not self.items:  # no more items left
            self._running = False


# ---------------------------------------------------------------------------
# Base test class with patches
# ---------------------------------------------------------------------------
class TestBaseQueueProcessor(unittest.TestCase):
    def setUp(self):
        # Patch tracer, meter, trace at module level to isolate from real OTel
        patcher_tracer = patch(
            "scl.processor.base_queue_processor.tracer", MagicMock(name="tracer")
        )
        patcher_meter = patch("scl.processor.base_queue_processor.meter", MagicMock(name="meter"))
        patcher_trace = patch("scl.processor.base_queue_processor.trace", MagicMock(name="trace"))
        self.addCleanup(patcher_tracer.stop)
        self.addCleanup(patcher_meter.stop)
        self.addCleanup(patcher_trace.stop)
        self.mock_tracer = patcher_tracer.start()
        self.mock_meter = patcher_meter.start()
        self.mock_trace = patcher_trace.start()

        # Mock the span and context manager interface
        self.mock_span = MagicMock(name="span")
        self.mock_tracer.start_as_current_span.return_value.__enter__.return_value = self.mock_span
        self.mock_tracer.start_as_current_span.return_value.__exit__ = Mock()

        # mock_meter.create_counter returns a mock counter
        self.mock_counter = MagicMock(name="items_consumed_counter")
        self.mock_meter.create_counter.return_value = self.mock_counter

        # mock_meter.create_observable_gauge returns a mock gauge
        self.mock_gauge = MagicMock(name="idle_gauge")
        self.mock_meter.create_observable_gauge.return_value = self.mock_gauge

        # trace.get_current_span returns our mock_span
        self.mock_trace.get_current_span.return_value = self.mock_span

        # Reset mocks to ensure test isolation
        self.mock_tracer.reset_mock()
        self.mock_meter.reset_mock()
        self.mock_trace.reset_mock()

    # ------------------------------------------------------------------ Constructor
    def test_init_defaults(self):
        proc = TestProcessor(name="test", logger_name="test")
        self.assertEqual(proc.name, "test")
        self.assertEqual(proc._wait_time, 1.0)
        self.assertEqual(proc._max_wait, 300.0)
        self.assertEqual(proc._idle_threshold, 16.0)
        self.assertFalse(proc._running)
        self.assertIsNone(proc._thread)
        self.assertIsInstance(proc._wakeup_event, threading.Event)
        self.assertEqual(proc.logger.name, "test")
        # Metrics creation calls
        self.mock_meter.create_counter.assert_called_with(
            "test.items_consumed", description="Total items consumed from the queue"
        )
        self.mock_meter.create_observable_gauge.assert_called()
        self.assertEqual(proc._idle_value, 0)

    def test_init_custom_logger(self):
        proc = TestProcessor(name="custom", logger_name="my.logger")
        self.assertEqual(proc.logger.name, "my.logger")

    # ------------------------------------------------------------------ status property
    def test_status_normal_when_wait_below_threshold(self):
        proc = TestProcessor(name="x")
        proc._wait_time = 15.9
        self.assertEqual(proc.status, "normal")

    def test_status_idle_when_wait_at_threshold(self):
        proc = TestProcessor(name="x")
        proc._wait_time = 16.0
        self.assertEqual(proc.status, "idle")

    def test_status_idle_when_wait_above_threshold(self):
        proc = TestProcessor(name="x")
        proc._wait_time = 200.0
        self.assertEqual(proc.status, "idle")

    # ------------------------------------------------------------------ start / stop
    def test_start(self):
        proc = TestProcessor(name="start_test", logger_name="start_test")
        proc.start()
        self.assertTrue(proc._running)
        self.assertIsNotNone(proc._thread)
        proc.stop()  # clean shutdown

    def test_start_already_running(self):
        proc = TestProcessor(name="start_test", logger_name="start_test")
        proc._running = True
        proc.start()
        self.assertIsNone(proc._thread)  # no new thread created

    def test_stop_stops_thread(self):
        proc = TestProcessor(name="stop_test", logger_name="stop_test")
        proc.start()
        time.sleep(0.05)
        proc.stop()
        self.assertFalse(proc._running)
        if proc._thread:
            self.assertFalse(proc._thread.is_alive())

    def test_stop_when_not_running(self):
        proc = TestProcessor(name="stop_test", logger_name="stop_test")
        proc._running = False
        proc._thread = None
        proc.stop()  # no error expected

    # ------------------------------------------------------------------ _consume_loop core logic
    def test_consume_loop_processes_items(self):
        items = ["a", "b", "c"]
        proc = StoppingProcessor(name="test", logger_name="test", items=items)
        proc._running = True
        proc._wakeup_event = Mock()  # prevent actual blocking
        proc._consume_loop()

        # All items processed
        self.assertEqual(proc.processed, items)
        # Counter incremented once per item
        self.assertEqual(self.mock_counter.add.call_count, 3)
        # Verify span attribute was set (from the decorator span)
        self.mock_span.set_attribute.assert_any_call("processor.name", "test")

    def test_consume_loop_empty_queue_backoff(self):
        proc = TestProcessor(name="backoff", logger_name="backoff", items=[])
        proc._running = True
        proc._wakeup_event = Mock()

        # Stop after one iteration to observe backoff
        original_wait = proc._wakeup_event.wait

        def stop_after_first_wait(timeout):
            proc._running = False
            return original_wait(timeout)

        proc._wakeup_event.wait = stop_after_first_wait

        proc._consume_loop()
        self.assertEqual(proc._wait_time, 2.0)  # 1.0 -> 2.0

    def test_consume_loop_resets_wait_time_after_item(self):
        items = ["x"]
        proc = StoppingProcessor(name="reset", logger_name="reset", items=items)
        proc._running = True
        proc._wait_time = 30.0
        proc._wakeup_event = Mock()
        proc._consume_loop()
        self.assertEqual(proc._wait_time, 1.0)
        self.assertEqual(self.mock_counter.add.call_count, 1)

    def test_consume_loop_updates_idle_metric(self):
        proc = StoppingProcessor(name="idle", logger_name="idle", items=["a"])
        proc._running = True
        proc._wakeup_event = Mock()

        original_update = proc._update_idle_metric
        proc._update_idle_metric = Mock(side_effect=original_update)

        proc._consume_loop()
        proc._update_idle_metric.assert_called()
        self.assertEqual(proc._update_idle_metric.call_count, 1)

    def test_consume_loop_backoff_capped_at_max(self):
        proc = TestProcessor(name="max", logger_name="max", items=[])
        proc._running = True
        proc._wait_time = 150.0
        proc._wakeup_event = Mock()

        def stop_loop(timeout):
            proc._running = False

        proc._wakeup_event.wait = stop_loop

        proc._consume_loop()
        self.assertEqual(proc._wait_time, 300.0)  # capped at max

    # ------------------------------------------------------------------ notify
    def test_notify_when_normal_does_nothing(self):
        proc = TestProcessor(name="notify", logger_name="notify")
        proc._wait_time = 1.0
        proc._wakeup_event.clear()
        proc.notify()
        self.assertFalse(proc._wakeup_event.is_set())

    def test_notify_when_idle_sets_event(self):
        proc = TestProcessor(name="notify", logger_name="notify")
        proc._wait_time = 20.0
        proc._wakeup_event.clear()
        proc.notify()
        self.assertTrue(proc._wakeup_event.is_set())

    # ------------------------------------------------------------------ idle metric helpers
    def test_update_idle_metric_sets_value(self):
        proc = TestProcessor(name="metric", logger_name="metric")
        proc._wait_time = 16.0
        proc._update_idle_metric()
        self.assertEqual(proc._idle_value, 1)
        proc._wait_time = 1.0
        proc._update_idle_metric()
        self.assertEqual(proc._idle_value, 0)

    def test_idle_gauge_callback(self):
        proc = TestProcessor(name="gauge", logger_name="gauge")
        proc._idle_value = 1
        mock_observer = Mock()
        proc._idle_gauge_callback(mock_observer)
        mock_observer.observe.assert_called_once_with(1, {"processor.name": "gauge"})
