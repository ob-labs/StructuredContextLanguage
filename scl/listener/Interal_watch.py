"""
Internal Watcher for Todo Items
"""

import logging
from scl.meta.taskQueue import TaskQueue
from scl.otel.otel import tracer

class InternalWatcher:
    def __init__(self, todo_queue: TaskQueue):
        self.todo_queue = todo_queue
        self.logger = logging.getLogger(__name__)

    @tracer.start_as_current_span("add task from internal method")
    def add(self, item):
        self.logger.debug(f"Internally generated todo: {item}")
        self.todo_queue.add(item)
            
    