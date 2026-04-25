# scl/test/processor/test_awaitCapTasksProcessor.py

"""
Unit tests for the AwaitCapTasksProcessor class.
"""

import pytest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch, ANY
from opentelemetry import trace as trace_api
from opentelemetry.trace import StatusCode

from scl.processor.awaitCapTasksProcessor import AwaitCapTasksProcessor
from scl.queue.awaitingCapTasksQueue import AwaitingCapTasksQueue
from scl.meta.task import Task

# Import the module itself to patch trace.get_current_span precisely
from scl.processor import awaitCapTasksProcessor as processor_module


# ----------------------------------------------------------------------
# Utilities
# ----------------------------------------------------------------------

@contextmanager
def mock_get_current_span(span):
    """Patch trace.get_current_span to return a specific mock span."""
    with patch.object(processor_module.trace, 'get_current_span', return_value=span):
        yield


# ----------------------------------------------------------------------
# Autouse fixtures to avoid side effects from real code
# ----------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_os_makedirs():
    """Prevent real directory creation."""
    with patch("os.makedirs"):
        yield


@pytest.fixture(autouse=True)
def patch_module_logger():
    """Replace the module‑level logger."""
    with patch("scl.processor.awaitCapTasksProcessor.logger") as mock_logger:
        yield mock_logger


@pytest.fixture(autouse=True)
def patch_module_meter():
    """Replace the meter and provide mocks for the three custom counters."""
    with patch("scl.processor.awaitCapTasksProcessor.meter") as mock_meter:
        counter_moved = MagicMock()
        counter_errors = MagicMock()
        counter_requeued = MagicMock()
        mock_meter.create_counter.side_effect = [
            counter_moved,    # files_moved
            counter_errors,   # file_move_errors
            counter_requeued  # tasks_requeued
        ]
        with patch("scl.processor.awaitCapTasksProcessor.tracer") as mock_tracer:
            yield mock_meter, counter_moved, counter_errors, counter_requeued, mock_tracer


# ----------------------------------------------------------------------
# Basic test fixtures
# ----------------------------------------------------------------------

@pytest.fixture
def mock_queue():
    """Return a mocked AwaitingCapTasksQueue."""
    return MagicMock(spec=AwaitingCapTasksQueue)


@pytest.fixture
def mock_task():
    """Return a mocked Task with a default hash."""
    task = MagicMock(spec=Task)
    task.hash = "testhash123"
    task.cap_tasks = []
    return task


class ProcessorFactory:
    """Creates a processor and attaches the mock counters for assertions."""

    def __init__(self, meter_info):
        _, self.counter_moved, self.counter_errors, self.counter_requeued, _ = meter_info

    def create(self, source_queue, waiting_dir="/tmp/waiting", file_watch_dir="/tmp/watch", name=None):
        proc = AwaitCapTasksProcessor(
            source_queue=source_queue,
            waiting_captask_dir=waiting_dir,
            file_watch_dir=file_watch_dir,
            name=name
        )
        proc.files_moved_counter = self.counter_moved
        proc.file_move_errors_counter = self.counter_errors
        proc.tasks_requeued_counter = self.counter_requeued
        return proc


@pytest.fixture
def factory(patch_module_meter):
    """Callable that builds a processor with mock counters."""
    return ProcessorFactory(patch_module_meter).create


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------

class TestInit:
    def test_initializes_directories_and_metrics(self, factory, patch_module_logger):
        # Override the autouse patch just in this test to assert calls
        with patch("os.makedirs") as mock_makedirs:
            proc = factory(
                MagicMock(spec=AwaitingCapTasksQueue),
                "/custom/wait", "/custom/watch", name="testproc"
            )
            mock_makedirs.assert_any_call("/custom/wait", exist_ok=True)
            mock_makedirs.assert_any_call("/custom/watch", exist_ok=True)

        assert proc.name == "testproc"
        patch_module_logger.info.assert_called_with(
            "%s initialized with queue %r", "testproc", proc.source_queue
        )


class TestGetItem:
    def test_returns_task_when_available(self, factory, mock_queue, mock_task,
                                         patch_module_logger, patch_module_meter):
        mock_queue.pop.return_value = mock_task
        proc = factory(mock_queue)
        mock_tracer = patch_module_meter[4]
        mock_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span

        with mock_get_current_span(mock_span):
            result = proc._get_item()

        assert result is mock_task
        mock_span.set_attribute.assert_any_call("processor.name", proc.name)
        mock_span.set_attribute.assert_any_call("task.available", True)
        mock_span.set_attribute.assert_any_call("task.hash", mock_task.hash)
        patch_module_logger.debug.assert_called_with(
            "%s: consumed Task %s from source queue", proc.name, mock_task.hash
        )

    def test_returns_none_when_queue_empty(self, factory, mock_queue,
                                           patch_module_logger, patch_module_meter):
        mock_queue.pop.return_value = None
        proc = factory(mock_queue)
        mock_tracer = patch_module_meter[4]
        mock_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span

        with mock_get_current_span(mock_span):
            result = proc._get_item()

        assert result is None
        mock_span.set_attribute.assert_any_call("task.available", False)
        patch_module_logger.debug.assert_not_called()

    def test_handles_pop_exception(self, factory, mock_queue,
                                   patch_module_logger, patch_module_meter):
        mock_queue.pop.side_effect = Exception("queue down")
        proc = factory(mock_queue)
        mock_tracer = patch_module_meter[4]
        mock_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span

        with mock_get_current_span(mock_span):
            result = proc._get_item()

        assert result is None
        patch_module_logger.error.assert_called_with(
            "%s: error consuming task from source queue: %s",
            proc.name,
            mock_queue.pop.side_effect
        )
        mock_span.record_exception.assert_called_once_with(mock_queue.pop.side_effect)


class TestProcessItem:
    def test_all_captasks_completed_triggers_move(self, factory, mock_queue, mock_task,
                                                  patch_module_logger, patch_module_meter):
        cap1 = MagicMock(status="Processed")
        cap2 = MagicMock(status="Error")
        mock_task.cap_tasks = [cap1, cap2]
        proc = factory(mock_queue)
        mock_tracer = patch_module_meter[4]
        mock_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span

        with mock_get_current_span(mock_span), \
                patch.object(proc, "_move_completed_file") as mock_move:
            proc._process_item(mock_task)

        mock_move.assert_called_once_with(mock_task.hash, mock_span)
        mock_span.set_attribute.assert_any_call("task.completed", True)
        mock_queue.push.assert_not_called()
        proc.tasks_requeued_counter.add.assert_not_called()

    def test_not_all_captasks_completed_requeues(self, factory, mock_queue, mock_task,
                                                 patch_module_logger, patch_module_meter):
        cap1 = MagicMock(status="created")
        mock_task.cap_tasks = [cap1]
        proc = factory(mock_queue)
        mock_tracer = patch_module_meter[4]
        mock_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span

        with mock_get_current_span(mock_span):
            proc._process_item(mock_task)

        mock_queue.push.assert_called_once_with(mock_task)
        proc.tasks_requeued_counter.add.assert_called_once_with(
            1, {"processor.name": proc.name}
        )
        patch_module_logger.debug.assert_called_with(
            "%s: requeued Task %s (CapTasks not all completed)",
            proc.name, mock_task.hash
        )
        mock_span.set_attribute.assert_any_call("task.requeued", True)

    def test_all_captasks_completed_exception_pushes_back(self, factory, mock_queue, mock_task,
                                                          patch_module_logger, patch_module_meter):
        cap1 = MagicMock(status="Processed")
        mock_task.cap_tasks = [cap1]
        proc = factory(mock_queue)
        original_error = RuntimeError("check failed")

        # Force _all_captasks_completed to raise
        with patch.object(proc, "_all_captasks_completed", side_effect=original_error):
            mock_span = MagicMock()
            mock_tracer = patch_module_meter[4]
            mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span

            with mock_get_current_span(mock_span):
                proc._process_item(mock_task)

        mock_queue.push.assert_called_with(mock_task)
        patch_module_logger.error.assert_called_with(
            "%s: failed to process Task %s: %s",
            proc.name, mock_task.hash, original_error, exc_info=True
        )
        mock_span.record_exception.assert_called_once()

        # Verify set_status was called with ERROR status and correct description.
        # Because Status objects may not compare equal across different instances,
        # we inspect the actual call arguments.
        mock_span.set_status.assert_called_once()
        call_args = mock_span.set_status.call_args[0][0]  # the Status object
        assert call_args.status_code == trace_api.StatusCode.ERROR
        assert call_args.description == "Task processing failed"

    def test_push_back_after_error_fails(self, factory, mock_queue, mock_task,
                                         patch_module_logger, patch_module_meter):
        cap1 = MagicMock(status="Processed")
        mock_task.cap_tasks = [cap1]
        proc = factory(mock_queue)
        requeue_error = Exception("requeue failed")
        mock_queue.push.side_effect = requeue_error
        original_error = RuntimeError("original error")

        with patch.object(proc, "_all_captasks_completed", side_effect=original_error):
            mock_span = MagicMock()
            mock_tracer = patch_module_meter[4]
            mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span

            with mock_get_current_span(mock_span):
                proc._process_item(mock_task)

        mock_queue.push.assert_called_with(mock_task)
        patch_module_logger.critical.assert_called_with(
            "%s: failed to requeue Task %s after error: %s",
            proc.name, mock_task.hash, requeue_error
        )


class TestAllCapTasksCompleted:
    def test_all_completed_returns_true(self, factory, mock_queue):
        cap1 = MagicMock(status="Processed")
        cap2 = MagicMock(status="Error")
        task = MagicMock(spec=Task)
        task.cap_tasks = [cap1, cap2]
        proc = factory(mock_queue)
        assert proc._all_captasks_completed(task) is True

    def test_one_created_returns_false(self, factory, mock_queue):
        cap1 = MagicMock(status="Processed")
        cap2 = MagicMock(status="created")
        task = MagicMock(spec=Task)
        task.cap_tasks = [cap1, cap2]
        proc = factory(mock_queue)
        assert proc._all_captasks_completed(task) is False

    def test_empty_captasks_returns_true(self, factory, mock_queue):
        task = MagicMock(spec=Task)
        task.cap_tasks = []
        proc = factory(mock_queue)
        assert proc._all_captasks_completed(task) is True


class TestMoveCompletedFile:
    def test_successful_move(self, factory, mock_queue, patch_module_logger, patch_module_meter):
        proc = factory(mock_queue, "/custom/wait", "/custom/watch", name="mover")
        task_hash = "abc123"
        src = "/custom/wait/abc123.json"
        dst = "/custom/watch/abc123.json"
        mock_span = MagicMock()

        with patch("os.path.exists", return_value=True):
            with patch("shutil.move") as mock_shutil_move:
                proc._move_completed_file(task_hash, mock_span)

        mock_shutil_move.assert_called_once_with(src, dst)
        proc.files_moved_counter.add.assert_called_once_with(
            1, {"processor.name": "mover"}
        )
        patch_module_logger.info.assert_called_with(
            "%s: moved completed Task file %s from %s to %s",
            "mover", "abc123.json", "/custom/wait", "/custom/watch"
        )
        mock_span.set_attribute.assert_any_call("file.moved", True)

    def test_file_not_found(self, factory, mock_queue, patch_module_logger, patch_module_meter):
        proc = factory(mock_queue, "/custom/wait", "/custom/watch", name="mover")
        task_hash = "missinghash"
        src = "/custom/wait/missinghash.json"
        mock_span = MagicMock()

        with patch("os.path.exists", return_value=False):
            with patch("shutil.move") as mock_shutil_move:
                proc._move_completed_file(task_hash, mock_span)

        mock_shutil_move.assert_not_called()
        proc.file_move_errors_counter.add.assert_called_once_with(
            1, {"processor.name": "mover", "error": "file_not_found"}
        )
        # The original code uses: logger.error("%s: %s", self.name, error_msg)
        expected_error_msg = f"Expected file {src} not found for completed Task {task_hash}"
        patch_module_logger.error.assert_called_with(
            "%s: %s", "mover", expected_error_msg
        )
        # Check set_status call
        mock_span.set_status.assert_called_once()
        call_args = mock_span.set_status.call_args[0][0]
        assert call_args.status_code == trace_api.StatusCode.ERROR
        assert call_args.description == expected_error_msg

    def test_move_failure(self, factory, mock_queue, patch_module_logger, patch_module_meter):
        proc = factory(mock_queue, "/custom/wait", "/custom/watch", name="mover")
        task_hash = "failhash"
        src = "/custom/wait/failhash.json"
        move_error = OSError("Permission denied")
        mock_span = MagicMock()

        with patch("os.path.exists", return_value=True):
            with patch("shutil.move", side_effect=move_error):
                with pytest.raises(OSError):
                    proc._move_completed_file(task_hash, mock_span)

        proc.file_move_errors_counter.add.assert_called_once_with(
            1, {"processor.name": "mover", "error": "move_failed"}
        )
        mock_span.record_exception.assert_called_once_with(move_error)
        patch_module_logger.error.assert_called_with(
            "%s: failed to move file %s to %s: %s",
            "mover", src, "/custom/watch/failhash.json", move_error
        )