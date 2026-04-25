"""
RESTful API for receiving scl.meta.task items via POST requests
1. It receives scl.meta.task format as JSON body.
2. It converts the task into a Task instance.
3. It responds with the scl.meta.task's hash value to the client if successful.
4. It allows clients to check the status of a task or captask by its hash value.
5. It lists all tasks or captasks which waiting for approval.
6. It allows clients to approve tasks or captasks by hash value.

RESTful API for client to check existing task and its status.
1. It receives hash value as a path parameter.
2. It checks if the hash value exists in the file_watch directory.
3. It responds with the task status in JSON format.

Note:
waiting_approval_dir is a subdirectory under watch_path (fixed name: "waitingapproval").

Dependencies:
    fastapi, uvicorn, pyyaml, opentelemetry-api, opentelemetry-sdk

Install with:
    pip install fastapi uvicorn pyyaml opentelemetry-api opentelemetry-sdk
"""

import logging
import os
import json
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Union

import uvicorn
from fastapi import FastAPI, Request, HTTPException

from scl.otel.otel import tracer, meter
from scl.meta.task import Task
from scl.meta.captask import CapTask

from opentelemetry import trace

logger = logging.getLogger(__name__)


class RestFulHandler:
    """
    REST API handler that receives Task/CapTask items, writes them to file_watch,
    provides status lookup, lists waiting items, and allows approval.

    Example usage:
        handler = RestFulHandler(
            watch_path="/path/to/file_watch",
            host="0.0.0.0",
            port=8000
        )
        handler.start()  # Blocks until server stops
    """

    def __init__(
        self,
        watch_path: str,
        host: str = "0.0.0.0",
        port: int = 8000,
        log_level: str = "info"
    ):
        """
        Initialize REST handler.

        :param watch_path: Directory where task files will be written (same as file watcher's watch_path)
        :param host: Binding address
        :param port: Binding port
        :param log_level: Uvicorn log level
        """
        self.watch_path = watch_path
        # waiting_approval_dir is a fixed subdirectory under watch_path
        self.waiting_approval_dir = os.path.join(watch_path, "waitingapproval")
        self.host = host
        self.port = int(port)
        self.log_level = log_level
        self.logger = logger

        # Ensure directories exist
        os.makedirs(self.watch_path, exist_ok=True)
        os.makedirs(self.waiting_approval_dir, exist_ok=True)

        # Metrics
        self.restful_task_counter = meter.create_counter(
            "restful_task_received",
            description="Total number of REST tasks received"
        )
        self.restful_captask_counter = meter.create_counter(
            "restful_captask_received",
            description="Total number of REST captasks received"
        )
        self.restful_item_valid_counter = meter.create_counter(
            "restful_item_valid",
            description="Number of REST items successfully processed"
        )
        self.restful_item_invalid_counter = meter.create_counter(
            "restful_item_invalid",
            description="Number of REST items that failed validation/conversion"
        )
        self.status_check_counter = meter.create_counter(
            "restful_status_check",
            description="Number of status checks performed"
        )
        self.approve_counter = meter.create_counter(
            "restful_approve",
            description="Number of approval actions performed"
        )

        # FastAPI app
        self.app = FastAPI(title="Task & CapTask Receiver API")
        self._register_routes()

    def _register_routes(self):
        """Register all API endpoints."""
        self.app.add_api_route("/tasks", self._receive_task, methods=["POST"])
        self.app.add_api_route("/captasks", self._receive_captask, methods=["POST"])
        self.app.add_api_route("/items/{item_hash}", self._check_status, methods=["GET"])
        self.app.add_api_route("/tasks/waiting", self._list_waiting, methods=["GET"])
        self.app.add_api_route("/items/{item_hash}/approve", self._approve_item, methods=["POST"])

    # -------------------------------------------------------------------------
    # POST /tasks
    # -------------------------------------------------------------------------
    @tracer.start_as_current_span("rest_api_receive_task")
    async def _receive_task(self, request: Request) -> Dict[str, str]:
        """
        POST /tasks endpoint.
        Validates JSON, converts to Task, writes file to watch_path, returns hash.
        """
        current_span = trace.get_current_span()
        client_ip = request.client.host if request.client else "unknown"
        current_span.set_attribute("client.ip", client_ip)
        current_span.set_attribute("item.type", "Task")

        data = await self._parse_json(request, current_span)
        self.restful_task_counter.add(1)

        try:
            task_obj = Task.from_dict(data)
        except Exception as e:
            self.logger.error(f"Failed to create Task object: {e}")
            current_span.record_exception(e)
            self.restful_item_invalid_counter.add(1, {"item.type": "Task"})
            raise HTTPException(status_code=422, detail="Invalid task format")

        return self._finalize_item(task_obj, "Task", current_span)

    # -------------------------------------------------------------------------
    # POST /captasks
    # -------------------------------------------------------------------------
    @tracer.start_as_current_span("rest_api_receive_captask")
    async def _receive_captask(self, request: Request) -> Dict[str, str]:
        """
        POST /captasks endpoint.
        Validates JSON, converts to CapTask, writes file to watch_path, returns hash.
        """
        current_span = trace.get_current_span()
        client_ip = request.client.host if request.client else "unknown"
        current_span.set_attribute("client.ip", client_ip)
        current_span.set_attribute("item.type", "CapTask")

        data = await self._parse_json(request, current_span)
        self.restful_captask_counter.add(1)

        try:
            captask_obj = CapTask.from_dict(data)
        except Exception as e:
            self.logger.error(f"Failed to create CapTask object: {e}")
            current_span.record_exception(e)
            self.restful_item_invalid_counter.add(1, {"item.type": "CapTask"})
            raise HTTPException(status_code=422, detail="Invalid captask format")

        return self._finalize_item(captask_obj, "CapTask", current_span)

    # -------------------------------------------------------------------------
    # GET /items/{item_hash}
    # -------------------------------------------------------------------------
    @tracer.start_as_current_span("rest_api_check_status")
    async def _check_status(self, item_hash: str) -> Dict[str, str]:
        """
        GET /items/{item_hash} endpoint.
        Checks if the item file exists and returns its processing status.
        """
        current_span = trace.get_current_span()
        current_span.set_attribute("item.hash", item_hash)
        self.status_check_counter.add(1)

        self.logger.debug(f"Status check requested for hash: {item_hash}")

        status = self._determine_status(item_hash)
        current_span.set_attribute("item.status", status)

        self.logger.info(f"Status for {item_hash}: {status}")
        return {"hash": item_hash, "status": status}

    # -------------------------------------------------------------------------
    # GET /tasks/waiting
    # -------------------------------------------------------------------------
    @tracer.start_as_current_span("rest_api_list_waiting")
    async def _list_waiting(self) -> List[Dict[str, Union[str, Dict]]]:
        """
        GET /tasks/waiting endpoint.
        Lists all items (Task/CapTask) currently waiting for approval.
        Scans the waiting_approval_dir for JSON files and returns their content.
        """
        current_span = trace.get_current_span()
        waiting_items = []

        if not os.path.isdir(self.waiting_approval_dir):
            return waiting_items

        for filename in os.listdir(self.waiting_approval_dir):
            if not filename.lower().endswith('.json'):
                continue
            filepath = os.path.join(self.waiting_approval_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                item_type = self._guess_type(data, filename)
                waiting_items.append({
                    "hash": Path(filename).stem,
                    "type": item_type,
                    "data": data
                })
            except Exception as e:
                self.logger.warning(f"Failed to read waiting file {filename}: {e}")

        current_span.set_attribute("waiting.count", len(waiting_items))
        self.logger.info(f"Returning {len(waiting_items)} waiting items")
        return waiting_items

    # -------------------------------------------------------------------------
    # POST /items/{item_hash}/approve
    # -------------------------------------------------------------------------
    @tracer.start_as_current_span("rest_api_approve_item")
    async def _approve_item(self, item_hash: str) -> Dict[str, str]:
        """
        POST /items/{item_hash}/approve endpoint.
        Approves a waiting item by moving its file from waiting_approval_dir
        to watch_path and updating its approval flag.
        """
        current_span = trace.get_current_span()
        current_span.set_attribute("item.hash", item_hash)

        # Locate file in waiting_approval_dir
        src_path = None
        for ext in ['.json', '.yaml', '.yml']:
            candidate = os.path.join(self.waiting_approval_dir, f"{item_hash}{ext}")
            if os.path.isfile(candidate):
                src_path = candidate
                break

        if src_path is None:
            self.logger.warning(f"Item {item_hash} not found in waiting_approval_dir")
            current_span.set_attribute("error", "not_found")
            raise HTTPException(status_code=404, detail="Item not found in waiting list")

        try:
            # Read file content
            with open(src_path, 'r', encoding='utf-8') as f:
                if src_path.endswith(('.yaml', '.yml')):
                    import yaml
                    data = yaml.safe_load(f)
                else:
                    data = json.load(f)

            # Update approval flag
            data['approval'] = True

            # Write updated file to watch_path
            dest_path = os.path.join(self.watch_path, os.path.basename(src_path))
            with open(dest_path, 'w', encoding='utf-8') as f:
                if dest_path.endswith(('.yaml', '.yml')):
                    import yaml
                    yaml.dump(data, f)
                else:
                    json.dump(data, f, indent=2)

            # Remove original file
            os.remove(src_path)

            self.approve_counter.add(1)
            self.logger.info(f"Item {item_hash} approved and moved to {dest_path}")
            current_span.set_attribute("item.approved", True)

            return {"hash": item_hash, "status": "approved"}

        except Exception as e:
            self.logger.error(f"Failed to approve item {item_hash}: {e}")
            current_span.record_exception(e)
            raise HTTPException(status_code=500, detail="Approval failed")

    # -------------------------------------------------------------------------
    # Helper methods
    # -------------------------------------------------------------------------
    async def _parse_json(self, request: Request, span: trace.Span) -> dict:
        """Parse JSON body, raise HTTP 400 on failure."""
        try:
            return await request.json()
        except Exception as e:
            self.logger.warning(f"Invalid JSON received: {e}")
            span.record_exception(e)
            self.restful_item_invalid_counter.add(1)
            raise HTTPException(status_code=400, detail="Invalid JSON body")

    def _finalize_item(self, item: Union[Task, CapTask], item_type: str, span: trace.Span) -> Dict[str, str]:
        """Write item to file and return hash response."""
        item_hash = getattr(item, 'hash', None)
        if not item_hash:
            self.logger.error(f"{item_type} object missing 'hash' attribute")
            span.set_attribute("error", "missing_hash")
            self.restful_item_invalid_counter.add(1, {"item.type": item_type})
            raise HTTPException(status_code=500, detail=f"{item_type} object has no hash identifier")

        span.set_attribute("item.hash", item_hash)

        file_path = self._write_item_file(item, item_hash, item_type)
        span.set_attribute("file.path", file_path)

        self.logger.info(f"{item_type} {item_hash} written to {file_path}")
        self.restful_item_valid_counter.add(1, {"item.type": item_type})

        return {"status": "accepted", "hash": item_hash}

    def _write_item_file(self, item: Union[Task, CapTask], item_hash: str, item_type: str) -> str:
        """Write item to a JSON file in watch_path."""
        ext = ".json"
        file_path = os.path.join(self.watch_path, f"{item_hash}{ext}")

        item_dict = item.to_dict() if hasattr(item, 'to_dict') else item.__dict__

        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(item_dict, f, indent=2)

        return file_path

    def _determine_status(self, item_hash: str) -> str:
        """
        Determine the status of an item by checking its file location.
        Possible statuses: "pending", "processed", "failed", "waiting_approval", "waiting_captask", "not_found"
        """
        watch_dir = Path(self.watch_path)
        processed_dir = watch_dir / "processed"
        processed_captask_dir = watch_dir / "processedCapTask"
        waiting_captask_dir = watch_dir / "waitingCapTask"
        failed_dir = watch_dir / "failed"
        waiting_approval_dir = Path(self.waiting_approval_dir)

        # Check waiting approval (unapproved items)
        for ext in ['.json', '.yaml', '.yml']:
            if (waiting_approval_dir / f"{item_hash}{ext}").is_file():
                return "waiting_approval"

        # Check pending (in watch_path)
        for file_path in watch_dir.glob(f"{item_hash}.*"):
            if file_path.is_file():
                return "pending"

        # Check processed (Task)
        if processed_dir.exists():
            for file_path in processed_dir.glob(f"{item_hash}.*"):
                if file_path.is_file():
                    return "processed"

        # Check processed CapTask
        if processed_captask_dir.exists():
            for file_path in processed_captask_dir.glob(f"{item_hash}.*"):
                if file_path.is_file():
                    return "processed"

        # Check waiting CapTask
        if waiting_captask_dir.exists():
            for file_path in waiting_captask_dir.glob(f"{item_hash}.*"):
                if file_path.is_file():
                    return "waiting_captask"

        # Check failed
        if failed_dir.exists():
            for file_path in failed_dir.glob(f"{item_hash}.*"):
                if file_path.is_file():
                    return "failed"

        return "not_found"

    def _guess_type(self, data: dict, filename: str) -> str:
        """Guess item type from data content or filename."""
        if 'cap_name' in data and 'args' in data:
            return "CapTask"
        elif 'system_prompt' in data or 'prompt_list' in data:
            return "Task"
        return "Unknown"

    def start(self):
        """Start Uvicorn server (blocking)."""
        uvicorn.run(self.app, host=self.host, port=self.port, log_level=self.log_level)


# ---------------------------------------------------------------------
# Example usage (if run as script)
# ---------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    handler = RestFulHandler(
        watch_path="./data/file_watch",
        host="127.0.0.1",
        port=8000
    )
    handler.start()