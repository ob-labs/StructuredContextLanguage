"""
Tests for InternalWatcher
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from opentelemetry import trace
import logging

from scl.listener.Interal_watch import InternalWatcher
from scl.meta.taskQueue import TaskQueue
from scl.meta.task import Task


class TestInternalWatcher:
    """Test suite for InternalWatcher class."""

    @pytest.fixture
    def mock_task_queue(self):
        """Fixture providing a mocked TaskQueue."""
        return Mock(spec=TaskQueue)

    @pytest.fixture
    def mock_task(self):
        """Fixture providing a mocked Task with id and type attributes."""
        task = Mock(spec=Task)
        task.id = "task-123"
        task.type = "test-task"
        return task

    @pytest.fixture
    def internal_watcher(self, mock_task_queue):
        """Fixture providing an InternalWatcher instance with mocked dependencies."""
        # Mock meter and tracer to avoid actual OpenTelemetry calls
        with patch('scl.listener.Interal_watch.meter') as mock_meter, \
             patch('scl.listener.Interal_watch.tracer') as mock_tracer:
            # Create separate counter mocks for success and error
            mock_success_counter = Mock()
            mock_error_counter = Mock()
            mock_meter.create_counter.side_effect = [mock_success_counter, mock_error_counter]
            watcher = InternalWatcher(mock_task_queue)
            return watcher

    def test_init_creates_counters_and_logs(self, mock_task_queue):
        """Test that __init__ creates metrics counters and logs initialization."""
        with patch('scl.listener.Interal_watch.meter') as mock_meter, \
             patch('scl.listener.Interal_watch.tracer'):
            mock_counter = Mock()
            mock_meter.create_counter.return_value = mock_counter

            with patch.object(logging, 'getLogger') as mock_get_logger:
                mock_logger = Mock()
                mock_get_logger.return_value = mock_logger

                watcher = InternalWatcher(mock_task_queue)

                # Check counters created
                assert mock_meter.create_counter.call_count == 2
                mock_meter.create_counter.assert_any_call(
                    "internal_task_add",
                    description="Number of internal tasks added to the queue"
                )
                mock_meter.create_counter.assert_any_call(
                    "internal_task_error",
                    description="Number of errors while adding internal tasks"
                )
                assert watcher.internal_task_counter == mock_counter
                assert watcher.internal_task_error_counter == mock_counter

                # Check logger called
                mock_logger.info.assert_called_with("InternalWatcher initialized")

    def test_add_valid_task_success(self, internal_watcher, mock_task_queue, mock_task):
        """Test adding a valid Task succeeds and metrics are updated."""
        with patch('scl.listener.Interal_watch.trace.get_current_span') as mock_get_span:
            mock_span = Mock()
            mock_get_span.return_value = mock_span

            internal_watcher.add(mock_task)

            # Verify task added to queue
            mock_task_queue.add.assert_called_once_with(mock_task)

            # Verify span attributes set
            mock_span.set_attribute.assert_any_call("task.id", "task-123")
            mock_span.set_attribute.assert_any_call("task.type", "test-task")
            # No error attribute should be set on success
            assert not any(call for call in mock_span.set_attribute.call_args_list if call[0][0] == "error")

            # Verify counter incremented
            internal_watcher.internal_task_counter.add.assert_called_once_with(1)

    def test_add_invalid_type_raises_typeerror(self, internal_watcher, mock_task_queue):
        """Test that passing a non-Task object raises TypeError."""
        not_a_task = "just a string"

        with patch('scl.listener.Interal_watch.trace.get_current_span') as mock_get_span:
            mock_span = Mock()
            mock_get_span.return_value = mock_span

            with pytest.raises(TypeError, match="Expected Task instance, got str"):
                internal_watcher.add(not_a_task)

            # Verify span error attributes
            mock_span.set_attribute.assert_any_call("error", True)
            mock_span.set_attribute.assert_any_call("error.message", "Expected Task instance, got str")

            # Verify error counter incremented
            internal_watcher.internal_task_error_counter.add.assert_called_once_with(1)

            # Verify queue.add was never called
            mock_task_queue.add.assert_not_called()

    def test_add_queue_add_raises_exception(self, internal_watcher, mock_task_queue, mock_task):
        """Test that when queue.add raises an exception, it's logged and re-raised."""
        test_exception = RuntimeError("Queue is full")
        mock_task_queue.add.side_effect = test_exception

        with patch('scl.listener.Interal_watch.trace.get_current_span') as mock_get_span:
            mock_span = Mock()
            mock_get_span.return_value = mock_span

            with pytest.raises(RuntimeError, match="Queue is full"):
                internal_watcher.add(mock_task)

            # Verify error logged and span recorded
            mock_span.record_exception.assert_called_once_with(test_exception)
            internal_watcher.internal_task_error_counter.add.assert_called_once_with(1)

            # Success counter should not be called
            internal_watcher.internal_task_counter.add.assert_not_called()

    def test_add_task_without_id_type_handles_gracefully(self, internal_watcher, mock_task_queue):
        """Test adding a Task without 'id' or 'type' attributes uses fallback values."""
        task_no_attrs = Mock(spec=Task)
        # Remove id and type from mock
        del task_no_attrs.id
        del task_no_attrs.type

        with patch('scl.listener.Interal_watch.trace.get_current_span') as mock_get_span:
            mock_span = Mock()
            mock_get_span.return_value = mock_span

            internal_watcher.add(task_no_attrs)

            mock_span.set_attribute.assert_any_call("task.id", "unknown")
            mock_span.set_attribute.assert_any_call("task.type", "unknown")
            mock_task_queue.add.assert_called_once_with(task_no_attrs)

    @patch('scl.listener.Interal_watch.logger')
    def test_logging_messages_on_success_and_error(self, mock_logger, mock_task_queue, mock_task):
        """Verify appropriate log messages are emitted."""
        with patch('scl.listener.Interal_watch.meter'), \
             patch('scl.listener.Interal_watch.tracer'), \
             patch('scl.listener.Interal_watch.trace.get_current_span'):
            watcher = InternalWatcher(mock_task_queue)
            # Replace logger with mock for this test
            watcher.logger = mock_logger

            # Success case
            watcher.add(mock_task)
            mock_logger.debug.assert_called_with(f"Internally generated task received: id={mock_task.id}, type={mock_task.type}")
            mock_logger.info.assert_called_with(f"Internal task {mock_task.id} added to queue successfully")

            # Error case - invalid type
            mock_logger.reset_mock()
            with pytest.raises(TypeError):
                watcher.add("invalid")
            mock_logger.error.assert_called_with("Expected Task instance, got str")

            # Error case - queue exception
            mock_logger.reset_mock()
            mock_task_queue.add.side_effect = RuntimeError("Test error")
            with pytest.raises(RuntimeError):
                watcher.add(mock_task)
            mock_logger.error.assert_called_with(
                f"Failed to add internal task {mock_task.id} to queue: Test error",
                exc_info=True
            )
