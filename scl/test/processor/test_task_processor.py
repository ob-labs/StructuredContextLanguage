"""
Unit tests for scl.processor.task_processor.TaskProcessor.
"""
import pytest
from unittest.mock import Mock, patch, PropertyMock

from scl.processor.task_processor import TaskProcessor


@pytest.fixture
def mock_input_queue():
    """Fixture for a mock TaskQueue."""
    queue = Mock(name="input_queue")
    queue.register_processor = Mock()
    return queue


@pytest.fixture
def mock_tracer():
    """Fixture for a mock OpenTelemetry tracer that yields fake spans."""
    tracer = Mock(name="tracer")
    span = Mock(name="span")
    span.__enter__ = Mock(return_value=span)
    span.__exit__ = Mock(return_value=None)
    tracer.start_as_current_span.return_value = span
    return tracer, span


@pytest.fixture
def mock_meter():
    """Fixture for a mock OpenTelemetry meter that yields fake counters."""
    meter = Mock(name="meter")
    counter = Mock(name="counter")
    meter.create_counter.return_value = counter
    return meter, counter


@pytest.fixture
def processor(mock_input_queue, mock_tracer, mock_meter, monkeypatch):
    """
    Create a TaskProcessor with mocked dependencies that persist
    for the whole test (using monkeypatch so patches are not torn down early).
    """
    tracer, span = mock_tracer
    meter, counter = mock_meter

    import scl.processor.task_processor as mod
    monkeypatch.setattr(mod, "tracer", tracer)
    monkeypatch.setattr(mod, "meter", meter)
    monkeypatch.setattr(mod.trace, "get_current_span", lambda: span)

    tp = TaskProcessor(mock_input_queue, name="test_processor")
    return tp, mock_input_queue, tracer, span, meter, counter


class TestTaskProcessorInit:
    def test_initialization_registers_with_queue(self, processor):
        tp, input_queue, *_ = processor
        input_queue.register_processor.assert_called_once_with(tp)

    def test_default_name_sets_logger(self, mock_input_queue, mock_tracer, mock_meter, monkeypatch):
        tracer, span = mock_tracer
        meter, counter = mock_meter

        import scl.processor.task_processor as mod
        monkeypatch.setattr(mod, "tracer", tracer)
        monkeypatch.setattr(mod, "meter", meter)
        monkeypatch.setattr(mod.trace, "get_current_span", lambda: span)

        tp = TaskProcessor(mock_input_queue)
        assert tp.name == "task_processor"

    def test_metrics_counter_created(self, processor):
        tp, _, _, _, meter, counter = processor
        meter.create_counter.assert_called_with(
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
    @pytest.fixture
    def dummy_task(self):
        task = Mock(name="task", spec=["id", "type"])
        task.id = 42
        task.type = "test_type"
        return task

    def test_process_item_success(self, processor, dummy_task):
        tp, _, tracer, span, meter, counter = processor

        with patch("time.sleep", return_value=None) as mock_sleep:
            tp._process_item(dummy_task)

        tracer.start_as_current_span.assert_called_with("TaskProcessor._process_item")
        assert span.set_attribute.call_count == 2
        span.set_attribute.assert_any_call("task.id", "42")
        span.set_attribute.assert_any_call("task.type", "test_type")
        mock_sleep.assert_called_once_with(0.1)
        counter.add.assert_not_called()

    def test_process_item_failure(self, processor, dummy_task):
        tp, _, tracer, span, meter, counter = processor

        with patch("time.sleep", side_effect=Exception("processing failure")):
            with pytest.raises(Exception):
                tp._process_item(dummy_task)

        span.set_attribute.assert_any_call("task.id", "42")
        span.record_exception.assert_called_once()
        counter.add.assert_called_once_with(1, {"processor.name": "test_processor"})

    def test_process_item_unknown_id_and_type(self, processor):
        tp, _, tracer, span, meter, counter = processor
        task = Mock(spec=[])

        with patch("time.sleep", return_value=None):
            tp._process_item(task)

        span.set_attribute.assert_any_call("task.id", "unknown")
        span.set_attribute.assert_any_call("task.type", "unknown")


class TestNotify:
    def test_notify_calls_super_and_sets_span_attributes(self, processor):
        tp, _, tracer, span, *_ = processor

        # Provide a long enough list of status values so that all accesses
        # (including those inside the base class notify) succeed.
        # The first two calls are the ones we care about.
        with patch.object(type(tp), "status", new_callable=PropertyMock) as mock_status:
            mock_status.side_effect = ["idle", "running", "running", "running", "running"]
            tp.notify()

        tracer.start_as_current_span.assert_called_with("TaskProcessor.notify")
        span.set_attribute.assert_any_call("processor.status_before", "idle")
        span.set_attribute.assert_any_call("processor.status_after", "running")

    def test_notify_propagates_to_base_class(self, processor):
        tp, _, *_ = processor
        with patch.object(tp.__class__.__bases__[0], "notify",
                          autospec=True) as super_notify:
            tp.notify()
            super_notify.assert_called_once_with(tp)