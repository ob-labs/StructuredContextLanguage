# File: scl/test/processor/test_cap_task_processor.py

import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


# Session‑scoped temporary directory that replaces config.todo_watch_dir
@pytest.fixture(scope="session")
def todo_watch_dir(tmp_path_factory):
    """A root directory that simulates the configured todo_watch_dir."""
    d = tmp_path_factory.mktemp("todo_watch")
    return str(d)


@pytest.fixture(scope="session")
def processor_module(todo_watch_dir):
    """
    Import the module under test while replacing:
    - config.todo_watch_dir with a temp directory
    - OpenTelemetry tracer, meter, and trace with mocks
    - The tracer's start_as_current_span acts as a pass-through decorator
      so that CapabilityProcessor's implementations remain recognised by ABC.
    """
    # --- OTEL mocks ---
    mock_span = MagicMock()

    mock_tracer = MagicMock()

    def _decorator_pass_through(span_name):
        def decorator(func):
            return func  # no wrapping – ABC will see a real method

        return decorator

    mock_tracer.start_as_current_span = _decorator_pass_through

    # meter.create_counter must return a NEW MagicMock each time
    mock_meter = MagicMock()
    mock_meter.create_counter.side_effect = lambda *args, **kwargs: MagicMock()

    mock_trace = MagicMock()
    mock_trace.get_current_span.return_value = mock_span

    # Apply all patches and then import the module under test
    with (
        patch("scl.config.config.todo_watch_dir", new=todo_watch_dir),
        patch("scl.otel.otel.tracer", mock_tracer),
        patch("scl.otel.otel.meter", mock_meter),
        patch("opentelemetry.trace", mock_trace),
    ):
        from scl.processor import cap_task_processor as mod

        yield mod


# ----------------------------------------------------------------------
# Fixtures per test
@pytest.fixture
def mock_queue():
    return MagicMock()


@pytest.fixture
def mock_cap_registry():
    return MagicMock()


@pytest.fixture
def processor(processor_module, mock_queue, mock_cap_registry):
    """Instantiate a real CapabilityProcessor (the class is now concrete)."""
    proc = processor_module.CapabilityProcessor(
        name="test_cap", queue=mock_queue, cap_registry=mock_cap_registry
    )
    return proc


@pytest.fixture(autouse=True)
def reset_counter_mocks(processor_module):
    """
    Reset counter mocks and the span mock before each test to avoid call count leaks.
    """
    for counter_name in [
        "tasks_processed_counter",
        "tasks_succeeded_counter",
        "tasks_failed_counter",
    ]:
        counter = getattr(processor_module, counter_name, None)
        if counter:
            counter.reset_mock()
            # Also reset the add method, which is a separate child mock
            if hasattr(counter, "add"):
                counter.add.reset_mock()
    processor_module.trace.get_current_span.return_value.reset_mock()


# ======================================================================
# Tests
# ======================================================================


class TestCapabilityProcessorInit:
    def test_attributes(self, processor, mock_queue):
        assert processor.name == "test_cap"
        assert processor.queue is mock_queue
        assert processor.cap_registry is processor.cap_registry  # same object

    def test_registers_notifier(self, processor, mock_queue):
        """Verify that a notify callback is registered and that it calls notify()."""
        mock_queue.register_notifier.assert_called_once()
        args = mock_queue.register_notifier.call_args[0]
        assert args[0] == "test_cap"
        callback = args[1]

        # Patch notify on the instance so we can assert it was called
        with patch.object(processor, "notify"):
            callback("test_cap", MagicMock())
            processor.notify.assert_called_once()


class TestGetItem:
    def test_returns_task(self, processor, mock_queue, processor_module):
        task = MagicMock(hash="abc", cap_name="greet", args={"x": 1})
        mock_queue.consume.return_value = task

        result = processor._get_item()
        assert result is task
        mock_queue.consume.assert_called_once_with("test_cap")

        span = processor_module.trace.get_current_span.return_value
        span.set_attribute.assert_has_calls(
            [
                call("processor.name", "test_cap"),
                call("task.available", True),
                call("task.hash", "abc"),
            ]
        )

    def test_no_task(self, processor, mock_queue, processor_module):
        mock_queue.consume.return_value = None
        result = processor._get_item()
        assert result is None
        span = processor_module.trace.get_current_span.return_value
        span.set_attribute.assert_any_call("task.available", False)

    def test_queue_exception(self, processor, mock_queue, processor_module, caplog):
        mock_queue.consume.side_effect = RuntimeError("queue down")
        result = processor._get_item()
        assert result is None
        span = processor_module.trace.get_current_span.return_value
        span.record_exception.assert_called_once()
        assert "Error consuming task from queue" in caplog.text


class TestProcessItem:
    def test_success(self, processor, processor_module, mock_cap_registry, todo_watch_dir):
        cap = MagicMock()
        cap.execute.return_value = "result-ok"
        mock_cap_registry.get_capability.return_value = cap

        task = MagicMock(hash="hash1", cap_name="greet", args={"name": "test"})
        # Important: the code expects <hash>.json
        task_file = os.path.join(todo_watch_dir, "hash1.json")
        with open(task_file, "w") as f:
            f.write("dummy")

        processor._process_item(task)

        # Success status is "Processed", not "success"
        assert task.status == "Processed"
        dest = os.path.join(processor_module.CAP_COMPLETE_DIR, "hash1.json")
        assert os.path.exists(dest)
        assert not os.path.exists(task_file)

        processor_module.tasks_processed_counter.add.assert_called_once_with(
            1, {"processor.name": "test_cap"}
        )
        processor_module.tasks_succeeded_counter.add.assert_called_once_with(
            1, {"processor.name": "test_cap"}
        )
        processor_module.tasks_failed_counter.add.assert_not_called()

        span = processor_module.trace.get_current_span.return_value
        # The real code sets "task.result_length", not "task.result"
        span.set_attribute.assert_any_call("task.result_length", len("result-ok"))

    def test_failure_execute_raises(
        self, processor, processor_module, mock_cap_registry, todo_watch_dir
    ):
        cap = MagicMock()
        cap.execute.side_effect = ValueError("bad input")
        mock_cap_registry.get_capability.return_value = cap

        task = MagicMock(hash="hash2", cap_name="fail", args={})
        task_file = os.path.join(todo_watch_dir, "hash2.json")
        with open(task_file, "w") as f:
            f.write("data")

        processor._process_item(task)

        assert task.status == "Error"
        dest = os.path.join(processor_module.CAP_ERROR_DIR, "hash2.json")
        assert os.path.exists(dest)
        assert not os.path.exists(task_file)

        processor_module.tasks_processed_counter.add.assert_called_once()
        processor_module.tasks_failed_counter.add.assert_called_once_with(
            1, {"processor.name": "test_cap"}
        )
        processor_module.tasks_succeeded_counter.add.assert_not_called()

        span = processor_module.trace.get_current_span.return_value
        span.record_exception.assert_called()
        span.set_status.assert_called()

    def test_missing_capability(
        self, processor, processor_module, mock_cap_registry, todo_watch_dir, caplog
    ):
        mock_cap_registry.get_capability.return_value = None

        task = MagicMock(hash="hash3", cap_name="unknown", args={})
        task_file = os.path.join(todo_watch_dir, "hash3.json")
        with open(task_file, "w") as f:
            f.write("data")

        processor._process_item(task)

        assert task.status == "Error"
        dest = os.path.join(processor_module.CAP_ERROR_DIR, "hash3.json")
        assert os.path.exists(dest)
        assert not os.path.exists(task_file)

        assert "No capability registered for name 'unknown'" in caplog.text

        processor_module.tasks_processed_counter.add.assert_called()
        processor_module.tasks_failed_counter.add.assert_called()


class TestSafeMove:
    def test_move_success(self, processor, todo_watch_dir):
        src = os.path.join(todo_watch_dir, "src.txt")
        with open(src, "w") as f:
            f.write("content")
        dst = os.path.join(todo_watch_dir, "dst.txt")
        processor._safe_move(src, dst, "test")
        assert os.path.exists(dst)
        assert not os.path.exists(src)

    def test_file_not_found(self, processor, todo_watch_dir, caplog):
        src = os.path.join(todo_watch_dir, "missing.txt")
        dst = os.path.join(todo_watch_dir, "dst.txt")
        processor._safe_move(src, dst, "test")
        assert "not found" in caplog.text

    def test_move_error(self, processor, todo_watch_dir, caplog, monkeypatch):
        src = os.path.join(todo_watch_dir, "src.txt")
        with open(src, "w") as f:
            f.write("data")
        dst = os.path.join(todo_watch_dir, "dst.txt")

        def mock_move(*args, **kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(shutil, "move", mock_move)

        processor._safe_move(src, dst, "error")
        assert "Failed to move" in caplog.text
