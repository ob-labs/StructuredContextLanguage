"""
This module contains the TodoProcessor class, which is responsible for processing todo items.
- It will consume a Queue
```
from scl.meta.taskQueue import TracedQueue
```
- It's inovker's duty to ensure the Queue is thread safe.
"""

import logging
import time
from scl.meta.taskQueue import TracedQueue
from threading import Thread

class TodoProcessor:
    """Placeholder class for processing todo items."""
    def __init__(self, tracer, meter, input_queue: TracedQueue):
        self.tracer = tracer
        self.meter = meter
        self.input_queue = input_queue
        self.logger = logging.getLogger(__name__)

        # Metrics
        self.processed_counter = meter.create_counter(
            "todo_items_processed",
            description="Number of todo items processed"
        )

    def start(self):
        """Start processing thread."""
        self.thread = Thread(target=self._process_loop, daemon=True)
        self.thread.start()

    def _process_loop(self):
        """Main loop that consumes items from queue and processes them."""
        while True:
            try:
                item = self.input_queue.get(timeout=1)
                self._process_item(item)
            except Exception:
                # Queue empty or other error, continue
                pass

    def _process_item(self, item):
        """Process a single todo item."""
        with self.tracer.start_as_current_span("process_todo_item") as span:
            span.set_attribute("todo.item.source", item.get("source", "unknown"))
            self.logger.info(f"Processing todo item: {item}")
            # Simulate work
            time.sleep(0.1)
            # Placeholder: actual processing logic goes here
            self.processed_counter.add(1, {"source": item.get("source", "unknown")})
            # In future, this might generate new todo items and put them back into queue