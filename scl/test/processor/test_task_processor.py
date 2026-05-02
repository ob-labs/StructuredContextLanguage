"""
Tests for scl.processor.task_processor.TaskProcessor

Uses pytest and unittest.mock to verify:
- Initialization and queue registration
- Non-blocking item retrieval
- Task processing with tracing, logging and error metrics
- Notification tracing override
"""
import logging
import queue
from unittest.mock import ANY, MagicMock, PropertyMock, call, patch

import pytest

from scl.meta.task import Task
from scl.processor.task_processor import TaskProcessor


# ---------- Fixtures ----------
@pytest.fixture
def mock_input_queue():
    """Mock of TaskQueue."""
    q = MagicMock()
    q.get = MagicMock()
    q.register_processor = MagicMock()
    return q


@pytest.fixture
def mock_task():
    """A dummy Task with id and type attributes."""
    t = MagicMock(spec=Task)
    t.id = "task-123"
    t.type = "test_type"
    return t


@pytest.fixture
def mock_tracer():
    """Patch the tracer used in the module under test."""
    with patch("scl.processor.task_processor.tracer") as tr:
        # Allow use as context manager
        tr.start_as_current_span.return_value.__enter__.return_value = MagicMock()
        tr.start_as_current_span.return_value.__exit__.return_value = None
        yield tr


@pytest.fixture
def mock_meter():
    """Patch the meter used in the module under test."""
    with patch("scl.processor.task_processor.meter") as mt:
        mt.create_counter.return_value = MagicMock()
        yield mt


@pytest.fixture
def processor(mock_input_queue, mock_tracer, mock_meter):
    """Create a TaskProcessor with mocked dependencies."""
    proc = TaskProcessor(input_queue=mock_input_queue, name="test_proc")
    return proc


# ---------- Tests: Initialization ----------
def test_init_registers_with_queue(mock_input_queue, mock_tracer, mock_meter):
    """Should call register_processor on the input queue."""
    processor = TaskProcessor(input_queue=mock_input_queue, name="worker")
    mock_input_queue.register_processor.assert_called_once_with(processor)
    # Verify that the name is propagated correctly (if BaseQueueProcessor stores it)
    assert processor.name == "worker"


def test_init_creates_error_counter(mock_input_queue, mock_tracer, mock_meter):
    """Should create a counter metric for processing errors."""
    processor = TaskProcessor(input_queue=mock_input_queue, name="myproc")
    mock_meter.create_counter.assert_called_once_with(
        "myproc.processing_errors",
        description="Number of errors while processing individual tasks"
    )


def test_init_logs_info_message(mock_input_queue, mock_tracer, mock_meter, caplog):
    """Should log an info message after initialisation."""
    with caplog.at_level(logging.INFO):
        TaskProcessor(input_queue=mock_input_queue, name="proc")
    assert "TaskProcessor initialized and registered with queue" in caplog.text


# ---------- Tests: _get_item ----------
def test_get_item_returns_task_non_blocking(processor, mock_input_queue, mock_task):
    """_get_item calls queue.get(block=False) and returns the item."""
    mock_input_queue.get.return_value = mock_task
    result = processor._get_item()
    mock_input_queue.get.assert_called_once_with(block=False)
    assert result is mock_task


def test_get_item_returns_none_on_queue_empty(processor, mock_input_queue):
    """If queue.get raises queue.Empty, _get_item should catch and return None."""
    mock_input_queue.get.side_effect = queue.Empty
    result = processor._get_item()
    assert result is None


def test_get_item_returns_none_on_any_exception(processor, mock_input_queue):
    """Any other exception from queue.get should be caught and None returned."""
    mock_input_queue.get.side_effect = RuntimeError("down")
    result = processor._get_item()
    assert result is None


# ---------- Tests: _process_item ----------
def test_process_item_sets_span_attributes(processor, mock_task, mock_tracer):
    """Should set task.id and task.type on the current span."""
    mock_span = MagicMock()
    with patch("scl.processor.task_processor.trace.get_current_span", return_value=mock_span):
        processor._process_item(mock_task)

    mock_span.set_attribute.assert_has_calls([
        call("task.id", "task-123"),
        call("task.type", "test_type"),
    ], any_order=True)


def test_process_item_logs_info_and_debug_on_success(processor, mock_task, caplog):
    """Successful processing logs info start and debug finish."""
    with patch("scl.processor.task_processor.trace.get_current_span", return_value=MagicMock()):
        with caplog.at_level(logging.DEBUG):
            processor._process_item(mock_task)

    assert "Processing Task: id=task-123, type=test_type" in caplog.text
    assert "task-123 processed successfully" in caplog.text


def test_process_item_sleeps(processor, mock_task):
    """Should call time.sleep(0.1) to simulate work."""
    with patch("scl.processor.task_processor.trace.get_current_span", return_value=MagicMock()):
        with patch("time.sleep") as mock_sleep:  # Patch built-in time.sleep
            processor._process_item(mock_task)
            mock_sleep.assert_called_once_with(0.1)


def test_process_item_on_error_logs_and_records_exception(processor, mock_task, caplog):
    """Exception inside processing should log error, record exception, increment counter, and re-raise."""
    mock_span = MagicMock()
    with patch("scl.processor.task_processor.trace.get_current_span", return_value=mock_span):
        # Make the processing block raise an error inside time.sleep
        with patch("time.sleep", side_effect=ValueError("boom")):
            with pytest.raises(ValueError, match="boom"):
                processor._process_item(mock_task)

    # Error log
    assert "Error processing task task-123: boom" in caplog.text
    # Record exception
    mock_span.record_exception.assert_called_once()
    exc_arg = mock_span.record_exception.call_args[0][0]
    assert isinstance(exc_arg, ValueError)
    assert str(exc_arg) == "boom"
    # Increment error counter
    processor.processing_error_counter.add.assert_called_once_with(
        1, {"processor.name": "test_proc"}
    )


# ---------- Tests: notify override ----------
def test_notify_opens_span_and_delegates(processor, mock_tracer):
    """notify() should create a span, set status attributes, and call super().notify()."""
    # Patch BaseQueueProcessor.notify to avoid real logic
    with patch.object(processor.__class__.__bases__[0], "notify") as mock_super_notify:
        # status is a read-only property in BaseQueueProcessor; mock its getter
        with patch.object(type(processor), "status", new_callable=PropertyMock) as mock_status:
            mock_status.return_value = "idle"
            processor.notify()

            # Verify tracer created a span for notify
            mock_tracer.start_as_current_span.assert_called_with("TaskProcessor.notify")
            span_instance = mock_tracer.start_as_current_span.return_value.__enter__.return_value
            span_instance.set_attribute.assert_has_calls([
                call("processor.status_before", "idle"),
                call("processor.status_after", "idle"),
            ])
            # super().notify() must be invoked
            mock_super_notify.assert_called_once()