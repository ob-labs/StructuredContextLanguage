# SDK reference
```
    todo_queue = TaskQueue()
    logger.info("Starting Todo Receiver Application")

    # Ensure watch directory exists
    watch_dir = os.getenv("TODO_WATCH_DIR", "./todo_folder")
    os.makedirs(watch_dir, exist_ok=True)

    # Start todo processor
    processor = TaskProcessor(todo_queue)
    processor.start()

    # Start listeners
    file_handler = FileHandler(watch_dir, todo_queue)
    rest_handler = RestFulHandler(todo_queue, host="0.0.0.0", port="8080")

    file_observer = file_handler.start()
    api_thread = threading.Thread(target=rest_handler.start, daemon=True)
    # Wait for termination signal
    def shutdown(signum, frame):
        logger.info("Shutting down...")
        file_observer.stop()
        file_observer.join()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    api_thread.start()

    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown(None, None)
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