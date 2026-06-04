"""
Unit tests for scl.listener.restful_watch.RestFulHandler
"""

import json
import os
from pathlib import Path
from unittest.mock import ANY, AsyncMock, MagicMock, mock_open, patch

import pytest
from fastapi import HTTPException
from opentelemetry import trace

from scl.listener.restful_watch import RestFulHandler
from scl.meta.captask import CapTask
from scl.meta.task import Task


@pytest.fixture
def mock_tracer():
    """Mock OpenTelemetry tracer."""
    with patch("scl.otel.otel.tracer") as mock_tracer:
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
    with patch("scl.listener.restful_watch.meter") as mock_meter:
        mock_counters = {}

        def create_counter_side_effect(name, *args, **kwargs):
            if name not in mock_counters:
                mock_counters[name] = MagicMock()
            return mock_counters[name]

        mock_meter.create_counter = MagicMock(side_effect=create_counter_side_effect)
        yield mock_meter, mock_counters


@pytest.fixture
def handler(tmp_path, mock_tracer, mock_meter):
    """Create a RestFulHandler instance with temporary directories."""
    _, mock_counters = mock_meter
    watch_path = str(tmp_path / "file_watch")
    # waiting_approval_dir is automatically created as watch_path/waitingapproval
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

    with patch("scl.meta.task.Task.from_dict", return_value=mock_task_instance) as mock_from_dict:
        m_open = mock_open()
        with patch("builtins.open", m_open):
            response = await handler._receive_task(mock_request)

    assert response == {"status": "accepted", "hash": "abc123"}
    mock_request.json.assert_called_once()
    mock_from_dict.assert_called_once_with(valid_payload)

    expected_file_path = os.path.join(handler.watch_path, "abc123.json")
    m_open.assert_called_once_with(expected_file_path, "w", encoding="utf-8")

    handler._mock_counters["restful_task_received"].add.assert_called_once_with(1)
    handler._mock_counters["restful_item_valid"].add.assert_called_once_with(
        1, {"item.type": "Task"}
    )
    handler._mock_counters["restful_item_invalid"].add.assert_not_called()


@pytest.mark.asyncio
async def test_receive_task_invalid_json(handler, mock_request):
    """Test POST /tasks with invalid JSON body returns 400."""
    mock_request.json.side_effect = ValueError("Invalid JSON")

    with pytest.raises(HTTPException) as exc_info:
        await handler._receive_task(mock_request)

    assert exc_info.value.status_code == 400
    assert "Invalid JSON body" in exc_info.value.detail
    handler._mock_counters["restful_task_received"].add.assert_not_called()
    handler._mock_counters["restful_item_invalid"].add.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_receive_task_conversion_failure(handler, mock_request):
    """Test POST /tasks with valid JSON but invalid Task format returns 422."""
    invalid_task_data = {"bad": "format"}
    mock_request.json.return_value = invalid_task_data

    with patch("scl.meta.task.Task.from_dict", side_effect=ValueError("Missing required field")):
        with pytest.raises(HTTPException) as exc_info:
            await handler._receive_task(mock_request)

        assert exc_info.value.status_code == 422
        assert "Invalid task format" in exc_info.value.detail

    handler._mock_counters["restful_task_received"].add.assert_called_once_with(1)
    handler._mock_counters["restful_item_invalid"].add.assert_called_once_with(
        1, {"item.type": "Task"}
    )
    handler._mock_counters["restful_item_valid"].add.assert_not_called()


@pytest.mark.asyncio
async def test_receive_task_missing_hash(handler, mock_request):
    """Test task without hash attribute raises 500."""
    valid_payload = {"id": "task-no-hash"}
    mock_request.json.return_value = valid_payload

    mock_task_instance = MagicMock(spec=Task)
    del mock_task_instance.hash

    with patch("scl.meta.task.Task.from_dict", return_value=mock_task_instance):
        with pytest.raises(HTTPException) as exc_info:
            await handler._receive_task(mock_request)

        assert exc_info.value.status_code == 500
        assert "no hash identifier" in exc_info.value.detail

    handler._mock_counters["restful_task_received"].add.assert_called_once_with(1)
    handler._mock_counters["restful_item_invalid"].add.assert_called_once_with(
        1, {"item.type": "Task"}
    )


# -----------------------------------------------------------------------------
# Tests for POST /captasks (receive_captask)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_receive_captask_success(handler, mock_request):
    """Test successful POST /captasks with valid CapTask data."""
    valid_payload = {"cap_name": "send_email", "args": ["to@example.com"], "hash": "cap123"}
    mock_request.json.return_value = valid_payload

    mock_captask_instance = MagicMock(spec=CapTask)
    mock_captask_instance.hash = "cap123"
    mock_captask_instance.to_dict.return_value = valid_payload

    with patch(
        "scl.meta.captask.CapTask.from_dict", return_value=mock_captask_instance
    ) as mock_from_dict:
        m_open = mock_open()
        with patch("builtins.open", m_open):
            response = await handler._receive_captask(mock_request)

    assert response == {"status": "accepted", "hash": "cap123"}
    mock_request.json.assert_called_once()
    mock_from_dict.assert_called_once_with(valid_payload)

    expected_file_path = os.path.join(handler.watch_path, "cap123.json")
    m_open.assert_called_once_with(expected_file_path, "w", encoding="utf-8")

    handler._mock_counters["restful_captask_received"].add.assert_called_once_with(1)
    handler._mock_counters["restful_item_valid"].add.assert_called_once_with(
        1, {"item.type": "CapTask"}
    )


@pytest.mark.asyncio
async def test_receive_captask_conversion_failure(handler, mock_request):
    """Test POST /captasks with invalid CapTask format returns 422."""
    invalid_data = {"wrong": "field"}
    mock_request.json.return_value = invalid_data

    with patch("scl.meta.captask.CapTask.from_dict", side_effect=ValueError("Invalid")):
        with pytest.raises(HTTPException) as exc_info:
            await handler._receive_captask(mock_request)

        assert exc_info.value.status_code == 422
        assert "Invalid captask format" in exc_info.value.detail

    handler._mock_counters["restful_captask_received"].add.assert_called_once_with(1)
    handler._mock_counters["restful_item_invalid"].add.assert_called_once_with(
        1, {"item.type": "CapTask"}
    )


# -----------------------------------------------------------------------------
# Tests for GET /items/{item_hash} (check_status)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_status_pending(handler, tmp_path):
    """Test status check when file exists in watch_path."""
    item_hash = "pending-item"
    file_path = Path(handler.watch_path) / f"{item_hash}.json"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.touch()

    response = await handler._check_status(item_hash)

    assert response == {"hash": item_hash, "status": "pending"}
    handler._mock_counters["restful_status_check"].add.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_check_status_waiting_approval(handler, tmp_path):
    """Test status check when file exists in waiting_approval_dir."""
    item_hash = "waiting-item"
    file_path = Path(handler.waiting_approval_dir) / f"{item_hash}.json"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.touch()

    response = await handler._check_status(item_hash)

    assert response == {"hash": item_hash, "status": "waiting_approval"}


@pytest.mark.asyncio
async def test_check_status_processed(handler, tmp_path):
    """Test status check when file exists in processed/ subdirectory."""
    item_hash = "processed-item"
    processed_dir = Path(handler.watch_path) / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    (processed_dir / f"{item_hash}.json").touch()

    response = await handler._check_status(item_hash)

    assert response == {"hash": item_hash, "status": "processed"}


@pytest.mark.asyncio
async def test_check_status_processed_captask(handler, tmp_path):
    """Test status check when file exists in processedCapTask/ subdirectory."""
    item_hash = "processed-cap"
    captask_dir = Path(handler.watch_path) / "processedCapTask"
    captask_dir.mkdir(parents=True, exist_ok=True)
    (captask_dir / f"{item_hash}.json").touch()

    response = await handler._check_status(item_hash)

    assert response == {"hash": item_hash, "status": "processed"}


@pytest.mark.asyncio
async def test_check_status_waiting_captask(handler, tmp_path):
    """Test status check when file exists in waitingCapTask/ subdirectory."""
    item_hash = "waiting-cap"
    waiting_dir = Path(handler.watch_path) / "waitingCapTask"
    waiting_dir.mkdir(parents=True, exist_ok=True)
    (waiting_dir / f"{item_hash}.json").touch()

    response = await handler._check_status(item_hash)

    assert response == {"hash": item_hash, "status": "waiting_captask"}


@pytest.mark.asyncio
async def test_check_status_failed(handler, tmp_path):
    """Test status check when file exists in failed/ subdirectory."""
    item_hash = "failed-item"
    failed_dir = Path(handler.watch_path) / "failed"
    failed_dir.mkdir(parents=True, exist_ok=True)
    (failed_dir / f"{item_hash}.json").touch()

    response = await handler._check_status(item_hash)

    assert response == {"hash": item_hash, "status": "failed"}


@pytest.mark.asyncio
async def test_check_status_not_found(handler):
    """Test status check when no file exists."""
    item_hash = "notfound"
    response = await handler._check_status(item_hash)
    assert response == {"hash": item_hash, "status": "not_found"}


# -----------------------------------------------------------------------------
# Tests for GET /tasks/waiting (list_waiting)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_waiting_empty(handler):
    """Test listing waiting items when directory is empty."""
    response = await handler._list_waiting()
    assert response == []


@pytest.mark.asyncio
async def test_list_waiting_with_items(handler):
    """Test listing waiting items with both Task and CapTask files."""
    # waiting_approval_dir is automatically a subdir of watch_path
    waiting_dir = Path(handler.waiting_approval_dir)
    waiting_dir.mkdir(parents=True, exist_ok=True)

    task_data = {
        "system_prompt": "You are a bot",
        "prompt_list": [],
        "hash": "task1",
        "approval": False,
    }
    captask_data = {"cap_name": "email", "args": ["x"], "hash": "cap1", "approval": False}

    with open(waiting_dir / "task1.json", "w") as f:
        json.dump(task_data, f)
    with open(waiting_dir / "cap1.json", "w") as f:
        json.dump(captask_data, f)

    response = await handler._list_waiting()

    assert len(response) == 2
    # Order may vary; check contents
    hashes = [item["hash"] for item in response]
    assert "task1" in hashes
    assert "cap1" in hashes

    for item in response:
        if item["hash"] == "task1":
            assert item["type"] == "Task"
            assert item["data"] == task_data
        elif item["hash"] == "cap1":
            assert item["type"] == "CapTask"
            assert item["data"] == captask_data


@pytest.mark.asyncio
async def test_list_waiting_handles_invalid_json(handler, caplog):
    """Test that malformed JSON files are skipped and logged."""
    waiting_dir = Path(handler.waiting_approval_dir)
    waiting_dir.mkdir(parents=True, exist_ok=True)

    with open(waiting_dir / "bad.json", "w") as f:
        f.write("not json")

    response = await handler._list_waiting()
    assert response == []
    assert "Failed to read waiting file" in caplog.text


# -----------------------------------------------------------------------------
# Tests for POST /items/{item_hash}/approve (approve_item)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_item_success(handler):
    """Test successful approval of an item."""
    item_hash = "approve-me"
    waiting_dir = Path(handler.waiting_approval_dir)
    waiting_dir.mkdir(parents=True, exist_ok=True)

    src_data = {"hash": item_hash, "approval": False, "content": "test"}
    src_path = waiting_dir / f"{item_hash}.json"
    with open(src_path, "w") as f:
        json.dump(src_data, f)

    response = await handler._approve_item(item_hash)

    assert response == {"hash": item_hash, "status": "approved"}
    # File moved to watch_path
    dest_path = Path(handler.watch_path) / f"{item_hash}.json"
    assert dest_path.exists()
    assert not src_path.exists()

    # Check updated approval flag in destination file
    with open(dest_path) as f:
        moved_data = json.load(f)
    assert moved_data["approval"] is True

    handler._mock_counters["restful_approve"].add.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_approve_item_not_found(handler):
    """Test approval of non-existent item returns 404."""
    item_hash = "missing"

    with pytest.raises(HTTPException) as exc_info:
        await handler._approve_item(item_hash)

    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.detail


@pytest.mark.asyncio
async def test_approve_item_supports_yaml(handler):
    """Test approval works with YAML file."""
    item_hash = "yaml-approve"
    waiting_dir = Path(handler.waiting_approval_dir)
    waiting_dir.mkdir(parents=True, exist_ok=True)

    src_data = {"hash": item_hash, "approval": False, "content": "yaml"}
    yaml_file_path = str(waiting_dir / f"{item_hash}.yaml")
    dest_file_path = os.path.join(handler.watch_path, f"{item_hash}.yaml")

    # 创建模拟的 yaml 模块
    mock_yaml_module = MagicMock()
    mock_yaml_module.safe_load.return_value = src_data
    mock_yaml_module.dump = MagicMock()

    def isfile_side_effect(path):
        return path == yaml_file_path

    with patch.dict("sys.modules", {"yaml": mock_yaml_module}):
        with patch("os.path.isfile", side_effect=isfile_side_effect):
            m_open = mock_open(read_data="hash: yaml-approve\napproval: false")
            with patch("builtins.open", m_open):
                with patch("os.remove") as mock_remove:
                    response = await handler._approve_item(item_hash)

    assert response == {"hash": item_hash, "status": "approved"}
    mock_remove.assert_called_once_with(yaml_file_path)
    handler._mock_counters["restful_approve"].add.assert_called_once_with(1)
    mock_yaml_module.dump.assert_called_once()
    # 确认目标文件被正确写入
    m_open.assert_any_call(dest_file_path, "w", encoding="utf-8")


# -----------------------------------------------------------------------------
# Initialization and Utility Tests
# -----------------------------------------------------------------------------


def test_handler_initialization(tmp_path):
    """Test RestFulHandler initialization creates required directories."""
    watch_path = tmp_path / "custom_watch"
    handler = RestFulHandler(
        watch_path=str(watch_path), host="0.0.0.0", port=8080, log_level="debug"
    )
    assert handler.watch_path == str(watch_path)
    # waiting_approval_dir is a fixed subdirectory
    expected_waiting = os.path.join(str(watch_path), "waitingapproval")
    assert handler.waiting_approval_dir == expected_waiting
    assert handler.host == "0.0.0.0"
    assert handler.port == 8080
    assert handler.log_level == "debug"
    assert os.path.exists(watch_path)
    assert os.path.exists(expected_waiting)


@patch("scl.listener.restful_watch.uvicorn")
def test_start_method(mock_uvicorn, handler):
    """Test start() calls uvicorn.run with correct parameters."""
    handler.start()
    mock_uvicorn.run.assert_called_once_with(
        handler.app, host=handler.host, port=handler.port, log_level=handler.log_level
    )


def test_write_item_file(handler):
    """Test _write_item_file writes JSON file correctly."""
    item_hash = "item123"
    item_dict = {"key": "value", "hash": item_hash}
    mock_item = MagicMock()
    mock_item.to_dict.return_value = item_dict

    with patch("builtins.open", mock_open()) as m_open:
        file_path = handler._write_item_file(mock_item, item_hash, "Task")

    expected_path = os.path.join(handler.watch_path, f"{item_hash}.json")
    assert file_path == expected_path
    m_open.assert_called_once_with(expected_path, "w", encoding="utf-8")


def test_guess_type(handler):
    """Test _guess_type correctly identifies Task vs CapTask."""
    assert handler._guess_type({"cap_name": "x", "args": []}, "") == "CapTask"
    assert handler._guess_type({"system_prompt": "hi"}, "") == "Task"
    assert handler._guess_type({}, "") == "Unknown"
