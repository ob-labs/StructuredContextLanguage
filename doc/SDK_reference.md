# SDK reference
```
# Setup telemetry
logger = logging.getLogger(__name__)

# Global input queue
todo_queue = TracedQueue(tracer, meter)

# Start todo processor
processor = TaskProcessor(tracer, meter, todo_queue)
processor.start()

# Start listeners
file_handler = TodoFileHandler(watch_dir, tracer, todo_queue)
rest_handler = RestFulHandler(todo_queue, host="0.0.0.0", port="8000", tracer=tracer, meter=meter)
```

# Impl details

## ./listener

Impls for how receive data from outside and inside.

## ./meta

Internal data structure define

## ./embeddings

embedding service

## storage

storage service

## TaskProcessor

class to process task