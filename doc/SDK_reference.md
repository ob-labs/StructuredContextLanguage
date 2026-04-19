# SDK reference
```
# Setup telemetry
logger = logging.getLogger(__name__)
from scl.otel.otel import init_telemetry
init_telemetry()

def main():
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
    rest_handler = RestFulHandler(watch_dir, host="0.0.0.0", port="8080")

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

if __name__ == "__main__":
    main()
```

# Impl details

## ./embeddings package

embedding service

## ./listener package

Impls for how receive data from outside and inside.
Note: That file listener always be 1st class citizen, so that everything keep on disk by design.

## ./meta package

Internal data structure define

## ./otel package

otel support and service, for usage:
```
Reference coding rules:
OTEL:
import logging
from scl.otel.otel import tracer,meter
from opentelemetry import trace

class example:
    def __init__(self):
        self.some_counter = meter.create_counter(
            "business",
            description="business"
        )
        self.logger = logging.getLogger(__name__)

    @tracer.start_as_current_span("function...")
    def function(self...)
        current_span = trace.get_current_span()
        ##update span....
        ##business impls(either status change or invoke other packages)
        self.logger.debug("debug msg for business impls")
        self.some_counter.add(1) # metric changes
```

## processor package

Internal processor task impls

## queue pacakge

Internal queue system impls

## storage

storage service

## config.py

class to handle configuration