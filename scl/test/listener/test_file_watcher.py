"""
Tests for File Watcher module.
"""

import os
import json
import yaml
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch, call, mock_open

import pytest

# Import the module under test
from scl.listener.file_watch import FileHandler
from scl.meta.taskQueue import TaskQueue
from scl.meta.task import Task
from watchdog.events import FileCreatedEvent
from watchdog.observers import Observer


@pytest.fixture
def mock_task_queue():
    """Fixture for a mocked TaskQueue."""
    return MagicMock(spec=TaskQueue)


@pytest.fixture
def temp_watch_dir(tmp_path):
    """Fixture for a temporary watch directory."""
    watch_dir = tmp_path / "watch"
    watch_dir.mkdir()
    return str(watch_dir)


@pytest.fixture
def handler(mock_task_queue, temp_watch_dir):
    """Fixture for a FileHandler instance with mocked dependencies."""
    with patch('scl.listener.file_watch.meter') as mock_meter, \
         patch('scl.listener.file_watch.tracer') as mock_tracer:
        # Mock counter creation - create separate counters for each metric
        mock_meter.create_counter.side_effect = lambda name, description: MagicMock()

        handler = FileHandler(temp_watch_dir, mock_task_queue)
        handler.logger = MagicMock()

        # Replace actual file operations with mocks to avoid real FS interactions
        handler._move_to_failed = MagicMock()
        return handler


class TestFileHandler:
    """Test cases for FileHandler."""

    def test_init_creates_directories(self, mock_task_queue, temp_watch_dir):
        """Should create processed and failed directories on initialization."""
        processed_dir = Path(temp_watch_dir) / "processed"
        failed_dir = Path(temp_watch_dir) / "failed"

        # Ensure they don't exist yet
        assert not processed_dir.exists()
        assert not failed_dir.exists()

        with patch('scl.listener.file_watch.meter') as mock_meter, \
             patch('scl.listener.file_watch.tracer'):
            mock_meter.create_counter.return_value = MagicMock()
            FileHandler(temp_watch_dir, mock_task_queue)

        assert processed_dir.exists()
        assert failed_dir.exists()

    def test_on_created_ignores_directories(self, handler):
        """Should ignore directory creation events."""
        event = MagicMock(spec=FileCreatedEvent)
        event.is_directory = True

        handler.on_created(event)

        handler.logger.info.assert_not_called()
        handler._move_to_failed.assert_not_called()

    def test_on_created_valid_json_task(self, handler, tmp_path):
        """Should process a valid JSON task file successfully."""
        file_path = tmp_path / "task.json"
        file_path.write_text(json.dumps({"id": "123", "name": "Test Task"}))

        event = MagicMock(spec=FileCreatedEvent)
        event.is_directory = False
        event.src_path = str(file_path)

        # Mock Task.from_dict
        mock_task = MagicMock()
        mock_task.id = "123"
        with patch.object(Task, 'from_dict', return_value=mock_task):
            # Mock shutil.move to avoid actual move
            with patch('scl.listener.file_watch.shutil.move') as mock_move:
                handler.on_created(event)

        # Assertions
        handler.file_receive_counter.add.assert_called_once_with(1)
        handler.file_valid_counter.add.assert_called_once_with(1)
        handler.queue.add.assert_called_once_with(mock_task)
        mock_move.assert_called_once_with(
            str(file_path),
            os.path.join(handler.processed_dir, "task.json")
        )
        handler.logger.info.assert_any_call(f"New file detected: {file_path}")
        handler.logger.info.assert_any_call(f"File moved to processed: {os.path.join(handler.processed_dir, 'task.json')}")

    def test_on_created_valid_yaml_task(self, handler, tmp_path):
        """Should process a valid YAML task file."""
        file_path = tmp_path / "task.yaml"
        file_path.write_text(yaml.dump({"id": "456", "name": "YAML Task"}))

        event = MagicMock(spec=FileCreatedEvent)
        event.is_directory = False
        event.src_path = str(file_path)

        mock_task = MagicMock()
        mock_task.id = "456"
        with patch.object(Task, 'from_dict', return_value=mock_task):
            with patch('scl.listener.file_watch.shutil.move') as mock_move:
                handler.on_created(event)

        handler.file_valid_counter.add.assert_called_once_with(1)
        handler.queue.add.assert_called_once_with(mock_task)
        mock_move.assert_called_once_with(
            str(file_path),
            os.path.join(handler.processed_dir, "task.yaml")
        )

    def test_on_created_unsupported_extension(self, handler, tmp_path):
        """Should reject files with unsupported extensions and move to failed."""
        file_path = tmp_path / "task.txt"
        file_path.write_text("Not a task file")

        event = MagicMock(spec=FileCreatedEvent)
        event.is_directory = False
        event.src_path = str(file_path)

        handler.on_created(event)

        handler.file_invalid_counter.add.assert_called_once_with(1)
        handler._move_to_failed.assert_called_once_with(str(file_path), reason="unsupported_format")
        handler.queue.add.assert_not_called()

    def test_on_created_parse_error(self, handler, tmp_path):
        """Should handle invalid JSON/YAML content gracefully."""
        file_path = tmp_path / "invalid.json"
        file_path.write_text("{invalid json")  # Malformed

        event = MagicMock(spec=FileCreatedEvent)
        event.is_directory = False
        event.src_path = str(file_path)

        handler.on_created(event)

        handler.file_invalid_counter.add.assert_called_once_with(1)
        handler._move_to_failed.assert_called_once_with(str(file_path), reason="parse_error")
        handler.queue.add.assert_not_called()

    def test_on_created_conversion_error(self, handler, tmp_path):
        """Should handle Task.from_dict conversion failure."""
        file_path = tmp_path / "task.json"
        file_path.write_text(json.dumps({"id": "789"}))

        event = MagicMock(spec=FileCreatedEvent)
        event.is_directory = False
        event.src_path = str(file_path)

        with patch.object(Task, 'from_dict', side_effect=ValueError("Invalid task data")):
            handler.on_created(event)

        handler.task_conversion_failure_counter.add.assert_called_once_with(1)
        handler._move_to_failed.assert_called_once_with(str(file_path), reason="conversion_error")
        handler.queue.add.assert_not_called()

    def test_on_created_queue_error(self, handler, tmp_path):
        """Should move file to failed if queue.add raises exception."""
        file_path = tmp_path / "task.json"
        file_path.write_text(json.dumps({"id": "101"}))

        event = MagicMock(spec=FileCreatedEvent)
        event.is_directory = False
        event.src_path = str(file_path)

        mock_task = MagicMock()
        with patch.object(Task, 'from_dict', return_value=mock_task):
            handler.queue.add.side_effect = Exception("Queue unavailable")
            with patch('scl.listener.file_watch.shutil.move') as mock_move:
                handler.on_created(event)

        # Should not move to processed
        mock_move.assert_not_called()
        handler._move_to_failed.assert_called_once_with(str(file_path), reason="queue_error")
        # Valid counter should NOT be incremented (because queue failed)
        handler.file_valid_counter.add.assert_not_called()

    def test_parse_task_file_json(self, handler, tmp_path):
        """_parse_task_file should correctly load JSON."""
        file_path = tmp_path / "data.json"
        data = {"key": "value", "num": 42}
        file_path.write_text(json.dumps(data))

        result = handler._parse_task_file(str(file_path))
        assert result == data

    def test_parse_task_file_yaml(self, handler, tmp_path):
        """_parse_task_file should correctly load YAML."""
        file_path = tmp_path / "data.yaml"
        data = {"key": "value", "list": [1, 2, 3]}
        file_path.write_text(yaml.dump(data))

        result = handler._parse_task_file(str(file_path))
        assert result == data

    def test_parse_task_file_yml_extension(self, handler, tmp_path):
        """_parse_task_file should handle .yml extension."""
        file_path = tmp_path / "data.yml"
        data = {"a": 1}
        file_path.write_text(yaml.dump(data))

        result = handler._parse_task_file(str(file_path))
        assert result == data

    def test_is_task_format_file(self, handler):
        """_is_task_format_file should accept json, yaml, yml."""
        assert handler._is_task_format_file("task.json") is True
        assert handler._is_task_format_file("task.yaml") is True
        assert handler._is_task_format_file("task.yml") is True
        assert handler._is_task_format_file("task.txt") is False
        assert handler._is_task_format_file("task.JSON") is True  # case insensitive
        assert handler._is_task_format_file("task.YAML") is True

    def test_move_to_failed_success(self, handler, tmp_path):
        """_move_to_failed should move file with reason appended."""
        src = tmp_path / "bad.json"
        src.write_text("content")

        failed_dir = tmp_path / "failed"
        failed_dir.mkdir()
        handler.failed_dir = str(failed_dir)

        # Use actual shutil for this test (or mock it)
        handler._move_to_failed = FileHandler._move_to_failed.__get__(handler, FileHandler)
        with patch('scl.listener.file_watch.shutil.move') as mock_move:
            handler._move_to_failed(str(src), reason="parse_error")

        expected_dest = str(failed_dir / "bad.parse_error.json")
        mock_move.assert_called_once_with(str(src), expected_dest)

    def test_move_to_failed_exception_handling(self, handler, tmp_path):
        """_move_to_failed should log error if move fails."""
        src = tmp_path / "bad.json"
        src.write_text("content")

        with patch.object(handler, 'logger') as mock_logger:
            # Restore the real _move_to_failed method
            handler._move_to_failed = FileHandler._move_to_failed.__get__(handler, FileHandler)
            with patch('scl.listener.file_watch.shutil.move', side_effect=PermissionError("Access denied")):
                handler._move_to_failed(str(src), reason="parse_error")

        mock_logger.error.assert_called_once()
        assert "Could not move file" in mock_logger.error.call_args[0][0]

    def test_start_returns_observer(self, handler):
        """start() should create and start an Observer."""
        with patch('scl.listener.file_watch.Observer') as MockObserver:
            mock_observer_instance = MagicMock()
            MockObserver.return_value = mock_observer_instance

            result = handler.start()

            MockObserver.assert_called_once()
            mock_observer_instance.schedule.assert_called_once_with(
                handler, handler.watch_path, recursive=False
            )
            mock_observer_instance.start.assert_called_once()
            assert result is mock_observer_instance

    def test_tracing_instrumentation(self, handler, tmp_path):
        """Ensure OpenTelemetry span is created and attributes set."""
        file_path = tmp_path / "task.json"
        file_path.write_text(json.dumps({"id": "trace-test"}))

        event = MagicMock(spec=FileCreatedEvent)
        event.is_directory = False
        event.src_path = str(file_path)

        mock_span = MagicMock()
        with patch('scl.listener.file_watch.trace.get_current_span', return_value=mock_span):
            mock_task = MagicMock()
            with patch.object(Task, 'from_dict', return_value=mock_task):
                with patch('scl.listener.file_watch.shutil.move'):
                    handler.on_created(event)

        mock_span.set_attribute.assert_any_call("file.path", str(file_path))
        mock_span.set_attribute.assert_any_call("file.name", "task.json")
        # Destination should also be set
        mock_span.set_attribute.assert_any_call(
            "file.moved_to",
            os.path.join(handler.processed_dir, "task.json")
        )

    def test_metrics_counters_called(self, handler, tmp_path):
        """Validate that metrics counters are properly incremented."""
        # Test receive counter
        file_path = tmp_path / "task.json"
        file_path.write_text(json.dumps({"id": "1"}))

        event = MagicMock(spec=FileCreatedEvent)
        event.is_directory = False
        event.src_path = str(file_path)

        with patch.object(Task, 'from_dict', return_value=MagicMock()):
            with patch('scl.listener.file_watch.shutil.move'):
                handler.on_created(event)

        handler.file_receive_counter.add.assert_called_once_with(1)
        handler.file_valid_counter.add.assert_called_once_with(1)
        handler.file_invalid_counter.add.assert_not_called()
        handler.task_conversion_failure_counter.add.assert_not_called()