"""
Tests for InternalWatcher (updated file‑based implementation)
"""

import os
import json
import pytest
from unittest.mock import Mock, patch, MagicMock, mock_open, call
from opentelemetry import trace
import logging

from scl.listener.Interal_watch import InternalWatcher
from scl.meta.task import Task


class TestInternalWatcher:
    """Test suite for InternalWatcher class (file writing version)."""

    @pytest.fixture
    def watch_path(self, tmp_path):
        """Provide a temporary directory as the watch path."""
        return str(tmp_path / "watch")

    @pytest.fixture
    def mock_task(self):
        """Fixture providing a mock Task with hash, id, type and to_dict method."""
        task = Mock(spec=Task)
        task.hash = "abc123hash"
        task.id = "task-456"
        task.type = "test-type"
        task.to_dict.return_value = {
            "hash": "abc123hash",
            "id": "task-456",
            "type": "test-type",
            "data": "sample"
        }
        # Make to_dict return the actual dict when called
        task.to_dict = Mock(return_value={
            "hash": "abc123hash",
            "id": "task-456",
            "type": "test-type",
            "data": "sample"
        })
        return task

    @pytest.fixture
    def internal_watcher(self, watch_path):
        """Fixture providing an InternalWatcher with mocked OpenTelemetry components."""
        with patch('scl.listener.Interal_watch.meter') as mock_meter, \
             patch('scl.listener.Interal_watch.tracer') as mock_tracer:
            mock_success_counter = Mock()
            mock_error_counter = Mock()
            # meter.create_counter called twice: first for success, second for error
            mock_meter.create_counter.side_effect = [mock_success_counter, mock_error_counter]

            watcher = InternalWatcher(watch_path)

            # Attach mocks for easier access in tests
            watcher._mock_success_counter = mock_success_counter
            watcher._mock_error_counter = mock_error_counter
            return watcher

    def test_init_creates_watch_directory_and_counters(self, watch_path):
        """Test that __init__ creates the watch directory, counters, and logs."""
        with patch('scl.listener.Interal_watch.meter') as mock_meter, \
             patch('scl.listener.Interal_watch.tracer'):

            mock_counter1 = Mock()
            mock_counter2 = Mock()
            mock_meter.create_counter.side_effect = [mock_counter1, mock_counter2]

            watcher = InternalWatcher(watch_path)

            # Directory should be created
            assert os.path.isdir(watch_path)

            # Counters created with correct names
            assert mock_meter.create_counter.call_count == 2
            mock_meter.create_counter.assert_any_call(
                "internal_task_write",
                description="Number of internal tasks written to file"
            )
            mock_meter.create_counter.assert_any_call(
                "internal_task_error",
                description="Number of errors while writing internal tasks to file"
            )
            assert watcher.internal_task_counter == mock_counter1
            assert watcher.internal_task_error_counter == mock_counter2

    def test_add_valid_task_writes_file_and_returns_hash(self, internal_watcher, mock_task, watch_path):
        """Test adding a valid Task writes the correct JSON file and returns the hash."""
        with patch('scl.listener.Interal_watch.trace.get_current_span') as mock_get_span, \
             patch('builtins.open', mock_open()) as mock_file:

            mock_span = Mock()
            mock_get_span.return_value = mock_span

            # Execute
            result_hash = internal_watcher.add(mock_task)

            # Assert return value is the task hash
            assert result_hash == "abc123hash"

            # Verify file open and write
            expected_file_path = os.path.join(watch_path, "abc123hash.json")
            mock_file.assert_called_once_with(expected_file_path, 'w', encoding='utf-8')
            handle = mock_file()
            # Verify write was called (json.dump calls write multiple times)
            assert handle.write.called
            # Check that JSON contains expected data by collecting all write calls
            written_content = ''.join(str(call[0][0]) for call in handle.write.call_args_list)
            written_json = json.loads(written_content)
            assert written_json == mock_task.to_dict.return_value

            # Span attributes
            mock_span.set_attribute.assert_any_call("task.id", "task-456")
            mock_span.set_attribute.assert_any_call("task.type", "test-type")
            mock_span.set_attribute.assert_any_call("task.hash", "abc123hash")
            mock_span.set_attribute.assert_any_call("file.path", expected_file_path)
            # No error attribute
            error_calls = [c for c in mock_span.set_attribute.call_args_list if c[0][0] == "error"]
            assert len(error_calls) == 0

            # Success counter incremented
            internal_watcher._mock_success_counter.add.assert_called_once_with(1)

    def test_add_task_missing_hash_raises_valueerror(self, internal_watcher):
        """Test that a Task without a 'hash' attribute raises ValueError."""
        task_no_hash = Mock(spec=Task)
        task_no_hash.to_dict.return_value = {}
        # Explicitly set hash to None to simulate missing hash
        type(task_no_hash).hash = property(lambda self: None)
        
        with patch('scl.listener.Interal_watch.trace.get_current_span') as mock_get_span:
            mock_span = Mock()
            mock_get_span.return_value = mock_span

            with pytest.raises(ValueError, match="Task object missing 'hash' attribute"):
                internal_watcher.add(task_no_hash)

            # Span error attributes set
            mock_span.set_attribute.assert_any_call("error", True)
            mock_span.set_attribute.assert_any_call("error.message", "Task object missing 'hash' attribute")

            # Error counter incremented
            internal_watcher._mock_error_counter.add.assert_called_once_with(1)

    def test_add_invalid_type_raises_typeerror(self, internal_watcher):
        """Test that passing a non-Task object raises TypeError."""
        not_a_task = {"foo": "bar"}

        with patch('scl.listener.Interal_watch.trace.get_current_span') as mock_get_span:
            mock_span = Mock()
            mock_get_span.return_value = mock_span

            with pytest.raises(TypeError, match="Expected Task instance, got dict"):
                internal_watcher.add(not_a_task)

            # Span error attributes set
            mock_span.set_attribute.assert_any_call("error", True)
            mock_span.set_attribute.assert_any_call("error.message", "Expected Task instance, got dict")

            # Error counter incremented
            internal_watcher._mock_error_counter.add.assert_called_once_with(1)

    def test_add_file_write_failure_logs_and_raises(self, internal_watcher, mock_task):
        """Test that if file writing fails, the exception is logged and re-raised."""
        test_error = OSError("Disk full")

        with patch('scl.listener.Interal_watch.trace.get_current_span') as mock_get_span, \
             patch('builtins.open', side_effect=test_error):
            mock_span = Mock()
            mock_get_span.return_value = mock_span

            with pytest.raises(OSError, match="Disk full"):
                internal_watcher.add(mock_task)

            # Exception recorded on span
            mock_span.record_exception.assert_called_once_with(test_error)

            # Error counter incremented
            internal_watcher._mock_error_counter.add.assert_called_once_with(1)

            # Success counter NOT called
            internal_watcher._mock_success_counter.add.assert_not_called()

    def test_add_task_with_missing_id_and_type_uses_unknown(self, internal_watcher, watch_path):
        """Test that Task without 'id' or 'type' uses 'unknown' in span attributes."""
        task = Mock(spec=Task)
        task.hash = "hash123"
        task.to_dict.return_value = {"hash": "hash123"}
        # No id or type attributes intentionally

        with patch('scl.listener.Interal_watch.trace.get_current_span') as mock_get_span, \
             patch('builtins.open', mock_open()):
            mock_span = Mock()
            mock_get_span.return_value = mock_span

            internal_watcher.add(task)

            mock_span.set_attribute.assert_any_call("task.id", "unknown")
            mock_span.set_attribute.assert_any_call("task.type", "unknown")

    def test_logging_on_success_and_error(self, watch_path, mock_task):
        """Verify appropriate log messages are emitted for success and error cases."""
        with patch('scl.listener.Interal_watch.meter'), \
             patch('scl.listener.Interal_watch.tracer'), \
             patch('scl.listener.Interal_watch.trace.get_current_span'), \
             patch('builtins.open', mock_open()), \
             patch('scl.listener.Interal_watch.logger') as mock_logger:

            watcher = InternalWatcher(watch_path)
            watcher.logger = mock_logger

            # Success
            watcher.add(mock_task)
            mock_logger.debug.assert_called_with(
                f"Internally generated task received: id={mock_task.id}, hash={mock_task.hash}, type={mock_task.type}"
            )
            mock_logger.info.assert_called_with(
                f"Internal task {mock_task.hash} written to file: {os.path.join(watch_path, mock_task.hash + '.json')}"
            )

            # Error: invalid type
            mock_logger.reset_mock()
            with pytest.raises(TypeError):
                watcher.add("invalid")
            mock_logger.error.assert_called_with("Expected Task instance, got str")

            # Error: file write failure
            mock_logger.reset_mock()
            test_error = OSError("Permission denied")
            with patch('builtins.open', side_effect=test_error):
                with pytest.raises(OSError):
                    watcher.add(mock_task)
                mock_logger.error.assert_called_with(
                    f"Failed to write internal task {mock_task.hash} to file: Permission denied",
                    exc_info=True
                )