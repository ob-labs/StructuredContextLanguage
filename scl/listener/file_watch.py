"""
File Watcher for Todo Items
"""
import logging
from scl.meta.taskQueue import TracedQueue

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class TodoFileHandler(FileSystemEventHandler):
    def __init__(self, watch_path, tracer, queue: TracedQueue):
        self.watch_path = watch_path
        self.tracer = tracer
        self.queue = queue
        self.logger = logging.getLogger(__name__)

    def on_created(self, event):
        if event.is_directory:
            return
        with self.tracer.start_as_current_span("file_watcher_new_file") as span:
            filepath = event.src_path
            span.set_attribute("file.path", filepath)
            self.logger.info(f"New file detected: {filepath}")
            # Placeholder: read file content
            try:
                with open(filepath, 'r') as f:
                    content = f.read()
                item = {"source": "file", "path": filepath, "content": content}
                self.queue.add(item)
            except Exception as e:
                self.logger.error(f"Error reading file {filepath}: {e}")

    def start(self):
        """Start watchdog observer on the given path."""
        observer = Observer()
        observer.schedule(self, self.watch_path, recursive=False)
        observer.start()
        logging.getLogger(__name__).info(f"File watcher started on {self.watch_path}")
        return observer