# Getting Started

## Installation

### From Source

```bash
# Clone the repository
git clone https://github.com/Teingi/StructuredContextLanguage.git
cd StructuredContextLanguage

# Install dependencies
pip install -e .

# Optional: install extra backends
pip install -e ".[oceanbase]"   # OceanBase vector store
pip install -e ".[postgres]"    # PostgreSQL + pgvector
pip install -e ".[local]"       # Local embedding (SentenceTransformer)
pip install -e ".[dev]"         # Development tools (pytest, ruff, mypy)
```

### Using pip (future)

```bash
pip install structured-context-language
```

## Configuration

SCL is configured via **environment variables**. All settings are optional with sensible defaults.

### Core Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `TODO_WATCH_DIR` | `./todo_folder` | Directory for file-based task persistence |
| `API_HOST` | `0.0.0.0` | REST API bind address |
| `API_PORT` | `8080` | REST API port |
| `SERVICE_NAME` | `SCL` | Service name for telemetry |
| `LOG_LEVEL` | `INFO` | Logging level |

### Embedding Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | Embedding model name |
| `EMBEDDING_MODEL_DIMS` | `1024` | Embedding dimensions |
| `EMBEDDING_API_KEY` | — | API key for web embedding service |
| `EMBEDDING_BASE_URL` | `https://api.siliconflow.cn/v1` | Web embedding endpoint |
| `EMBEDDING_LOCAL_MODEL_PATH` | — | Path for local SentenceTransformer model |
| `EMBEDDING_CACHE_PATH` | — | Path for embedding cache |

### RAG / Search Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `LIMIT` | `5` | Top-k results for capability search |
| `MIN_SIMILARITY` | `0.5` | Minimum similarity threshold |

### OpenTelemetry Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `OTLP_ENDPOINT` | `http://localhost:4318` | OTLP HTTP endpoint |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | `http://localhost:4318` | Metrics endpoint |

## Quick Start

### 1. Start the SCL Service

```bash
# Set the watch directory
export TODO_WATCH_DIR=./todo_folder

# Run SCL
python main.py
```

This starts:
- A **file watcher** on `./todo_folder`
- A **REST API** server on `0.0.0.0:8080`
- The **task processor** loop

### 2. Submit a Task via REST API

```bash
curl -X POST http://localhost:8080/task \
  -H "Content-Type: application/json" \
  -d '{
    "system_prompt": "You are a helpful assistant.",
    "prompt_list": ["Hello, what can you do?"],
    "capacity": ["bash", "file_read"]
  }'
```

### 3. Submit a Task via File

Simply create a `.json` file in the watch directory:

```bash
cat > ./todo_folder/my_task.json << 'EOF'
{
  "system_prompt": "You are a helpful assistant.",
  "prompt_list": ["List files in the current directory."],
  "capacity": ["bash", "file_read", "file_write"]
}
EOF
```

The file watcher will detect the new file and enqueue it for processing.

### 4. Monitor with OpenTelemetry

If you have an OTLP collector running:

```bash
# Start a local OTLP collector (example with Docker)
docker run -p 4318:4318 otel/opentelemetry-collector-contrib

# SCL automatically sends traces, metrics, and logs
```

## Running Forms

### RESTful (Containerized)

```bash
# Using Docker
docker build -t scl .
docker run -p 8080:8080 \
  -e TODO_WATCH_DIR=/data/todo \
  -v ./data:/data \
  scl
```

### As a Library

```python
from scl.config import config
from scl.queue.task_queue import TaskQueue
from scl.processor.task_processor import TaskProcessor
from scl.listener.file_watch import FileHandler
from scl.meta.task import Task
from scl.otel.otel import init_telemetry

# Initialize telemetry
init_telemetry()

# Create queue and processor
todo_queue = TaskQueue()
processor = TaskProcessor(todo_queue)
processor.start()

# Create file watcher
watch_dir = config.todo_watch_dir
file_handler = FileHandler(watch_path=watch_dir, task_queue=todo_queue, ...)
file_observer = file_handler.start()

# Create and submit tasks directly
task = Task(
    system_prompt="You are a helpful assistant.",
    prompt_list=["Hello"],
    capacity=["bash"]
)
todo_queue.add(task)
```

## Next Steps

- Read [01-overview.md](01-overview.md) to understand the philosophy
- Explore [03-core-concepts.md](03-core-concepts.md) for detailed model documentation
- Check [05-sdk-reference.md](05-sdk-reference.md) for SDK integration patterns
- See [06-development.md](06-development.md) for the current development status
