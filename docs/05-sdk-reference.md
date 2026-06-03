# SDK Reference

## Package Overview

```
scl/                          # Root package
├── __init__.py               # Package init, exports __version__
├── config.py                 # Configuration (dataclass, env-based)
├── cap_reg.py                # Capability Registry (CapRegistry)
├── llm_chat.py               # LLM chat orchestration
├── capabilities/             # Built-in tools
│   ├── bash.py
│   ├── fileread.py
│   ├── filewrite.py
│   ├── git.py
│   └── grep.py
├── embeddings/               # Embedding service
│   ├── base_embedding.py     # Abstract base
│   ├── embedding.py          # CompositeEmbedding (singleton)
│   ├── embedding_cache.py    # Persistent cache
│   ├── local_embedding.py    # SentenceTransformer backend
│   └── web_embedding.py      # OpenAI-compatible API backend
├── listener/                 # Input listeners
│   ├── file_watch.py         # File system watcher
│   ├── restful_watch.py      # REST API (FastAPI)
│   └── internal_watch.py     # Internal task generation
├── meta/                     # Data models
│   ├── task.py               # Task entity
│   ├── captask.py            # Capability task
│   ├── capability.py         # Abstract capability base
│   ├── functioncall.py       # Function call capability
│   ├── skill.py              # Skill capability
│   ├── msg.py                # Message with embedding
│   └── skills_ref/           # Skill file parser
├── otel/                     # Observability
│   ├── init.py               # Telemetry init
│   ├── otel.py               # Tracer, meter, metrics
│   ├── traces.py             # Span utilities
│   ├── metrics.py            # Metric definitions
│   └── metric_decorator.py   # @record_latency decorator
├── processor/                # Task processors
│   ├── base_queue_processor.py
│   ├── task_processor.py
│   ├── cap_task_processor.py
│   ├── await_cap_tasks_processor.py
│   └── awaiting_approve_processor.py
├── queue/                    # Queue implementations
│   ├── task_queue.py
│   ├── cap_task_queues.py
│   ├── awaiting_cap_tasks_queue.py
│   └── awaiting_approve_queue.py
└── storage/                  # Storage backends
    ├── base.py               # StoreBase (abstract)
    ├── fsstore.py            # Filesystem store
    ├── oceanbasestore.py     # OceanBase store (optional)
    └── pgstore.py            # PostgreSQL + pgvector (optional)
```

## Quick API Reference

### Configuration

```python
from scl.config import config

# Access configuration (all env-based)
watch_dir = config.todo_watch_dir
api_port = config.api_port
limit = config.limit

# Validate
config.validate()
```

### Task

```python
from scl.meta.task import Task
from scl.meta.captask import CapTask

# Create a task
task = Task(
    system_prompt="You are a helpful assistant.",
    prompt_list=["User: Hello"],
    capacity=["bash", "file_read"],
    status="created",
    approval=True,
)

# Add subtask
child = Task(system_prompt="Subtask")
task.add_subtask(child)

# Add CapTask
cap = CapTask(cap_name="send_email", args=["user@example.com", "Subject"])
task.add_cap_task(cap)

# Serialize
json_str = task.to_json(indent=2)
yaml_str = task.to_yaml()

# Deserialize
restored = Task.from_json(json_str)

# Check latest status (LRU)
latest = task.get_latest_status()
```

### Capability

```python
from scl.meta.capability import Capability
from scl.meta.skill import Skill
from scl.meta.functioncall import FunctionCall

# Create a skill
skill = Skill(
    name="web_search",
    type="skill",
    description="Search the web for information",
)

# Create a function call
func = FunctionCall(
    name="calculate",
    description="Calculate mathematical expression",
    function_impl="def calculate(expr): return eval(expr)",
)

# Access embedding (lazy, triggers computation)
vector = skill.embedding_description

# Execute
result = func.execute({"expr": "2+2"})
```

### Message

```python
from scl.meta.msg import Msg

# Create from messages list
msg = Msg([{"role": "user", "content": "Hello"}])

# Access properties
messages = msg.messages
embedding = msg.embed

# Append tool results
msg.append_cap_result("tool output", "call_123")
```

### Capability Registry

```python
from scl.cap_reg import CapRegistry
from scl.storage.fsstore import fsstore

# Initialize storage and registry
store = fsstore(path="./skills", init=True, embedding_service_on=True)
registry = CapRegistry(store)

# Search by names
caps = registry.getCapsByNames(["bash", "file_read"])

# Semantic search (RAG)
msg = Msg([{"role": "user", "content": "I need to read a file"}])
relevant = registry.getCapsBySimilarity(msg, limit=5, min_similarity=0.5)

# History-based search
historical = registry.getCapsByHistory(msg, limit=5)

# Record usage
registry.record(msg, cap)
```

### Embedding

```python
from scl.embeddings.embedding import embed

# Get embedding for any text
vector = embed("What is the capital of France?")

# Also access the singleton client
from scl.embeddings.embedding import get_embedding_client
client = get_embedding_client()
vector = client.embed("Some text")
```

### LLM Chat

```python
from scl.llm_chat import send_messages, function_call_playground
from scl.meta.msg import Msg

# Requires an OpenAI-compatible client
response = send_messages(
    client=openai_client,
    model="gpt-4",
    cap_registry=registry,
    ToolNames=["bash"],
    msg=Msg([{"role": "user", "content": "List files"}]),
    Turns=0,
)

# Full function call loop
result = function_call_playground(
    client=openai_client,
    model="gpt-4",
    cap_registry=registry,
    ToolNames=["bash"],
    msg=Msg([{"role": "user", "content": "List files"}]),
)
```

### Storage Backend

```python
from scl.storage.base import StoreBase

# Custom backend
class MyStore(StoreBase):
    def get_cap_by_name(self, name):
        # ...
    def search_by_similarity(self, msg, limit, min_similarity):
        # ...
    def record(self, msg, cap):
        # ...
    def getCapsByHistory(self, msg, limit, min_similarity):
        # ...
    def insert_capability(self, cap):
        # ...
```

### Queues and Processors

```python
from scl.queue.task_queue import TaskQueue
from scl.processor.task_processor import TaskProcessor

# Queue
queue = TaskQueue()
queue.add(task)
item = queue.get()

# Processor
processor = TaskProcessor(input_queue=queue, name="my_processor")
processor.start()
# ... later ...
processor.stop()
```

### Telemetry

```python
from scl.otel.otel import init_telemetry, tracer, meter

# Initialize OpenTelemetry
init_telemetry()

# Use tracer and meter for custom instrumentation
@tracer.start_as_current_span("my_function")
def my_function():
    counter = meter.create_counter("my_counter", description="...")
    counter.add(1)
```

## Usage Patterns

### Pattern 1: Task-Driven Agent Loop

```python
# Setup telemetry
from scl.otel.otel import init_telemetry
init_telemetry()

def main():
    # Initialize queues
    todo_queue = TaskQueue()
    captask_queue = CapabilityTaskQueues()
    waiting_approval_queue = AwaitingApproveQueue()
    waiting_captask_queue = AwaitingCapTasksQueue()

    # Start processors
    processor = TaskProcessor(todo_queue)
    processor.start()

    # Start listeners
    watch_dir = config.todo_watch_dir
    file_handler = FileHandler(
        watch_path=watch_dir,
        task_queue=todo_queue,
        captask_queue=captask_queue,
        waiting_approval_queue=waiting_approval_queue,
        waiting_captask_queue=waiting_captask_queue,
    )
    rest_handler = RestFulHandler(watch_dir, host=config.api_host, port=config.api_port)

    file_observer = file_handler.start()
    api_thread = threading.Thread(target=rest_handler.start, daemon=True)
    api_thread.start()

    # Wait for shutdown signal
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
```

### Pattern 2: Direct SDK Integration

```python
from scl.config import config
from scl.queue.task_queue import TaskQueue
from scl.processor.task_processor import TaskProcessor
from scl.meta.task import Task

# Minimal setup
queue = TaskQueue()
processor = TaskProcessor(queue)
processor.start()

# Submit work
task = Task(system_prompt="You are a helpful assistant.")
queue.add(task)

# Cleanup
processor.stop()
processor.join()
```
