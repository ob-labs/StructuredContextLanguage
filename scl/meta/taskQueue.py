"""
Task definition module for the Structured Context Language project
"""

from typing import Any, Optional
from scl.otel.otel import task_enqueue_counter, task_dequeue_counter
from scl.otel.otel import tracer
import queue
from opentelemetry import trace

class TaskQueue:
    """带有追踪和指标的队列包装器"""

    def __init__(self):
        """
        初始化队列处理器
        """
        self._queue = queue.Queue()

    @tracer.start_as_current_span("add task to queue")
    def add(self, item: Any) -> None:
        """
        向队列中添加一个元素，并记录追踪和指标

        :param item: 要添加的元素
        """
        current_span = trace.get_current_span()
        current_span.set_attribute("queue.item", str(item))
        self._queue.put(item)
        # 增加计数器
        task_enqueue_counter.add(1)
        # 可选：记录队列大小作为指标
        queue_size = self._queue.qsize()
        current_span.set_attribute("queue.size.after_add", queue_size)

    @tracer.start_as_current_span("get task from queue")
    def get(self, block: bool = True, timeout: Optional[float] = None) -> Any:
        """
        从队列中取出一个元素，并记录追踪和指标

        :param block: 是否阻塞等待
        :param timeout: 超时时间（秒），仅在 block=True 时有效
        :return: 取出的元素
        :raises queue.Empty: 当非阻塞且队列为空，或阻塞超时时抛出
        """
        current_span = trace.get_current_span()
        item = self._queue.get(block=block, timeout=timeout)
        current_span.set_attribute("queue.item", str(item))
        task_dequeue_counter.add(1)
        queue_size = self._queue.qsize()
        current_span.set_attribute("queue.size.after_get", queue_size)
        return item