"""
File Watcher for Todo Items
1. if the file been read, move the file into processed folder.
"""
import logging
import os
import shutil
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from scl.meta.taskQueue import TaskQueue
from scl.otel.otel import tracer

class TodoFileHandler(FileSystemEventHandler):
    def __init__(self, watch_path, queue: TaskQueue):
        self.watch_path = watch_path
        self.queue = queue
        self.logger = logging.getLogger(__name__)
        self.processed_dir = os.path.join(watch_path, "processed")
        os.makedirs(self.processed_dir, exist_ok=True)

    def on_created(self, event):
        if event.is_directory:
            return
        with tracer.start_as_current_span("file_watcher_new_file") as span:
            filepath = event.src_path
            span.set_attribute("file.path", filepath)
            self.logger.info(f"New file detected: {filepath}")

            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                item = {"source": "file", "path": filepath, "content": content}
                self.queue.add(item)
                self.logger.debug(f"File content queued: {filepath}")

                # Move to processed folder after successful read
                dest_path = os.path.join(self.processed_dir, os.path.basename(filepath))
                shutil.move(filepath, dest_path)
                span.set_attribute("file.moved_to", dest_path)
                self.logger.info(f"File moved to processed: {dest_path}")

            except Exception as e:
                self.logger.error(f"Error processing file {filepath}: {e}")
                span.record_exception(e)

    def start(self):
        """Start watchdog observer on the given path."""
        observer = Observer()
        observer.schedule(self, self.watch_path, recursive=False)
        observer.start()
        self.logger.info(f"File watcher started on {self.watch_path}")
        return observer