"""
Unit tests for scl.listener.RestFulHandler
"""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch, ANY

from fastapi import HTTPException
from opentelemetry import trace

from scl.listener.restful_watch import RestFulHandler
from scl.meta.taskQueue import TaskQueue
from scl.meta.task import Task


@pytest.fixture
def mock_task_queue():
    """Mock TaskQueue instance."""
    queue = MagicMock(spec=TaskQueue)
    queue.add = MagicMock()
    return queue


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
def handler(mock_task_queue, mock_tracer):
    """Create a RestFulHandler instance with mocked dependencies."""
    # Create separate mocks for each counter
    mock_counters = {}
    
    def create_counter_side_effect(name, *args, **kwargs):
        if name not in mock_counters:
            mock_counters[name] = MagicMock()
        return mock_counters[name]
    
    # Patch where meter is used, not where it's defined
    with patch('scl.listener.restful_watch.meter') as mock_meter:
        mock_meter.create_counter = MagicMock(side_effect=create_counter_side_effect)
        handler = RestFulHandler(todo_queue=mock_task_queue, host="127.0.0.1", port=8001)
        # Attach counter mocks to handler for easier access in tests
        handler._mock_counters = mock_counters
        yield handler


@pytest.fixture
def mock_request():
    """Create a mock FastAPI Request object."""
    request = AsyncMock()
    request.client = MagicMock()
    request.client.host = "192.168.1.100"
    request.json = AsyncMock()
    return request


@pytest.mark.asyncio
async def test_receive_todo_success(handler, mock_request, mock_task_queue, mock_tracer):
    """Test successful POST /todo with valid task data."""
    # Arrange
    valid_payload = {"id": "task-123", "data": "sample", "hash": "abc123"}
    mock_request.json.return_value = valid_payload

    mock_task_instance = MagicMock(spec=Task)
    mock_task_instance.hash = "abc123"

    with patch('scl.meta.task.Task.from_dict', return_value=mock_task_instance) as mock_from_dict:
        # Act
        response = await handler._receive_todo(mock_request)

    # Assert
    assert response == {"status": "accepted", "task_id": "abc123"}
    mock_request.json.assert_called_once()
    mock_from_dict.assert_called_once_with(valid_payload)
    mock_task_queue.add.assert_called_once_with(mock_task_instance)

    # Verify metrics were called
    handler._mock_counters["restful_task_received"].add.assert_called_once_with(1)
    handler._mock_counters["restful_task_valid"].add.assert_called_once_with(1)
    handler._mock_counters["restful_task_invalid"].add.assert_not_called()

    # Verify span attributes (mock tracer ensures span is available)
    # In a real test, you'd use a proper tracing mock to verify set_attribute calls


@pytest.mark.asyncio
async def test_receive_todo_invalid_json(handler, mock_request):
    """Test POST /todo with invalid JSON body returns 400."""
    # Arrange
    mock_request.json.side_effect = ValueError("Invalid JSON")

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        await handler._receive_todo(mock_request)

    assert exc_info.value.status_code == 400
    assert "Invalid JSON body" in exc_info.value.detail

    # Verify metrics
    handler._mock_counters["restful_task_received"].add.assert_not_called()
    handler._mock_counters["restful_task_invalid"].add.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_receive_todo_task_conversion_failure(handler, mock_request):
    """Test POST /todo with valid JSON but invalid Task format returns 422."""
    # Arrange
    invalid_task_data = {"bad": "format"}
    mock_request.json.return_value = invalid_task_data

    with patch('scl.meta.task.Task.from_dict', side_effect=ValueError("Missing required field")) as mock_from_dict:
        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            await handler._receive_todo(mock_request)

        assert exc_info.value.status_code == 422
        assert "Invalid task format" in exc_info.value.detail

    # Verify metrics
    handler._mock_counters["restful_task_received"].add.assert_called_once_with(1)
    handler._mock_counters["restful_task_invalid"].add.assert_called_once_with(1)
    handler._mock_counters["restful_task_valid"].add.assert_not_called()


@pytest.mark.asyncio
async def test_receive_todo_queue_unavailable(handler, mock_request, mock_task_queue):
    """Test POST /todo when queue.add raises exception returns 500."""
    # Arrange
    valid_payload = {"id": "task-456", "hash": "def456"}
    mock_request.json.return_value = valid_payload

    mock_task_instance = MagicMock(spec=Task)
    mock_task_instance.hash = "def456"

    mock_task_queue.add.side_effect = RuntimeError("Queue connection lost")

    with patch('scl.meta.task.Task.from_dict', return_value=mock_task_instance):
        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            await handler._receive_todo(mock_request)

        assert exc_info.value.status_code == 500
        assert "Queue unavailable" in exc_info.value.detail

    # Verify metrics
    handler._mock_counters["restful_task_received"].add.assert_called_once_with(1)
    handler._mock_counters["restful_task_valid"].add.assert_not_called()


@pytest.mark.asyncio
async def test_receive_todo_task_without_hash(handler, mock_request, mock_task_queue):
    """Test task without hash attribute still processed correctly."""
    # Arrange
    payload_no_hash = {"id": "no-hash-task"}
    mock_request.json.return_value = payload_no_hash

    mock_task_instance = MagicMock(spec=Task)
    del mock_task_instance.hash  # simulate missing hash attribute

    with patch('scl.meta.task.Task.from_dict', return_value=mock_task_instance):
        # Act
        response = await handler._receive_todo(mock_request)

    # Assert
    assert response == {"status": "accepted", "task_id": None}
    mock_task_queue.add.assert_called_once_with(mock_task_instance)
    handler._mock_counters["restful_task_valid"].add.assert_called_once_with(1)


def test_handler_initialization(mock_task_queue):
    """Test RestFulHandler initialization sets attributes correctly."""
    handler = RestFulHandler(
        todo_queue=mock_task_queue,
        host="0.0.0.0",
        port=8080,
        log_level="debug"
    )
    assert handler.todo_queue == mock_task_queue
    assert handler.host == "0.0.0.0"
    assert handler.port == 8080
    assert handler.log_level == "debug"
    assert handler.app is not None
    assert handler.logger is not None


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


@pytest.mark.asyncio
async def test_receive_todo_logs_client_ip(handler, mock_request, caplog):
    """Test that client IP is logged appropriately."""
    # Arrange
    valid_payload = {"hash": "test-hash"}
    mock_request.json.return_value = valid_payload
    mock_request.client.host = "10.0.0.42"

    mock_task_instance = MagicMock(spec=Task)
    mock_task_instance.hash = "test-hash"

    with patch('scl.meta.task.Task.from_dict', return_value=mock_task_instance):
        with caplog.at_level("INFO"):
            await handler._receive_todo(mock_request)

    # Assert
    assert "Received task payload from 10.0.0.42" in caplog.text


@pytest.mark.asyncio
async def test_receive_todo_handles_request_without_client(handler):
    """Test handling of request where client attribute is None."""
    # Arrange
    request = AsyncMock()
    request.client = None
    request.json.return_value = {"hash": "test"}

    mock_task_instance = MagicMock(spec=Task)
    mock_task_instance.hash = "test"

    with patch('scl.meta.task.Task.from_dict', return_value=mock_task_instance):
        # Act - should not raise exception
        response = await handler._receive_todo(request)

    assert response == {"status": "accepted", "task_id": "test"}