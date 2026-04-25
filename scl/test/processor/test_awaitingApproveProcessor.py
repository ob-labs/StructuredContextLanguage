"""
Unit tests for AwaitingApproveProcessor.

Tests cover:
- Initialization: directory creation, base class init, metric counter creation.
- _get_item: empty queue, successful fetch, exception handling.
- _process_item: missing hash, unapproved requeue, approved file move,
  exception paths including requeue failure.
- _move_approved_file: success, missing file, move error.
"""

import pytest
from unittest.mock import patch, MagicMock, call
import os
import tempfile
import logging

from scl.processor.awaitingApproveProcessor import AwaitingApproveProcessor
from scl.queue.awaitingApproveQueue import AwaitingApproveQueue
from scl.meta.task import Task
from scl.meta.captask import CapTask


# ------------------------------------------------------------------ Fixtures
@pytest.fixture
def otel_mock():
    """Mock OpenTelemetry tracer, meter, and trace.get_current_span."""
    with patch('scl.processor.awaitingApproveProcessor.tracer') as mock_tracer, \
         patch('scl.processor.awaitingApproveProcessor.meter') as mock_meter, \
         patch('scl.processor.awaitingApproveProcessor.trace') as mock_trace:
        mock_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span
        mock_tracer.start_as_current_span.return_value.__exit__.return_value = None
        mock_trace.get_current_span.return_value = mock_span

        # Each call to create_counter returns a unique MagicMock
        def create_counter_side_effect(name, description=None):
            cnt = MagicMock()
            cnt.name = name  # helps in debugging
            return cnt
        mock_meter.create_counter.side_effect = create_counter_side_effect

        yield mock_tracer, mock_meter, mock_trace, mock_span


@pytest.fixture
def mock_base_init():
    """Prevent BaseQueueProcessor.__init__ from executing any real logic."""
    with patch('scl.processor.awaitingApproveProcessor.BaseQueueProcessor.__init__',
               autospec=True) as mock_init:
        def init_side_effect(self, name=None, logger_name=None):
            self.name = name or 'mock'
            self.logger = MagicMock()
        mock_init.side_effect = init_side_effect
        yield mock_init


@pytest.fixture
def processor_fixture(otel_mock, mock_base_init, tmp_path):
    """Provide a fully mocked AwaitingApproveProcessor instance."""
    mock_tracer, mock_meter, mock_trace, mock_span = otel_mock
    mock_queue = MagicMock(spec=AwaitingApproveQueue)

    waiting_dir = tmp_path / "waitingapproval"
    file_watch_dir = tmp_path / "file_watch"
    proc = AwaitingApproveProcessor(
        source_queue=mock_queue,
        waiting_approval_dir=str(waiting_dir),
        file_watch_dir=str(file_watch_dir),
        name="test_processor"
    )
    # Logger is set by the mocked base init, but ensure it's present.
    if not hasattr(proc, 'logger'):
        proc.logger = MagicMock()

    return proc, mock_queue, mock_tracer, mock_span, mock_meter, waiting_dir, file_watch_dir


# ------------------------------------------------------------------ Test class
class TestAwaitingApproveProcessor:
    """All tests for the AwaitingApproveProcessor class."""

    def test_init_creates_directories(self, processor_fixture):
        proc, _, _, _, _, waiting_dir, file_watch_dir = processor_fixture
        assert waiting_dir.exists()
        assert file_watch_dir.exists()

    def test_init_calls_base_queue_processor(self, mock_base_init, processor_fixture):
        mock_base_init.assert_called_once()
        call_kwargs = mock_base_init.call_args[1]
        assert call_kwargs.get('name') == 'test_processor'
        assert 'logger_name' in call_kwargs

    def test_init_creates_counters(self, otel_mock, processor_fixture):
        mock_meter = otel_mock[1]  # the mocked meter
        proc, _, _, _, _, _, _ = processor_fixture
        # Four counters should be created during __init__
        assert mock_meter.create_counter.call_count == 4
        counter_names = [c.args[0] for c in mock_meter.create_counter.call_args_list]
        assert 'test_processor.items_fetched_by_type' in counter_names
        assert 'test_processor.items_requeued' in counter_names
        assert 'test_processor.files_moved' in counter_names
        assert 'test_processor.file_move_errors' in counter_names

    # ------ _get_item tests ------
    def test_get_item_when_queue_empty(self, processor_fixture):
        proc, mock_queue, _, mock_span, _, _, _ = processor_fixture
        mock_queue.get.return_value = None
        item = proc._get_item()
        assert item is None
        mock_span.set_attribute.assert_any_call("item.available", False)
        proc.items_fetched_by_type_counter.add.assert_not_called()

    def test_get_item_returns_task(self, processor_fixture):
        proc, mock_queue, _, mock_span, _, _, _ = processor_fixture
        # Use a plain MagicMock without spec to avoid type='Task' expectation
        mock_item = MagicMock()
        mock_item.hash = 'abc123'
        mock_queue.get.return_value = mock_item
        item = proc._get_item()
        assert item is mock_item
        mock_span.set_attribute.assert_any_call("item.available", True)
        expected_type = type(mock_item).__name__
        mock_span.set_attribute.assert_any_call("item.type", expected_type)
        mock_span.set_attribute.assert_any_call("item.hash", 'abc123')
        proc.items_fetched_by_type_counter.add.assert_called_once_with(
            1, {"processor.name": "test_processor", "item_type": expected_type}
        )

    def test_get_item_exception_returns_none(self, processor_fixture):
        proc, mock_queue, _, mock_span, _, _, _ = processor_fixture
        mock_queue.get.side_effect = Exception("Queue failure")
        item = proc._get_item()
        assert item is None
        mock_span.record_exception.assert_called_once()
        proc.logger.error.assert_called()

    # ------ _process_item tests ------
    def test_process_item_missing_hash_logs_error(self, processor_fixture):
        proc, mock_queue, _, mock_span, _, _, _ = processor_fixture
        item = MagicMock()  # no 'hash' attribute
        proc._process_item(item)
        proc.logger.error.assert_called()
        mock_span.set_status.assert_called_once()
        mock_queue.add.assert_not_called()

    def test_process_item_unapproved_requeues(self, processor_fixture):
        proc, mock_queue, _, mock_span, _, _, _ = processor_fixture
        item = MagicMock(hash='u1', approval=False)
        proc._process_item(item)
        mock_queue.add.assert_called_once_with(item)
        proc.items_requeued_counter.add.assert_called_once_with(
            1, {"processor.name": "test_processor", "item_type": type(item).__name__}
        )
        mock_span.set_attribute.assert_any_call("item.routed_to", "source_queue")

    def test_process_item_approved_calls_move_file(self, processor_fixture):
        proc, mock_queue, _, mock_span, _, _, _ = processor_fixture
        item = MagicMock(hash='a1', approval=True)
        with patch.object(proc, '_move_approved_file') as mock_move:
            proc._process_item(item)
            mock_move.assert_called_once_with('a1', type(item).__name__, mock_span)
        mock_queue.add.assert_not_called()
        mock_span.set_attribute.assert_any_call("item.routed_to", "file_watch_dir")

    def test_process_item_exception_requeues_and_logs(self, processor_fixture):
        proc, mock_queue, _, mock_span, _, _, _ = processor_fixture
        item = MagicMock(hash='e1', approval=True)
        with patch.object(proc, '_move_approved_file', side_effect=RuntimeError("move fail")):
            proc._process_item(item)
        mock_queue.add.assert_called_with(item)
        proc.logger.error.assert_called()
        mock_span.record_exception.assert_called()
        mock_span.set_status.assert_called_once()

    def test_process_item_requeue_failure_logs_critical(self, processor_fixture):
        proc, mock_queue, _, mock_span, _, _, _ = processor_fixture
        item = MagicMock(hash='e2', approval=True)
        with patch.object(proc, '_move_approved_file', side_effect=RuntimeError("move fail")):
            mock_queue.add.side_effect = Exception("add fail")
            proc._process_item(item)
        proc.logger.critical.assert_called()

    # ------ _move_approved_file tests ------
    def test_move_approved_file_success(self, processor_fixture):
        proc, _, _, mock_span, _, waiting_dir, file_watch_dir = processor_fixture
        src = waiting_dir / "hash1.json"
        src.write_text("payload")
        dst = file_watch_dir / "hash1.json"

        with patch('scl.processor.awaitingApproveProcessor.shutil.move') as mock_move:
            proc._move_approved_file("hash1", "Task", mock_span)

        mock_move.assert_called_once_with(str(src), str(dst))
        proc.files_moved_counter.add.assert_called_once_with(
            1, {"processor.name": "test_processor", "item_type": "Task"}
        )
        mock_span.set_attribute.assert_any_call("file.moved", True)

    def test_move_approved_file_not_found(self, processor_fixture):
        proc, _, _, mock_span, _, waiting_dir, _ = processor_fixture
        # Do not create the file
        with patch('scl.processor.awaitingApproveProcessor.shutil.move') as mock_move:
            proc._move_approved_file("nohash", "CapTask", mock_span)

        proc.logger.error.assert_called()
        proc.file_move_errors_counter.add.assert_called_once_with(
            1, {"processor.name": "test_processor", "error": "file_not_found"}
        )
        mock_span.set_status.assert_called_once()
        mock_move.assert_not_called()

    def test_move_approved_file_move_exception(self, processor_fixture):
        proc, _, _, mock_span, _, waiting_dir, _ = processor_fixture
        src = waiting_dir / "errhash.json"
        src.write_text("data")

        with patch('scl.processor.awaitingApproveProcessor.shutil.move',
                   side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                proc._move_approved_file("errhash", "Task", mock_span)

        proc.file_move_errors_counter.add.assert_called_once_with(
            1, {"processor.name": "test_processor", "error": "move_failed"}
        )
        mock_span.record_exception.assert_called()