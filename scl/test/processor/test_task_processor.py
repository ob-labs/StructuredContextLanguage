"""
Unit tests for scl.processor.task_processor.TaskProcessor.
"""
import sys
import functools
import pytest
from unittest.mock import Mock, patch, PropertyMock

import scl.otel.otel as otel_module


# ---------------------------------------------------------------------------
# Custom mock context manager / decorator used to replace
# tracer.start_as_current_span.
# ---------------------------------------------------------------------------
class _MockSpanCtx:
    """Acts as both a context manager and a decorator for OTel spans."""
    def __init__(self, mock_span):
        self.mock_span = mock_span

    def __enter__(self):
        return self.mock_span

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def __call__(self, func):
        """Make the instance a decorator that wraps the original function."""
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return wrapper


@pytest.fixture
def mock_input_queue():
    """A mock TaskQueue that accepts register_processor."""
    queue = Mock(name="input_queue")
    queue.register_processor = Mock()
    return queue


def _create_patched_task_processor(monkeypatch, input_queue, name="test_processor"):
    """
    Replace the real tracer/meter with fully mocked versions so that
    decorators and context managers in the processor can be controlled.
    Returns (processor, mock_span, mock_counter).
    """
    # ----- mock span (passes OTel validity checks if needed) -----
    mock_span = Mock(name="span")
    mock_span.get_span_context.return_value = Mock(is_valid=True)

    # ----- mocked tracer -----
    mock_tracer = Mock(name="tracer")
    # start_as_current_span must return something that works as a
    # context manager AND as a decorator factory.
    mock_ctx = _MockSpanCtx(mock_span)
    mock_tracer.start_as_current_span = Mock(return_value=mock_ctx)

    # ----- mocked meter -----
    mock_meter = Mock(name="meter")
    mock_counter = Mock(name="counter")
    mock_meter.create_counter = Mock(return_value=mock_counter)

    # Patch the entire tracer / meter objects in otel_module so that
    # the re‑imported task_processor picks them up.
    monkeypatch.setattr(otel_module, "tracer", mock_tracer)
    monkeypatch.setattr(otel_module, "meter", mock_meter)

    # Force re‑import of task_processor so that its global names
    # tracer / meter are bound to our mock objects.
    if "scl.processor.task_processor" in sys.modules:
        del sys.modules["scl.processor.task_processor"]
    keys_to_remove = [
        k for k in sys.modules if k.startswith("scl.processor.task_processor")
    ]
    for key in keys_to_remove:
        del sys.modules[key]

    import scl.processor.task_processor as task_mod

    # Patch get_current_span on the exact trace module used by task_processor.
    # This ensures that inside _process_item the call returns our mock_span.
    monkeypatch.setattr(task_mod.trace, "get_current_span",
                        Mock(return_value=mock_span))

    TaskProcessor = task_mod.TaskProcessor
    tp = TaskProcessor(input_queue, name=name)
    return tp, mock_span, mock_counter


@pytest.fixture
def processor(mock_input_queue, monkeypatch):
    """Fixture providing a fully mocked TaskProcessor."""
    tp, mock_span, mock_counter = _create_patched_task_processor(
        monkeypatch, mock_input_queue, name="test_processor"
    )
    return tp, mock_input_queue, mock_span, mock_counter


@pytest.fixture
def dummy_task():
    """A standard Task mock."""
    task = Mock(name="task", spec=["id", "type"])
    task.id = 42
    task.type = "test_type"
    return task


class TestTaskProcessorInit:
    def test_initialization_registers_with_queue(self, processor):
        tp, input_queue, *_ = processor
        input_queue.register_processor.assert_called_once_with(tp)

    def test_default_name_sets_logger(self, mock_input_queue, monkeypatch):
        # Instantiate with the default name
        tp, *_ = _create_patched_task_processor(monkeypatch, mock_input_queue,
                                                name="task_processor")
        assert tp.name == "task_processor"

    def test_metrics_counter_created(self, processor):
        tp, _, _, mock_counter = processor
        # After patching, otel_module.meter is our mock meter
        otel_module.meter.create_counter.assert_called_with(
            "test_processor.processing_errors",
            description="Number of errors while processing individual tasks"
        )


class TestGetItem:
    def test_get_item_returns_task(self, processor):
        tp, input_queue, *_ = processor
        mock_task = Mock(name="task")
        input_queue.get.return_value = mock_task

        result = tp._get_item()
        assert result is mock_task
        input_queue.get.assert_called_once_with(block=False)

    def test_get_item_empty_queue_returns_none(self, processor):
        tp, input_queue, *_ = processor
        input_queue.get.side_effect = Exception("Queue empty")

        result = tp._get_item()
        assert result is None
        input_queue.get.assert_called_once_with(block=False)


class TestProcessItem:
    def test_process_item_success(self, processor, dummy_task):
        tp, _, mock_span, mock_counter = processor

        with patch("time.sleep", return_value=None) as mock_sleep:
            tp._process_item(dummy_task)

        # The decorator runs the real method, which calls set_attribute twice
        assert mock_span.set_attribute.call_count == 2
        mock_span.set_attribute.assert_any_call("task.id", "42")
        mock_span.set_attribute.assert_any_call("task.type", "test_type")
        mock_sleep.assert_called_once_with(0.1)
        mock_counter.add.assert_not_called()
        mock_span.record_exception.assert_not_called()

    def test_process_item_failure(self, processor, dummy_task):
        tp, _, mock_span, mock_counter = processor

        with patch("time.sleep",
                   side_effect=Exception("processing failure")):
            with pytest.raises(Exception):
                tp._process_item(dummy_task)

        mock_span.set_attribute.assert_any_call("task.id", "42")
        mock_span.record_exception.assert_called_once()
        mock_counter.add.assert_called_once_with(
            1, {"processor.name": "test_processor"}
        )

    def test_process_item_unknown_id_and_type(self, processor):
        tp, _, mock_span, mock_counter = processor
        task = Mock(spec=[])

        with patch("time.sleep", return_value=None):
            tp._process_item(task)

        mock_span.set_attribute.assert_any_call("task.id", "unknown")
        mock_span.set_attribute.assert_any_call("task.type", "unknown")


class TestNotify:
    def test_notify_calls_super_and_sets_span_attributes(self, processor):
        tp, _, mock_span, mock_counter = processor

        # Provide enough 'running' values so that the base class accesses
        # self.status as many times as needed and still returns "running" last.
        status_values = ["idle"] + ["running"] * 20
        with patch.object(type(tp), "status",
                          new_callable=PropertyMock) as mock_status:
            mock_status.side_effect = status_values
            tp.notify()

        # The notify method uses `with tracer.start_as_current_span(...) as span:`
        # and our mock_ctx returns mock_span.  Set_attribute calls are on mock_span.
        mock_span.set_attribute.assert_any_call("processor.status_before",
                                                "idle")
        mock_span.set_attribute.assert_any_call("processor.status_after",
                                                "running")

    def test_notify_propagates_to_base_class(self, processor):
        tp, _, _, _ = processor
        with patch.object(tp.__class__.__bases__[0], "notify",
                          autospec=True) as super_notify:
            tp.notify()
            super_notify.assert_called_once_with(tp)