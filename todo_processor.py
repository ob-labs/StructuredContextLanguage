"""
This module contains the TodoProcessor class, which is responsible for processing todo items.
- It will consume the TaskQueue
- It use a while true to consume items from the queue
- If the item is empty then double the wait time for the queue and the max sleep time is 300s
"""

import logging
import time
from threading import Thread
from queue import Empty

from scl.meta.taskQueue import TaskQueue
from scl.otel.otel import tracer
from opentelemetry import trace

class TodoProcessor:
    """Processes todo items from a TaskQueue with exponential backoff on empty queue."""

    def __init__(self, input_queue: TaskQueue):
        self.input_queue = input_queue
        self.logger = logging.getLogger(__name__)
        self.thread = None

    def start(self):
        """Start processing thread."""
        self.thread = Thread(target=self._process_loop, daemon=True)
        self.thread.start()

    def _process_loop(self):
        """Main loop that consumes items from queue with exponential backoff."""
        wait_time = 1.0          # 初始等待时间 1 秒
        max_wait_time = 300.0    # 最大等待时间 300 秒

        while True:
            try:
                with tracer.start_as_current_span("todo_queue_operation") as parent_span:
                    item = self.input_queue.get(timeout=wait_time)
                    # 成功获取到 item，重置等待时间
                    if item is not None:
                        wait_time = 1.0
                        self._process_item(item)
                    else:
                        wait_time = min(wait_time * 2, max_wait_time)
            except Empty:
                # 队列为空，等待时间翻倍，但不超过上限
                self.logger.debug(f"Queue empty, backing off for {wait_time}s")
                wait_time = min(wait_time * 2, max_wait_time)

            except Exception as e:
                # 其他异常记录日志，避免静默吞没
                self.logger.exception(f"Unexpected error in processing loop: {e}")
                # 发生意外错误时短暂休眠后继续，避免高频错误日志
                time.sleep(1)

    @tracer.start_as_current_span("process todo item from queue")
    def _process_item(self, item):
        """Process a single todo item."""
        current_span = trace.get_current_span()
        #source = item.get("source", "unknown")
        current_span.set_attribute("todo.item.source", item)
        self.logger.info(f"Processing todo item: {item}")
        # 模拟处理逻辑
        time.sleep(0.1)
        # Placeholder: 未来可能会生成新的 todo 并放回队列