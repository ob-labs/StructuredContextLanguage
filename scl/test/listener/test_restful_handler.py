"""
Unit tests for scl.listener.restful_watch.RestFulHandler
"""

import pytest
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, ANY, mock_open

from fastapi import HTTPException
from opentelemetry import trace

from scl.listener.restful_watch import RestFulHandler
from scl.meta.task import Task


@pytest.fixture
def mock_tracer():
    """Mock OpenTelemetry tracer."""
    with patch('scl.otel.otel.tracer') as mock_tracer:
        # Configure start_as_current_span to act as a no-op decorator that returns a mock span
        mock_span = MagicMock()
        mock_span.__enter__ = MagicMock(return_value=mock_span)
        mock_span.__exit__ = MagicMock(return_value=None)
        
        def decorator_factory(name):
            def decorator(func):
                async def wrapper(*args, **kwargs):
                    return await func(*args, **kwargs)
                return wrapper
            return decorator
        
        mock_tracer.start_as_current_span = MagicMock(side_effect=decorator_factory)
        yield mock_tracer


@pytest.fixture
def mock_meter():
    """Mock OpenTelemetry meter."""
    with patch('scl.listener.restful_watch.meter') as mock_meter:
        mock_counters = {}
        def create_counter_side_effect(name, *args, **kwargs):
            if name not in mock_counters:
                mock_counters[name] = MagicMock()
            return mock_counters[name]
        mock_meter.create_counter = MagicMock(side_effect=create_counter_side_effect)
        yield mock_meter, mock_counters


@pytest.fixture
def handler(tmp_path, mock_tracer, mock_meter):
    """Create a RestFulHandler instance with a temporary watch_path."""
    _, mock_counters = mock_meter
    watch_path = str(tmp_path / "watch")
    handler = RestFulHandler(watch_path=watch_path, host="127.0.0.1", port=8001)
    # Attach counter mocks to handler for easier access in tests
    handler._mock_counters = mock_counters
    return handler


@pytest.fixture
def mock_request():
    """Create a mock FastAPI Request object."""
    request = AsyncMock()
    request.client = MagicMock()
    request.client.host = "192.168.1.100"
    request.json = AsyncMock()
    return request


# -----------------------------------------------------------------------------
# Tests for POST /tasks (receive_task)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_receive_task_success(handler, mock_request, tmp_path):
    """Test successful POST /tasks with valid task data."""
    # Arrange
    valid_payload = {"id": "task-123", "data": "sample", "hash": "abc123"}
    mock_request.json.return_value = valid_payload

    mock_task_instance = MagicMock(spec=Task)
    mock_task_instance.hash = "abc123"
    mock_task_instance.to_dict.return_value = valid_payload

    with patch('scl.meta.task.Task.from_dict', return_value=mock_task_instance) as mock_from_dict:
        # Mock file writing
        m_open = mock_open()
        with patch('builtins.open', m_open):
            # Act
            response = await handler._receive_task(mock_request)

    # Assert
    assert response == {"status": "accepted", "task_hash": "abc123"}
    mock_request.json.assert_called_once()
    mock_from_dict.assert_called_once_with(valid_payload)

    # Verify file was written
    expected_file_path = os.path.join(handler.watch_path, "abc123.json")
    m_open.assert_called_once_with(expected_file_path, 'w', encoding='utf-8')
    handle = m_open()
    # Check that json.dump was called with the correct dict
    handle.write.assert_called()  # We can't easily check the content due to json.dump internals

    # Verify metrics
    handler._mock_counters["restful_task_received"].add.assert_called_once_with(1)
    handler._mock_counters["restful_task_valid"].add.assert_called_once_with(1)
    handler._mock_counters["restful_task_invalid"].add.assert_not_called()


@pytest.mark.asyncio
async def test_receive_task_invalid_json(handler, mock_request):
    """Test POST /tasks with invalid JSON body returns 400."""
    # Arrange
    mock_request.json.side_effect = ValueError("Invalid JSON")

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        await handler._receive_task(mock_request)

    assert exc_info.value.status_code == 400
    assert "Invalid JSON body" in exc_info.value.detail

    # Verify metrics
    handler._mock_counters["restful_task_received"].add.assert_not_called()
    handler._mock_counters["restful_task_invalid"].add.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_receive_task_conversion_failure(handler, mock_request):
    """Test POST /tasks with valid JSON but invalid Task format returns 422."""
    # Arrange
    invalid_task_data = {"bad": "format"}
    mock_request.json.return_value = invalid_task_data

    with patch('scl.meta.task.Task.from_dict', side_effect=ValueError("Missing required field")) as mock_from_dict:
        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            await handler._receive_task(mock_request)

        assert exc_info.value.status_code == 422
        assert "Invalid task format" in exc_info.value.detail

    # Verify metrics
    handler._mock_counters["restful_task_received"].add.assert_called_once_with(1)
    handler._mock_counters["restful_task_invalid"].add.assert_called_once_with(1)
    handler._mock_counters["restful_task_valid"].add.assert_not_called()


@pytest.mark.asyncio
async def test_receive_task_missing_hash(handler, mock_request):
    """Test task without hash attribute raises 500."""
    # Arrange
    valid_payload = {"id": "task-no-hash"}
    mock_request.json.return_value = valid_payload

    mock_task_instance = MagicMock(spec=Task)
    del mock_task_instance.hash  # simulate missing hash attribute

    with patch('scl.meta.task.Task.from_dict', return_value=mock_task_instance):
        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            await handler._receive_task(mock_request)

        assert exc_info.value.status_code == 500
        assert "no hash identifier" in exc_info.value.detail

    # Verify metrics
    handler._mock_counters["restful_task_received"].add.assert_called_once_with(1)
    handler._mock_counters["restful_task_invalid"].add.assert_called_once_with(1)
    handler._mock_counters["restful_task_valid"].add.assert_not_called()


@pytest.mark.asyncio
async def test_receive_task_file_write_error(handler, mock_request):
    """Test that file write errors propagate appropriately."""
    # Arrange
    valid_payload = {"hash": "xyz789"}
    mock_request.json.return_value = valid_payload

    mock_task_instance = MagicMock(spec=Task)
    mock_task_instance.hash = "xyz789"
    mock_task_instance.to_dict.return_value = valid_payload

    with patch('scl.meta.task.Task.from_dict', return_value=mock_task_instance):
        # Simulate file write error (e.g., disk full)
        m_open = mock_open()
        m_open.side_effect = OSError("Disk full")
        with patch('builtins.open', m_open):
            with pytest.raises(OSError, match="Disk full"):
                await handler._receive_task(mock_request)

    # Metrics: received and invalid (since exception occurred)
    handler._mock_counters["restful_task_received"].add.assert_called_once_with(1)
    # Note: valid counter not incremented due to exception


@pytest.mark.asyncio
async def test_receive_task_logs_client_ip(handler, mock_request, caplog):
    """Test that client IP is logged appropriately."""
    # Arrange
    valid_payload = {"hash": "test-hash"}
    mock_request.json.return_value = valid_payload
    mock_request.client.host = "10.0.0.42"

    mock_task_instance = MagicMock(spec=Task)
    mock_task_instance.hash = "test-hash"
    mock_task_instance.to_dict.return_value = valid_payload

    with patch('scl.meta.task.Task.from_dict', return_value=mock_task_instance):
        with patch('builtins.open', mock_open()):
            with caplog.at_level("INFO"):
                await handler._receive_task(mock_request)

    # Assert
    assert "Received task payload from 10.0.0.42" in caplog.text


@pytest.mark.asyncio
async def test_receive_task_handles_request_without_client(handler):
    """Test handling of request where client attribute is None."""
    # Arrange
    request = AsyncMock()
    request.client = None
    request.json.return_value = {"hash": "test"}

    mock_task_instance = MagicMock(spec=Task)
    mock_task_instance.hash = "test"
    mock_task_instance.to_dict.return_value = {"hash": "test"}

    with patch('scl.meta.task.Task.from_dict', return_value=mock_task_instance):
        with patch('builtins.open', mock_open()):
            # Act - should not raise exception
            response = await handler._receive_task(request)

    assert response == {"status": "accepted", "task_hash": "test"}


# -----------------------------------------------------------------------------
# Tests for GET /tasks/{task_hash} (check_status)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_status_pending(handler, tmp_path):
    """Test status check when task file exists in watch_path."""
    # Arrange
    task_hash = "pending-task"
    file_path = tmp_path / "watch" / f"{task_hash}.json"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.touch()

    # Act
    response = await handler._check_status(task_hash)

    # Assert
    assert response == {"task_hash": task_hash, "status": "pending"}
    handler._mock_counters["restful_status_check"].add.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_check_status_processed(handler, tmp_path):
    """Test status check when task file exists in processed/ subdirectory."""
    # Arrange
    task_hash = "processed-task"
    processed_dir = tmp_path / "watch" / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    file_path = processed_dir / f"{task_hash}.json"
    file_path.touch()

    # Act
    response = await handler._check_status(task_hash)

    # Assert
    assert response == {"task_hash": task_hash, "status": "processed"}
    handler._mock_counters["restful_status_check"].add.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_check_status_failed(handler, tmp_path):
    """Test status check when task file exists in failed/ subdirectory."""
    # Arrange
    task_hash = "failed-task"
    failed_dir = tmp_path / "watch" / "failed"
    failed_dir.mkdir(parents=True, exist_ok=True)
    file_path = failed_dir / f"{task_hash}.json"
    file_path.touch()

    # Act
    response = await handler._check_status(task_hash)

    # Assert
    assert response == {"task_hash": task_hash, "status": "failed"}
    handler._mock_counters["restful_status_check"].add.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_check_status_not_found(handler, tmp_path):
    """Test status check when no task file exists."""
    # Arrange
    task_hash = "notfound-task"
    # Ensure directories exist but no file
    (tmp_path / "watch").mkdir(parents=True, exist_ok=True)

    # Act
    response = await handler._check_status(task_hash)

    # Assert
    assert response == {"task_hash": task_hash, "status": "not_found"}
    handler._mock_counters["restful_status_check"].add.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_check_status_prefers_pending_over_subdirs(handler, tmp_path):
    """Test that pending status is returned even if file exists in subdirs (edge case)."""
    # Arrange
    task_hash = "multi-status"
    # Create file in watch_path (pending)
    pending_file = tmp_path / "watch" / f"{task_hash}.json"
    pending_file.parent.mkdir(parents=True, exist_ok=True)
    pending_file.touch()

    # Also create file in processed subdir
    processed_dir = tmp_path / "watch" / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    (processed_dir / f"{task_hash}.json").touch()

    # Act
    response = await handler._check_status(task_hash)

    # Assert
    assert response["status"] == "pending"


@pytest.mark.asyncio
async def test_check_status_with_non_json_extension(handler, tmp_path):
    """Test status check with file having non-json extension (e.g., .yaml)."""
    # Arrange
    task_hash = "yaml-task"
    file_path = tmp_path / "watch" / f"{task_hash}.yaml"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.touch()

    # Act
    response = await handler._check_status(task_hash)

    # Assert
    assert response["status"] == "pending"


# -----------------------------------------------------------------------------
# Initialization and Utility Tests
# -----------------------------------------------------------------------------

def test_handler_initialization(tmp_path):
    """Test RestFulHandler initialization sets attributes and creates watch directory."""
    watch_path = tmp_path / "custom_watch"
    handler = RestFulHandler(
        watch_path=str(watch_path),
        host="0.0.0.0",
        port=8080,
        log_level="debug"
    )
    assert handler.watch_path == str(watch_path)
    assert handler.host == "0.0.0.0"
    assert handler.port == 8080
    assert handler.log_level == "debug"
    assert handler.app is not None
    assert handler.logger is not None
    assert os.path.exists(watch_path)


def test_handler_initialization_creates_watch_dir_if_missing(tmp_path):
    """Test that initialization creates watch directory if it doesn't exist."""
    watch_path = tmp_path / "non_existent_dir"
    assert not watch_path.exists()
    RestFulHandler(watch_path=str(watch_path))
    assert watch_path.exists()
    assert watch_path.is_dir()


@patch('scl.listener.restful_watch.uvicorn')
def test_start_method(mock_uvicorn, handler):
    """Test start() calls uvicorn.run with correct parameters."""
    handler.start()
    mock_uvicorn.run.assert_called_once_with(
        handler.app,
        host=handler.host,
        port=handler.port,
        log_level=handler.log_level
    )


def test_write_task_file_uses_json_format(handler, tmp_path):
    """Test _write_task_file writes JSON file with correct content."""
    task_hash = "test123"
    task_dict = {"key": "value", "hash": task_hash}
    mock_task = MagicMock(spec=Task)
    mock_task.to_dict.return_value = task_dict

    with patch('builtins.open', mock_open()) as m_open:
        file_path = handler._write_task_file(mock_task, task_hash)

    expected_path = os.path.join(handler.watch_path, f"{task_hash}.json")
    assert file_path == expected_path
    m_open.assert_called_once_with(expected_path, 'w', encoding='utf-8')
    handle = m_open()
    # Verify json.dump was called
    # We can capture the written string
    written_data = ''.join(call.args[0] for call in handle.write.call_args_list)
    expected_json = json.dumps(task_dict, indent=2)
    assert written_data == expected_json


def test_write_task_file_without_to_dict_falls_back_to_dict(handler):
    """Test _write_task_file when Task lacks to_dict method."""
    task_hash = "fallback"
    mock_task = MagicMock(spec=Task)
    del mock_task.to_dict
    mock_task.__dict__ = {"attr": "val", "hash": task_hash}

    with patch('builtins.open', mock_open()) as m_open:
        file_path = handler._write_task_file(mock_task, task_hash)

    # Check that json.dump got the __dict__ content
    handle = m_open()
    written_data = ''.join(call.args[0] for call in handle.write.call_args_list)
    expected_json = json.dumps(mock_task.__dict__, indent=2)
    assert written_data == expected_json