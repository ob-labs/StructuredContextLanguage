"""
Task definition module for the Structured Context Language project
"""

from typing import Any, Optional
import queue

class TracedQueue:
    """带有追踪和指标的队列包装器"""

    def __init__(self, tracer, meter):
        """
        初始化队列处理器

        :param tracer: OpenTelemetry Tracer 对象，用于创建 Span
        :param meter: OpenTelemetry Meter 对象，用于记录指标
        """
        self._queue = queue.Queue()
        self.tracer = tracer
        self.meter = meter
        # 创建一个计数器指标（示例）
        self.add_counter = meter.create_counter(
            "traced_queue.add.count",
            description="Number of items added to the queue"
        )
        self.get_counter = meter.create_counter(
            "traced_queue.get.count",
            description="Number of items retrieved from the queue"
        )

    def add(self, item: Any) -> None:
        """
        向队列中添加一个元素，并记录追踪和指标

        :param item: 要添加的元素
        """
        with self.tracer.start_as_current_span("traced_queue.add") as span:
            span.set_attribute("queue.item", str(item))
            self._queue.put(item)
            # 增加计数器
            self.add_counter.add(1)
            # 可选：记录队列大小作为指标
            queue_size = self._queue.qsize()
            span.set_attribute("queue.size.after_add", queue_size)

    def get(self, block: bool = True, timeout: Optional[float] = None) -> Any:
        """
        从队列中取出一个元素，并记录追踪和指标

        :param block: 是否阻塞等待
        :param timeout: 超时时间（秒），仅在 block=True 时有效
        :return: 取出的元素
        :raises queue.Empty: 当非阻塞且队列为空，或阻塞超时时抛出
        """
        with self.tracer.start_as_current_span("traced_queue.get") as span:
            item = self._queue.get(block=block, timeout=timeout)
            span.set_attribute("queue.item", str(item))
            self.get_counter.add(1)
            queue_size = self._queue.qsize()
            span.set_attribute("queue.size.after_get", queue_size)
            return item