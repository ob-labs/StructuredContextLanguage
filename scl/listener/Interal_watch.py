"""
Internal Watcher for Todo Items
"""

import logging
from scl.meta.taskQueue import TaskQueue
from scl.otel.otel import tracer,meter

class InternalWatcher:
    def __init__(self, todo_queue: TaskQueue):
        self.todo_queue = todo_queue
        self.logger = logging.getLogger(__name__)
        self.internal_task_counter = meter.create_counter(
            "internal_task_add",
            description="Number of items added to the internal task queue"
        )

    @tracer.start_as_current_span("add task from internal method")
    def add(self, item):
        self.logger.debug(f"Internally generated todo: {item}")
        self.todo_queue.add(item)
        self.internal_task_counter.add(1)
            
    