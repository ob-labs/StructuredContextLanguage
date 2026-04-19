"""
RESTful API for receiving scl.meta.task items via POST requests
1. It receives scl.meta.task format as JSON body.
2. It converts the task into a Task instance and writes a file to the file_watch directory.
3. It responds with the scl.meta.task's hash value to the client if successful.

RESTful API for client to check existing task and its status.
1. It receives hash value as a path parameter.
2. It checks if the hash value exists in the file_watch directory.
3. It responds with the task status in JSON format.

Dependencies:
    fastapi, uvicorn, pyyaml, opentelemetry-api, opentelemetry-sdk

Install with:
    pip install fastapi uvicorn pyyaml opentelemetry-api opentelemetry-sdk
"""

import logging
import os
import json
import yaml
from pathlib import Path
from typing import Dict, Optional

import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

from scl.otel.otel import tracer, meter
from scl.meta.task import Task  # Assumed import; adjust to actual module

from opentelemetry import trace


class RestFulHandler:
    """REST API handler that receives Todo items, validates, converts to Task, writes to file_watch, and provides status check."""

    def __init__(self, watch_path: str, host: str = "0.0.0.0", port: int = 8000, log_level: str = "info"):
        """
        Initialize REST handler.

        :param watch_path: Directory where task files will be written (same as file watcher's watch_path)
        :param host: Binding address
        :param port: Binding port
        :param log_level: Uvicorn log level
        """
        self.watch_path = watch_path
        self.host = host
        self.port = int(port)
        self.log_level = log_level

        self.logger = logging.getLogger(self.__class__.__name__)

        # Ensure watch directory exists
        os.makedirs(self.watch_path, exist_ok=True)

        # Metrics
        self.restful_task_counter = meter.create_counter(
            "restful_task_received",
            description="Total number of REST tasks received"
        )
        self.restful_task_valid_counter = meter.create_counter(
            "restful_task_valid",
            description="Number of REST tasks successfully converted to Task objects and written to disk"
        )
        self.restful_task_invalid_counter = meter.create_counter(
            "restful_task_invalid",
            description="Number of REST tasks that failed validation/conversion"
        )
        self.status_check_counter = meter.create_counter(
            "restful_status_check",
            description="Number of status checks performed"
        )

        # FastAPI app
        self.app = FastAPI(title="Task Receiver & Status API")
        self.app.add_api_route("/tasks", self._receive_task, methods=["POST"])
        self.app.add_api_route("/tasks/{task_hash}", self._check_status, methods=["GET"])

    @tracer.start_as_current_span("rest_api_receive_task")
    async def _receive_task(self, request: Request) -> Dict[str, str]:
        """
        POST /tasks endpoint.
        Validates JSON, converts to Task, writes file to watch_path, returns hash.
        """
        current_span = trace.get_current_span()
        client_ip = request.client.host if request.client else "unknown"
        current_span.set_attribute("client.ip", client_ip)

        # 1. Parse JSON body
        try:
            data = await request.json()
        except Exception as e:
            self.logger.warning(f"Invalid JSON received from {client_ip}: {e}")
            current_span.record_exception(e)
            self.restful_task_invalid_counter.add(1)
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        self.logger.info(f"Received task payload from {client_ip}")
        self.logger.debug(f"Payload: {data}")
        self.restful_task_counter.add(1)

        # 2. Convert to Task instance
        try:
            task_obj = Task.from_dict(data)  # Assuming factory method; adjust as needed
        except Exception as e:
            self.logger.error(f"Failed to create Task object from payload: {e}")
            current_span.record_exception(e)
            self.restful_task_invalid_counter.add(1)
            raise HTTPException(status_code=422, detail="Invalid task format")

        # 3. Enrich span with task metadata
        task_hash = getattr(task_obj, 'hash', None)
        if not task_hash:
            # If Task has no hash, generate one from content or raise error
            self.logger.error("Task object missing 'hash' attribute")
            current_span.set_attribute("error", "missing_hash")
            self.restful_task_invalid_counter.add(1)
            raise HTTPException(status_code=500, detail="Task object has no hash identifier")

        current_span.set_attribute("task.hash", str(task_hash))
        current_span.set_attribute("task.type", "rest")

        # 4. Write task to file in watch_path
        file_path = self._write_task_file(task_obj, task_hash)
        current_span.set_attribute("file.path", file_path)
        self.logger.info(f"Task written to {file_path}")
        self.restful_task_valid_counter.add(1)

        return {"status": "accepted", "task_hash": task_hash}

    @tracer.start_as_current_span("rest_api_check_status")
    async def _check_status(self, task_hash: str) -> Dict[str, str]:
        """
        GET /tasks/{task_hash} endpoint.
        Checks if the task file exists and returns its processing status.
        """
        current_span = trace.get_current_span()
        current_span.set_attribute("task.hash", task_hash)
        self.status_check_counter.add(1)

        self.logger.debug(f"Status check requested for hash: {task_hash}")

        # Look for any file starting with task_hash in watch_path or processed/failed subdirs
        status = self._determine_status(task_hash)
        current_span.set_attribute("task.status", status)

        self.logger.info(f"Status for {task_hash}: {status}")
        return {"task_hash": task_hash, "status": status}

    def _write_task_file(self, task_obj: Task, task_hash: str) -> str:
        """
        Write Task instance to a file in watch_path.
        Format can be JSON or YAML; defaulting to JSON.
        """
        # Determine output format (could be configurable)
        ext = ".json"
        file_path = os.path.join(self.watch_path, f"{task_hash}{ext}")

        # Serialize task to dict (assuming Task has a to_dict method)
        task_dict = task_obj.to_dict() if hasattr(task_obj, 'to_dict') else task_obj.__dict__

        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(task_dict, f, indent=2)

        return file_path

    def _determine_status(self, task_hash: str) -> str:
        """
        Determine the status of a task by checking the presence and location of its file.
        Returns one of: "pending", "processing", "processed", "failed", "not_found"
        """
        watch_dir = Path(self.watch_path)
        processed_dir = watch_dir / "processed"
        failed_dir = watch_dir / "failed"

        # Check pending (files in watch_dir matching hash)
        for file_path in watch_dir.glob(f"{task_hash}.*"):
            if file_path.is_file():
                return "pending"

        # Check processed
        if processed_dir.exists():
            for file_path in processed_dir.glob(f"{task_hash}.*"):
                if file_path.is_file():
                    return "processed"

        # Check failed
        if failed_dir.exists():
            for file_path in failed_dir.glob(f"{task_hash}.*"):
                if file_path.is_file():
                    # Could extract failure reason from filename if needed
                    return "failed"

        return "not_found"

    def start(self):
        """Start Uvicorn server."""
        uvicorn.run(self.app, host=self.host, port=self.port, log_level=self.log_level)