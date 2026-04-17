"""
RESTful API for receiving scl.meta.task items via POST requests
1. it receives scl.meta.task format as JSON body
2. it converts the task into a Task instance and puts it into queue.
"""

import logging
from typing import Dict

import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

from scl.otel.otel import tracer, meter
from scl.meta.taskQueue import TaskQueue
from scl.meta.task import Task  # Assumed import; adjust to actual module

from opentelemetry import trace


class RestFulHandler:
    """REST API handler that receives Todo items, validates, converts to Task, and queues."""

    def __init__(self, todo_queue: TaskQueue, host: str = "0.0.0.0", port: int = 8000, log_level: str = "info"):
        """
        Initialize REST handler.

        :param todo_queue: Queue object supporting add(item) method
        :param host: Binding address
        :param port: Binding port
        :param log_level: Uvicorn log level
        """
        self.todo_queue = todo_queue
        self.host = host
        self.port = int(port)
        self.log_level = log_level

        self.logger = logging.getLogger(self.__class__.__name__)

        # Metrics
        self.restful_task_counter = meter.create_counter(
            "restful_task_received",
            description="Total number of REST tasks received"
        )
        self.restful_task_valid_counter = meter.create_counter(
            "restful_task_valid",
            description="Number of REST tasks successfully converted to Task objects"
        )
        self.restful_task_invalid_counter = meter.create_counter(
            "restful_task_invalid",
            description="Number of REST tasks that failed validation/conversion"
        )

        # FastAPI app
        self.app = FastAPI(title="Todo Receiver")
        self.app.add_api_route("/todo", self._receive_todo, methods=["POST"])

    @tracer.start_as_current_span("rest_api_receive_task")
    async def _receive_todo(self, request: Request) -> Dict[str, str]:
        """
        POST /todo endpoint. Validates JSON, converts to Task, and queues.
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
        if task_hash:
            current_span.set_attribute("task.hash", str(task_hash))
        current_span.set_attribute("task.type", "rest")

        # 4. Add to queue
        try:
            self.todo_queue.add(task_obj)
            self.logger.info(f"Task {task_hash} queued successfully")
            self.restful_task_valid_counter.add(1)
        except Exception as e:
            self.logger.error(f"Failed to queue task {task_hash}: {e}")
            current_span.record_exception(e)
            raise HTTPException(status_code=500, detail="Queue unavailable")

        return {"status": "accepted", "task_id": task_hash}

    def start(self):
        """Start Uvicorn server."""
        uvicorn.run(self.app, host=self.host, port=self.port, log_level=self.log_level)