# System Architecture

## High-Level Architecture

SCL follows an **event-driven architecture** with file-system-based persistence as its backbone. The system can be divided into several layers:

```
 ┌─────────────────────────────────────────────────────────┐
 │                      Entry Point                         │
 │                     main.py / CLI                        │
 └──────────┬──────────────────────┬──────────────────────┘
            │                      │
     ┌──────▼──────┐        ┌─────▼──────┐
     │  Listeners  │        │  REST API  │
     │ (File Watch)│        │ (FastAPI)  │
     └──────┬──────┘        └─────┬──────┘
            │                     │
            └─────────┬───────────┘
                      │ (write files)
              ┌───────▼────────┐
              │  todo_folder/  │  ← File-based persistence layer
              │  (watch dir)   │
              └───────┬────────┘
                      │
              ┌───────▼──────────────────────┐
              │       Queue System            │
              │  ┌────────┐ ┌──────────────┐  │
              │  │TaskQueue│ │CapTaskQueues  │  │
              │  └───┬────┘ └──────┬───────┘  │
              │  ┌───▼────┐ ┌──────▼───────┐  │
              │  │Awaiting│ │Awaiting       │  │
              │  │Caps    │ │Approvals      │  │
              │  └────────┘ └──────────────┘  │
              └───────┬──────────────────────┘
                      │
              ┌───────▼──────────────────────┐
              │      Processor System         │
              │  ┌──────────┐ ┌────────────┐  │
              │  │Task      │ │CapTask      │  │
              │  │Processor │ │Processor    │  │
              │  └──────────┘ └────────────┘  │
              │  ┌──────────┐ ┌────────────┐  │
              │  │Awaiting  │ │Awaiting    │  │
              │  │Caps      │ │Approve     │  │
              │  │Processor │ │Processor   │  │
              │  └──────────┘ └────────────┘  │
              └───────┬──────────────────────┘
                      │
              ┌───────▼──────────────────────┐
              │      Core Services            │
              │  ┌────────────┐ ┌──────────┐  │
              │  │ CapRegistry│ │Embedding  │  │
              │  │ (RAG)     │ │Service    │  │
              │  └────────────┘ └──────────┘  │
              │  ┌────────────┐ ┌──────────┐  │
              │  │ Storage    │ │ LLM Chat │  │
              │  │ Backends   │ │ Provider │  │
              │  └────────────┘ └──────────┘  │
              └───────┬──────────────────────┘
                      │
              ┌───────▼──────┐
              │ Observability │
              │ (OpenTelemetry)│
              └──────────────┘
```

## Component Breakdown

### 1. Listeners (`scl/listener/`)

The entry points for data ingestion. SCL supports three input channels:

| Listener | File | Description |
|----------|------|-------------|
| **File Watch** | `file_watch.py` | Watches a directory (`todo_folder`) for new/modified files; the **1st class citizen** |
| **REST API** | `restful_watch.py` | FastAPI-based RESTful interface for external API access |
| **Internal Watch** | `internal_watch.py` | Handles tasks generated internally during processing |

**Design philosophy:** File listener is the 1st class citizen. REST API validates incoming data and writes it as a file, leaving processing to the file listener. Internal tasks also create files. This ensures persistence and decoupling.

### 2. Queue System (`scl/queue/`)

Manages task lifecycle through multiple queues:

| Queue | File | Purpose |
|-------|------|---------|
| **TaskQueue** | `task_queue.py` | Main queue for Task instances; thread-safe; notifies registered processors |
| **CapabilityTaskQueues** | `cap_task_queues.py` | Hash-map-based queue for capability tasks (parallel execution) |
| **AwaitingCapTasksQueue** | `awaiting_cap_tasks_queue.py` | Heap-ordered queue for tasks blocked waiting for capability results |
| **AwaitingApproveQueue** | `awaiting_approve_queue.py` | Queue for tasks waiting human approval |

### 3. Processor System (`scl/processor/`)

Consumes tasks from queues with exponential backoff:

| Processor | File | Consumes From |
|-----------|------|---------------|
| **TaskProcessor** | `task_processor.py` | TaskQueue |
| **CapTaskProcessor** | `cap_task_processor.py` | CapabilityTaskQueues |
| **AwaitingCapTasksProcessor** | `await_cap_tasks_processor.py` | AwaitingCapTasksQueue |
| **AwaitingApproveProcessor** | `awaiting_approve_processor.py` | AwaitingApproveQueue |
| **BaseQueueProcessor** | `base_queue_processor.py` | Abstract base with backoff/notify/status |

All processors inherit from `BaseQueueProcessor`, which provides:
- Infinite processing loop with configurable polling
- Exponential backoff on empty queues
- Thread-safe start/stop/join lifecycle
- Wake-up notification mechanism

### 4. Core Services

#### Capability Registry (`scl/cap_reg.py`)

The central `CapRegistry` class manages capability lifecycle:
- **Name-based retrieval** — `getCapsByNames()` / `get_cap_by_name()`
- **Semantic search (RAG)** — `getCapsBySimilarity()` using BM25 + Embedding
- **History-based suggestion** — `getCapsByHistory()` (stub; future enhancement)
- **Usage recording** — `record()` for collaborative filtering style recommendations

#### Embedding Service (`scl/embeddings/`)

A composite embedding system with priority fallback:
1. Cache check (persistent JSON)
2. Local embedding (SentenceTransformer)
3. Web API (OpenAI-compatible, defaults to SiliconFlow)

| Component | File | Description |
|-----------|------|-------------|
| CompositeEmbedding | `embedding.py` | Singleton coordinator, priority-based selection |
| LocalEmbeddingClient | `local_embedding.py` | SentenceTransformer-based local inference |
| WebEmbeddingClient | `web_embedding.py` | OpenAI-compatible API client |
| EmbeddingCache | `embedding_cache.py` | Persistent cache for computed embeddings |
| BaseEmbedding | `base_embedding.py` | Abstract base class for embedding backends |

#### Storage Backends (`scl/storage/`)

Pluggable storage via `StoreBase` abstract interface:

| Backend | File | Description |
|---------|------|-------------|
| **StoreBase** (abstract) | `base.py` | Defines the uniform interface |
| **FileSystem Store** | `fsstore.py` | File-based capability storage with BM25 + embedding similarity search |
| **OceanBase Store** | `oceanbasestore.py` | OceanBase vector store backend (optional, requires `[oceanbase]`) |
| **PostgreSQL Store** | `pgstore.py` | PostgreSQL + pgvector backend (optional, requires `[postgres]`) |

The **FileSystem Store** (`fsstore`) is the primary implementation, featuring:
- Directory-based capability loading from property files
- BM25 indexing via `rank-bm25`
- Embedding similarity search
- 5 combination strategies for hybrid scoring (minmax, sigmoid, tanh, etc.)
- Pickle-based cache persistence
- Duplicate detection with similarity threshold

#### LLM Chat Provider (`scl/llm_chat.py`)

The `send_messages()` function orchestrates:
1. Named tool lookup
2. Semantic tool search (autonomy sidecar)
3. History-based tool suggestion
4. Tool merging and deduplication
5. LLM invocation with merged tools
6. Tool call result processing and recording

#### Capabilities (`scl/capabilities/`)

Built-in tool implementations:

| Capability | File | Description |
|------------|------|-------------|
| Bash | `bash.py` | Shell command execution |
| File Read | `fileread.py` | Read file contents |
| File Write | `filewrite.py` | Write content to files |
| Git | `git.py` | Git operations |
| Grep | `grep.py` | Text search |

### 5. Meta Models (`scl/meta/`)

Core data structures:

| Model | File | Description |
|-------|------|-------------|
| **Task** | `task.py` | Main task entity with prompt, capacity, status, hash chain, subtasks |
| **Capability** (abstract) | `capability.py` | Abstract base for Skill and FunctionCall |
| **Skill** | `skill.py` | Progressive disclosure skill implementation |
| **FunctionCall** | `functioncall.py` | Direct function call implementation |
| **CapTask** | `captask.py` | Invocation task for a specific capability |
| **Msg** | `msg.py` | Message wrapper with embedding |
| **Skills Reference** | `skills_ref/` | Parser, models, and error handling for skill configurations |

### 6. Observability (`scl/otel/`)

Full OpenTelemetry instrumentation:

| Component | File | Description |
|-----------|------|-------------|
| Init | `init.py` | Telemetry initialization |
| Core | `otel.py` | Tracer, meter, and shared metric definitions |
| Traces | `traces.py` | Span management utilities |
| Metrics | `metrics.py` | Metric definitions |
| Metric Decorator | `metric_decorator.py` | `@record_latency` decorator |

## Data Flow

```
1. Input arrives via one of the Listeners (REST, file watch, internal)
2. Listener writes data as a file to the todo_watch_dir
3. FileWatcher detects new file and enqueues a Task to TaskQueue
4. TaskProcessor consumes the Task and processes it
5. During processing, LLM Chat determines which capabilities to invoke
6. CapRegistry performs RAG-based tool selection (BM25 + Embedding)
7. Selected capabilities are merged and sent to the LLM
8. LLM responds with tool calls → CapTasks are created
9. CapTasks are queued in CapabilityTaskQueues
10. CapTaskProcessor executes capabilities in parallel
11. Results flow back through the system
```

## Configuration

SCL uses environment-variable-based configuration via the `Config` dataclass (`scl/config.py`). See [04-getting-started.md](04-getting-started.md) for details.

## System Requirements

- Python ≥ 3.11
- OpenTelemetry-compatible collector (optional, for observability)
- SentenceTransformer (optional, for local embedding)
- PostgreSQL with pgvector or OceanBase (optional, for vector storage)
