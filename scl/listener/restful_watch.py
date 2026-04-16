"""
RESTful API for receiving Todo items via POST requests
"""

import logging
from typing import Dict

import uvicorn
from fastapi import FastAPI, Request
from scl.otel.otel import tracer,meter
from scl.meta.taskQueue import TaskQueue
from opentelemetry import trace

class RestFulHandler:
    """REST API 处理器，负责接收 Todo 项并放入队列"""

    def __init__(self, todo_queue: TaskQueue, host:str = "0.0.0.0", port: int = 8000, log_level: str = "info"):
        """
        初始化 REST 处理器

        :param todo_queue: 队列对象，需支持 put(item) 方法
        """
        self.todo_queue = todo_queue
        self.host = host
        self.port = int(port)
        self.log_level = log_level
        # 配置日志
        self.logger = logging.getLogger(self.__class__.__name__)
        # 创建 FastAPI 应用
        self.app = FastAPI(title="Todo Receiver")
        self.restful_task_counter = meter.create_counter(
            "restful_task_add",
            description="Number of items added to the restful task queue"
        )
        # 注册路由
        self.app.add_api_route("/todo", self._receive_todo, methods=["POST"])

    @tracer.start_as_current_span("rest api receive task")
    async def _receive_todo(self, request: Request) -> Dict[str, str]:
        """
        处理 POST /todo 请求的内部方法

        :param request: FastAPI 请求对象
        :return: 响应状态
        """
        current_span = trace.get_current_span()
        data = await request.json()
        current_span.set_attribute("todo.rest.data", str(data))
        self.logger.info(f"Received todo via REST: {data}")
        self.todo_queue.add({"source": "rest", "data": data})
        self.restful_task_counter.add(1)
        return {"status": "accepted"}

    def start(self):
        """
        启动 Uvicorn 服务器，监听指定端口

        :param host: 监听地址
        :param port: 监听端口
        :param log_level: 日志级别
        """
        uvicorn.run(self.app, host=self.host, port=self.port, log_level=self.log_level)
