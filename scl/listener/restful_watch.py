"""
RESTful API for receiving Todo items via POST requests
"""

import logging
from typing import Dict

import uvicorn
from fastapi import FastAPI, Request

from scl.meta.taskQueue import TracedQueue

class RestFulHandler:
    """REST API 处理器，负责接收 Todo 项并放入队列"""

    def __init__(self, todo_queue: TracedQueue, host:str = "0.0.0.0", port: int = 8000, log_level: str = "info", tracer=None, meter=None):
        """
        初始化 REST 处理器

        :param todo_queue: 队列对象，需支持 put(item) 方法
        """
        self.todo_queue = todo_queue
        self.host = host
        self.port = port
        self.log_level = log_level
        # 设置遥测（tracer 用于链路追踪，meter 用于指标）
        self.tracer= tracer
        self.meter = meter
        # 配置日志
        self.logger = logging.getLogger(self.__class__.__name__)
        # 创建 FastAPI 应用
        self.app = FastAPI(title="Todo Receiver")
        # 注册路由
        self.app.add_api_route("/todo", self._receive_todo, methods=["POST"])

    async def _receive_todo(self, request: Request) -> Dict[str, str]:
        """
        处理 POST /todo 请求的内部方法

        :param request: FastAPI 请求对象
        :return: 响应状态
        """
        with self.tracer.start_as_current_span("rest_receive_todo") as span:
            data = await request.json()
            span.set_attribute("todo.rest.data", str(data))
            self.logger.info(f"Received todo via REST: {data}")
            self.todo_queue.add({"source": "rest", "data": data})
            return {"status": "accepted"}

    def start(self):
        """
        启动 Uvicorn 服务器，监听指定端口

        :param host: 监听地址
        :param port: 监听端口
        :param log_level: 日志级别
        """
        uvicorn.run(self.app, host=self.host, port=self.port, log_level=self.log_level)
