"""
Tests for InternalWatcher (updated file‑based implementation)
"""

import json
import logging
import os
from unittest.mock import MagicMock, Mock, call, mock_open, patch

import pytest
from opentelemetry import trace

from scl.listener.internal_watch import InternalWatcher
from scl.meta.captask import CapTask
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
            "data": "sample",
        }
        # Make to_dict return the actual dict when called
        task.to_dict = Mock(
            return_value={
                "hash": "abc123hash",
                "id": "task-456",
                "type": "test-type",
                "data": "sample",
            }
        )
        return task

    @pytest.fixture
    def internal_watcher(self, watch_path):
        """Fixture providing an InternalWatcher with mocked OpenTelemetry components."""
        with (
            patch("scl.listener.internal_watch.meter") as mock_meter,
            patch("scl.listener.internal_watch.tracer") as mock_tracer,
        ):
            mock_success_counter = Mock()
            mock_error_counter = Mock()
            mock_captask_success_counter = Mock()
            mock_captask_error_counter = Mock()
            # meter.create_counter called 4 times: Task success/error, CapTask success/error
            mock_meter.create_counter.side_effect = [
                mock_success_counter,
                mock_error_counter,
                mock_captask_success_counter,
                mock_captask_error_counter,
            ]

            watcher = InternalWatcher(watch_path)

            # Attach mocks for easier access in tests
            watcher._mock_success_counter = mock_success_counter
            watcher._mock_error_counter = mock_error_counter
            watcher._mock_captask_success_counter = mock_captask_success_counter
            watcher._mock_captask_error_counter = mock_captask_error_counter
            return watcher

    def test_init_creates_watch_directory_and_counters(self, watch_path):
        """Test that __init__ creates the watch directory, counters, and logs."""
        with (
            patch("scl.listener.internal_watch.meter") as mock_meter,
            patch("scl.listener.internal_watch.tracer"),
        ):
            mock_counter1 = Mock()
            mock_counter2 = Mock()
            mock_counter3 = Mock()
            mock_counter4 = Mock()
            mock_meter.create_counter.side_effect = [
                mock_counter1,
                mock_counter2,
                mock_counter3,
                mock_counter4,
            ]

            watcher = InternalWatcher(watch_path)

            # Directory should be created
            assert os.path.isdir(watch_path)

            # Counters created with correct names
            assert mock_meter.create_counter.call_count == 4
            mock_meter.create_counter.assert_any_call(
                "internal_task_write",
                description="Number of internal Task instances written to file",
            )
            mock_meter.create_counter.assert_any_call(
                "internal_task_error",
                description="Number of errors while writing internal Task instances to file",
            )
            mock_meter.create_counter.assert_any_call(
                "internal_captask_write",
                description="Number of internal CapTask instances written to file",
            )
            mock_meter.create_counter.assert_any_call(
                "internal_captask_error",
                description="Number of errors while writing internal CapTask instances to file",
            )
            assert watcher.internal_task_counter == mock_counter1
            assert watcher.internal_task_error_counter == mock_counter2
            assert watcher.internal_captask_counter == mock_counter3
            assert watcher.internal_captask_error_counter == mock_counter4

    def test_init_invalid_format_raises_valueerror(self, watch_path):
        """Test that initialising with an unsupported format raises ValueError."""
        with pytest.raises(ValueError, match="output_format must be 'json' or 'yaml', got 'xml'"):
            InternalWatcher(watch_path, output_format="xml")

    def test_add_valid_task_writes_file_and_returns_hash(
        self, internal_watcher, mock_task, watch_path
    ):
        """Test adding a valid Task writes the correct JSON file and returns the hash."""
        with (
            patch("scl.listener.internal_watch.trace.get_current_span") as mock_get_span,
            patch("builtins.open", mock_open()) as mock_file,
        ):
            mock_span = Mock()
            mock_get_span.return_value = mock_span

            # Execute
            result_hash = internal_watcher.add(mock_task)

            # Assert return value is the task hash
            assert result_hash == "abc123hash"

            # Verify file open and write
            expected_file_path = os.path.join(watch_path, "abc123hash.json")
            mock_file.assert_called_once_with(expected_file_path, "w", encoding="utf-8")
            handle = mock_file()
            # Verify write was called (json.dump calls write multiple times)
            assert handle.write.called
            # Check that JSON contains expected data by collecting all write calls
            written_content = "".join(str(call[0][0]) for call in handle.write.call_args_list)
            written_json = json.loads(written_content)
            assert written_json == mock_task.to_dict.return_value

            # Span attributes
            mock_span.set_attribute.assert_any_call("task.id", "task-456")
            mock_span.set_attribute.assert_any_call("task.type", "test-type")
            mock_span.set_attribute.assert_any_call("task.hash", "abc123hash")
            mock_span.set_attribute.assert_any_call("item.type", "Task")
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

        with patch("scl.listener.internal_watch.trace.get_current_span") as mock_get_span:
            mock_span = Mock()
            mock_get_span.return_value = mock_span

            with pytest.raises(ValueError, match="Task object missing 'hash' attribute"):
                internal_watcher.add(task_no_hash)

            # Span error attributes set
            mock_span.set_attribute.assert_any_call("error", True)
            mock_span.set_attribute.assert_any_call(
                "error.message", "Task object missing 'hash' attribute"
            )

            # Error counter incremented
            internal_watcher._mock_error_counter.add.assert_called_once_with(1)

    def test_add_invalid_type_raises_typeerror(self, internal_watcher):
        """Test that passing a non-Task/CapTask object raises TypeError."""
        not_a_task = {"foo": "bar"}

        with patch("scl.listener.internal_watch.trace.get_current_span") as mock_get_span:
            mock_span = Mock()
            mock_get_span.return_value = mock_span

            with pytest.raises(TypeError, match="Expected Task or CapTask instance, got dict"):
                internal_watcher.add(not_a_task)

            # Span error attributes set
            mock_span.set_attribute.assert_any_call("error", True)
            mock_span.set_attribute.assert_any_call(
                "error.message", "Expected Task or CapTask instance, got dict"
            )

    def test_add_file_write_failure_logs_and_raises(self, internal_watcher, mock_task):
        """Test that if file writing fails, the exception is logged and re-raised."""
        test_error = OSError("Disk full")

        with (
            patch("scl.listener.internal_watch.trace.get_current_span") as mock_get_span,
            patch("builtins.open", side_effect=test_error),
        ):
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

        with (
            patch("scl.listener.internal_watch.trace.get_current_span") as mock_get_span,
            patch("builtins.open", mock_open()),
        ):
            mock_span = Mock()
            mock_get_span.return_value = mock_span

            internal_watcher.add(task)

            mock_span.set_attribute.assert_any_call("task.id", "unknown")
            mock_span.set_attribute.assert_any_call("task.type", "unknown")

    # -------------------------------------------------------------------------
    # Logging assertions now match the OSError object (not its string)
    # -------------------------------------------------------------------------
    def test_logging_on_success_and_error(self, watch_path, mock_task):
        """Verify appropriate log messages are emitted for success and error cases."""
        with (
            patch("scl.listener.internal_watch.meter"),
            patch("scl.listener.internal_watch.tracer"),
            patch("scl.listener.internal_watch.trace.get_current_span"),
            patch("builtins.open", mock_open()),
            patch("scl.listener.internal_watch.logger") as mock_logger,
        ):
            watcher = InternalWatcher(watch_path)
            watcher.logger = mock_logger

            # Success
            watcher.add(mock_task)
            mock_logger.debug.assert_called_with(
                "Internally generated Task received: id=%s, hash=%s, type=%s",
                mock_task.id,
                mock_task.hash,
                mock_task.type,
            )
            expected_path = os.path.join(watch_path, mock_task.hash + ".json")
            mock_logger.info.assert_called_with(
                "Internal Task %s written to file: %s", mock_task.hash, expected_path
            )

            # Error: invalid type
            mock_logger.reset_mock()
            with pytest.raises(TypeError):
                watcher.add("invalid")
            mock_logger.error.assert_called_with("Expected Task or CapTask instance, got str")

            # Error: file write failure
            mock_logger.reset_mock()
            test_error = OSError("Permission denied")
            with patch("builtins.open", side_effect=test_error):
                with pytest.raises(OSError):
                    watcher.add(mock_task)
                mock_logger.error.assert_called_with(
                    "Failed to write internal Task %s to file: %s",
                    mock_task.hash,
                    test_error,
                    exc_info=True,
                )

    def test_add_valid_captask_writes_file_and_returns_hash(self, internal_watcher, watch_path):
        """Test adding a valid CapTask writes the correct JSON file and returns the hash."""
        mock_captask = Mock(spec=CapTask)
        mock_captask.hash = "captask789hash"
        mock_captask.cap_name = "email"
        mock_captask.to_dict.return_value = {
            "hash": "captask789hash",
            "cap_name": "email",
            "args": ["to@example.com"],
        }

        with (
            patch("scl.listener.internal_watch.trace.get_current_span") as mock_get_span,
            patch("builtins.open", mock_open()) as mock_file,
        ):
            mock_span = Mock()
            mock_get_span.return_value = mock_span

            # Execute
            result_hash = internal_watcher.add(mock_captask)

            # Assert return value is the captask hash
            assert result_hash == "captask789hash"

            # Verify file open and write
            expected_file_path = os.path.join(watch_path, "captask789hash.json")
            mock_file.assert_called_once_with(expected_file_path, "w", encoding="utf-8")
            handle = mock_file()
            # Verify write was called
            assert handle.write.called
            # Check that JSON contains expected data
            written_content = "".join(str(call[0][0]) for call in handle.write.call_args_list)
            written_json = json.loads(written_content)
            assert written_json == mock_captask.to_dict.return_value

            # Span attributes
            mock_span.set_attribute.assert_any_call("captask.cap_name", "email")
            mock_span.set_attribute.assert_any_call("captask.hash", "captask789hash")
            mock_span.set_attribute.assert_any_call("item.type", "CapTask")
            mock_span.set_attribute.assert_any_call("file.path", expected_file_path)
            # No error attribute
            error_calls = [c for c in mock_span.set_attribute.call_args_list if c[0][0] == "error"]
            assert len(error_calls) == 0

            # CapTask success counter incremented
            internal_watcher._mock_captask_success_counter.add.assert_called_once_with(1)

    def test_add_captask_missing_hash_raises_valueerror(self, internal_watcher):
        """Test that a CapTask without a 'hash' attribute raises ValueError."""
        captask_no_hash = Mock(spec=CapTask)
        captask_no_hash.to_dict.return_value = {}
        # Explicitly set hash to None to simulate missing hash
        type(captask_no_hash).hash = property(lambda self: None)

        with patch("scl.listener.internal_watch.trace.get_current_span") as mock_get_span:
            mock_span = Mock()
            mock_get_span.return_value = mock_span

            with pytest.raises(ValueError, match="CapTask object missing 'hash' attribute"):
                internal_watcher.add(captask_no_hash)

            # Span error attributes set
            mock_span.set_attribute.assert_any_call("error", True)
            mock_span.set_attribute.assert_any_call(
                "error.message", "CapTask object missing 'hash' attribute"
            )

            # CapTask error counter incremented
            internal_watcher._mock_captask_error_counter.add.assert_called_once_with(1)

    def test_add_captask_with_missing_cap_name_uses_unknown(self, internal_watcher, watch_path):
        """Test that CapTask without 'cap_name' uses 'unknown' in span attributes."""
        captask = Mock(spec=CapTask)
        captask.hash = "captaskhash456"
        captask.to_dict.return_value = {"hash": "captaskhash456"}
        # No cap_name attribute intentionally

        with (
            patch("scl.listener.internal_watch.trace.get_current_span") as mock_get_span,
            patch("builtins.open", mock_open()),
        ):
            mock_span = Mock()
            mock_get_span.return_value = mock_span

            internal_watcher.add(captask)

            mock_span.set_attribute.assert_any_call("captask.cap_name", "unknown")

    def test_captask_file_write_failure_logs_and_raises(self, internal_watcher, watch_path):
        """Test that if CapTask file writing fails, the exception is logged and re-raised."""
        mock_captask = Mock(spec=CapTask)
        mock_captask.hash = "captask123"
        mock_captask.cap_name = "test"
        mock_captask.to_dict.return_value = {"hash": "captask123", "cap_name": "test"}

        test_error = OSError("Permission denied")

        with (
            patch("scl.listener.internal_watch.trace.get_current_span") as mock_get_span,
            patch("builtins.open", side_effect=test_error),
        ):
            mock_span = Mock()
            mock_get_span.return_value = mock_span

            with pytest.raises(OSError, match="Permission denied"):
                internal_watcher.add(mock_captask)

            # Exception recorded on span
            mock_span.record_exception.assert_called_once_with(test_error)

            # CapTask error counter incremented
            internal_watcher._mock_captask_error_counter.add.assert_called_once_with(1)

            # CapTask success counter NOT called
            internal_watcher._mock_captask_success_counter.add.assert_not_called()

    # -------------------------------------------------------------------------
    # Fixed CapTask logging assertions to use exception object
    # -------------------------------------------------------------------------
    def test_captask_logging_on_success_and_error(self, watch_path):
        """Verify appropriate log messages are emitted for CapTask success and error cases."""
        mock_captask = Mock(spec=CapTask)
        mock_captask.hash = "captask999"
        mock_captask.cap_name = "notification"
        mock_captask.to_dict.return_value = {"hash": "captask999", "cap_name": "notification"}

        with (
            patch("scl.listener.internal_watch.meter"),
            patch("scl.listener.internal_watch.tracer"),
            patch("scl.listener.internal_watch.trace.get_current_span"),
            patch("builtins.open", mock_open()),
            patch("scl.listener.internal_watch.logger") as mock_logger,
        ):
            watcher = InternalWatcher(watch_path)
            watcher.logger = mock_logger

            # Success
            watcher.add(mock_captask)
            mock_logger.debug.assert_called_with(
                "Internally generated CapTask received: cap_name=%s, hash=%s",
                mock_captask.cap_name,
                mock_captask.hash,
            )
            expected_path = os.path.join(watch_path, mock_captask.hash + ".json")
            mock_logger.info.assert_called_with(
                "Internal CapTask %s written to file: %s", mock_captask.hash, expected_path
            )

            # Error: file write failure
            mock_logger.reset_mock()
            test_error = OSError("Disk full")
            with patch("builtins.open", side_effect=test_error):
                with pytest.raises(OSError):
                    watcher.add(mock_captask)
                mock_logger.error.assert_called_with(
                    "Failed to write internal CapTask %s to file: %s",
                    mock_captask.hash,
                    test_error,
                    exc_info=True,
                )

    # -------------------------------------------------------------------------
    # Additional tests for YAML output format
    # -------------------------------------------------------------------------
    def test_add_task_yaml_format(self, watch_path, mock_task):
        """Test that a Task is written as YAML when output_format is 'yaml'."""
        import yaml

        with (
            patch("scl.listener.internal_watch.meter"),
            patch("scl.listener.internal_watch.tracer"),
            patch("scl.listener.internal_watch.trace.get_current_span"),
            patch("builtins.open", mock_open()) as mock_file,
        ):
            watcher = InternalWatcher(watch_path, output_format="yaml")
            # ensure logger stays mock-free; not checked
            watcher.logger = Mock()

            watcher.add(mock_task)

            expected_path = os.path.join(watch_path, mock_task.hash + ".yaml")
            mock_file.assert_called_once_with(expected_path, "w", encoding="utf-8")
            handle = mock_file()
            # yaml.dump should have been called; we cannot easily inspect the written data,
            # but we can verify that the file was opened with the correct name.
            # To verify content, we would need to mock yaml.dump, but we can trust that
            # the implementation uses yaml.dump internally.
            # For thoroughness, check that write was called at least once.
            assert handle.write.called

    def test_add_captask_yaml_format(self, watch_path):
        """Test that a CapTask is written as YAML when output_format is 'yaml'."""
        mock_captask = Mock(spec=CapTask)
        mock_captask.hash = "captask789"
        mock_captask.cap_name = "test"
        mock_captask.to_dict.return_value = {"hash": "captask789", "cap_name": "test"}

        with (
            patch("scl.listener.internal_watch.meter"),
            patch("scl.listener.internal_watch.tracer"),
            patch("scl.listener.internal_watch.trace.get_current_span"),
            patch("builtins.open", mock_open()) as mock_file,
        ):
            watcher = InternalWatcher(watch_path, output_format="yaml")
            watcher.logger = Mock()

            watcher.add(mock_captask)

            expected_path = os.path.join(watch_path, mock_captask.hash + ".yaml")
            mock_file.assert_called_once_with(expected_path, "w", encoding="utf-8")
            handle = mock_file()
            assert handle.write.called
