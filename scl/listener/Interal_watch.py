"""
Internal Watcher for Todo Items
"""

import logging
from scl.meta.taskQueue import TracedQueue

class InternalWatcher:
    def __init__(self, todo_queue: TracedQueue):
        self.todo_queue = todo_queue
        self.logger = logging.getLogger(__name__)

    def add(self, item):
        self.logger.debug(f"Internally generated todo: {item}")
        self.todo_queue.add(item)
            
    