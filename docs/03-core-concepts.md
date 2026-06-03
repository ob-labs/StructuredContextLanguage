# Core Concepts

## 1. Task (`scl/meta/task.py`)

A `Task` is the fundamental unit of work in SCL. It represents a user request or system operation.

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `system_prompt` | `str` | System prompt for the LLM |
| `prompt_list` | `list[str]` | Prompt history (conversation context) |
| `capacity` | `list[str]` | Required capability identifiers |
| `status` | `str` | One of `"created"`, `"subtasking"`, `"done"` |
| `approval` | `bool` | Approval flag; defaults to `True` |
| `additional` | `dict[str,str]` | Extension data key-value store |
| `previous_hash` | `str\|None` | Hash of predecessor (hash chain) |
| `hash` | `str` | SHA-256 of `(system_prompt, prompt_list, capacity)` |
| `sub_tasks` | `list[Task]` | Child tasks (hierarchy support) |
| `cap_tasks` | `list[CapTask]` | Associated capability invocation tasks |
| `created_at` / `updated_at` | `datetime` | Timestamps for LRU ordering |

### Hash Chain

Tasks form a hash chain via `previous_hash`, enabling traceability back to the root task. The chain is verified through parent-child relationships.

```python
# Creating a task chain
root = Task(system_prompt="Root task")
child = Task(system_prompt="Child task")
root.add_subtask(child)
# child.previous_hash automatically set to root.hash
is_valid = child.verify_hash_chain()
```

### LRU Status View

`get_latest_status()` walks the task hierarchy and returns the status of the most recently updated node (self or any descendant), providing a quick "freshest" state view.

### Serialization

Tasks support JSON and YAML serialization:

```python
json_str = task.to_json(indent=2)
restored = Task.from_json(json_str)

yaml_str = task.to_yaml()          # requires PyYAML
restored = Task.from_yaml(yaml_str)
```

---

## 2. Capability (`scl/meta/capability.py`)

`Capability` is an **abstract base class** for all tool-like entities in SCL, encompassing both Skills and Function Calls.

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `name` | `str` | Unique name of the capability |
| `type` | `str` | Implementation type (`"skill"`, `"function"`, etc.) |
| `description` | `str\|None` | Human-readable description for RAG/progressive loading |
| `original_body` | `str\|None` | Original source/body of the capability |
| `llm_description` | `str\|None` | LLM-formatted description for tool injection |
| `function_impl` | `str\|None` | Code implementation for sandbox execution |
| `embedding_description` | `Any` | **Lazy-loaded** embedding vector for RAG search |

### Embedding Computation

The `embedding_description` property computes and caches the embedding lazily (first access triggers computation). This avoids unnecessary embedding calls for capabilities that are registered but never queried.

### Abstract Method

```python
class Capability(ABC):
    @abstractmethod
    def execute(self, args_dict: dict[str, Any]) -> Any: ...
```

### Concrete Implementations

| Class | File | Description |
|-------|------|-------------|
| **Skill** | `scl/meta/skill.py` | Progressive disclosure skill with `.skill` file parsing |
| **FunctionCall** | `scl/meta/functioncall.py` | Direct function call capability |

---

## 3. CapTask (`scl/meta/captask.py`)

A `CapTask` represents a **single invocation** of a capability. When created, it automatically writes a JSON file to the `todo_watch_dir`, making it visible to the file watcher.

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `cap_name` | `str` | Name of the capability to invoke |
| `args` | `list[Any]` | Arguments for the capability call |
| `hash` | `str` | Auto-generated UUID identifier |
| `task_hash` | `str\|None` | Parent task hash |
| `approval` | `bool` | Approval flag (default `True`) |
| `status` | `str` | One of `"created"`, `"Processed"`, `"Error"` |
| `full_result` | `str` | Complete output from capability invocation |
| `result` | `str` | **First 500 lines** of `full_result` (property) |

```python
task = CapTask(cap_name="send_email", args=["user@example.com", "Hello!"])
task.full_result = "Sent successfully\n..."
task.set_status("Processed")
```

---

## 4. Capability Registry (`scl/cap_reg.py`)

The `CapRegistry` is the central hub for capability discovery and management. It wraps any `StoreBase` implementation to provide three retrieval strategies:

### Retrieval Modes

```
User Query
    │
    ├──► getCapsByNames()     — Exact name lookup
    │     (for explicitly specified tools)
    │
    ├──► getCapsBySimilarity() — Semantic search (RAG)
    │     (BM25 + Embedding hybrid scoring)
    │
    └──► getCapsByHistory()   — Usage history suggestion
          (collaborative filtering; stub in fsstore)
```

### How Tools Are Selected (in `llm_chat.py`)

When processing a new user query (turn 0), the system:

1. Fetches **named tools** explicitly provided
2. Performs **semantic search** to find relevant tools via RAG
3. Queries **usage history** for contextual suggestions
4. **Merges** all results (named tools take precedence)
5. Injects merged tools into the LLM context
6. Records which capabilities were actually used

```python
# Pseudocode of the selection flow
tools_named = cap_registry.getCapsByNames(ToolNames)
tools_autonomy = cap_registry.getCapsBySimilarity(msg, limit, min_similarity)
tools_history = cap_registry.getCapsByHistory(msg, limit, min_similarity)

tools_merged = {**tools_named, **tools_autonomy, **tools_history}
```

---

## 5. Embedding System (`scl/embeddings/`)

The embedding system provides a **composite** interface with priority fallback:

```
embed(text)
    │
    1. Check cache ──────────────────► hit? → return cached vector
    │
    2. Try local (SentenceTransformer) ──► success? → return vector
    │                                        fail? → fall through
    │
    3. Web API (OpenAI-compatible) ──► return vector, cache it
```

The singleton `CompositeEmbedding` ensures consistent backend selection across the application.

```python
from scl.embeddings.embedding import embed
vector = embed("What is the capital of France?")
```

### Supported Backends

| Backend | Requirements | Priority |
|---------|-------------|----------|
| Cache | (built-in) | 1st |
| Local (SentenceTransformer) | `pip install sentence-transformers`, set `EMBEDDING_LOCAL_MODEL_PATH` | 2nd |
| Web API (OpenAI-compatible) | Set `EMBEDDING_API_KEY` and `EMBEDDING_BASE_URL` | 3rd |

---

## 6. Storage Backends (`scl/storage/`)

SCL provides a pluggable storage architecture via the `StoreBase` abstract interface.

### StoreBase Interface

```python
class StoreBase(ABC):
    def get_cap_by_name(name: str) -> Capability: ...
    def search_by_similarity(msg: Msg, limit, min_similarity) -> dict[str, Capability]: ...
    def record(msg: Msg, cap: Capability) -> None: ...
    def getCapsByHistory(msg: Msg, limit, min_similarity) -> dict[str, Capability]: ...
    def insert_capability(cap: Capability) -> None: ...
```

### FileSystem Store (`fsstore`)

The primary backend. Key capabilities:

- **BM25 indexing** via `rank-bm25` for keyword search
- **Embedding similarity** via cosine similarity for semantic search
- **5 hybrid scoring methods** combining BM25 and embedding:
  1. `minmax` — Min-max normalized BM25 + embedding
  2. `sigmoid` — Sigmoid BM25 + embedding
  3. `tanh` — Tanh BM25 + embedding
  4. `minmax_sigmoid` — Min-max BM25 + sigmoid BM25
  5. `minmax_tanh` — Min-max BM25 + tanh BM25
- **Duplicate detection** with configurable similarity threshold
- **Pickle cache** for fast startup

```python
store = fsstore(path="/path/to/capabilities", init=True, embedding_service_on=True)

# Combined search
results = store.search_by_similarity(
    query, limit=5, min_similarity=0.3,
    combine_method="minmax", alpha=0.7
)
```

### Vector Database Backends (Optional)

| Backend | Install | Description |
|---------|---------|-------------|
| OceanBase | `pip install scl[oceanbase]` | OceanBase vector store |
| PostgreSQL + pgvector | `pip install scl[postgres]` | PG vector store |

---

## 7. Queue System (`scl/queue/`)

Four queues manage task lifecycle in an event-driven model:

```
Incoming Task
    │
    ▼
┌──────────────┐     ┌──────────────────┐
│  TaskQueue   │────►│  TaskProcessor   │
│ (main queue) │     │ (consumes tasks) │
└──────────────┘     └────────┬─────────┘
                              │
                    Creates CapTasks (if LLM calls tools)
                              │
                              ▼
                    ┌──────────────────┐
                    │ CapabilityTask   │
                    │ Queues           │  ← Hash map for parallel execution
                    └────────┬─────────┘
                             │
                    ┌────────▼────────┐    ┌──────────────────┐
                    │ AwaitingCapTasks│───►│ AwaitingCapTasks │
                    │ Queue (heap)    │    │ Processor        │
                    └─────────────────┘    └──────────────────┘
                    
                    ┌──────────────────┐   ┌──────────────────┐
                    │ AwaitingApprove  │──►│ AwaitingApprove  │
                    │ Queue            │   │ Processor        │
                    └──────────────────┘   └──────────────────┘
```

### Design Rationale

As described in [blog/Story.md](blog/Story.md), different data structures serve different queue needs:

| Queue | Structure | Why |
|-------|-----------|-----|
| TaskQueue | FIFO Queue | Sequential task processing |
| CapabilityTaskQueues | Hash Map | Different capabilities can execute in parallel |
| AwaitingCapTasks | Heap | Ordered by number of pending caps (highest first) |
| AwaitingApprove | Queue | Human approval needed |

---

## 8. Processor System (`scl/processor/`)

All processors extend `BaseQueueProcessor` which provides:

### BaseQueueProcessor Features

- **Infinite loop** with configurable polling interval
- **Exponential backoff** on empty queue (min → max sleep interval)
- **Status management** (`idle`, `running`, `stopped`)
- **Notification mechanism** — wake-up signal when new items arrive
- **Thread-safe lifecycle** — `start()`, `stop()`, `join()`
- **OpenTelemetry instrumentation** — traces, metrics, structured logging

```python
class MyProcessor(BaseQueueProcessor):
    def _get_item(self):          # Fetch one item (non-blocking)
    def _process_item(self, item): # Business logic
```

---

## 9. Message (`scl/meta/msg.py`)

`Msg` wraps LLM conversation messages and provides embedding for RAG search:

```python
class Msg:
    messages: list   # List of message dicts (role/content)
    embed: list      # Embedding vector of the messages

    def append(context)              # Add a message to the conversation
    def append_cap_result(out, id)   # Add a tool result
```

---

## 10. Built-in Capabilities (`scl/capabilities/`)

Pre-installed tools available out of the box:

| Tool | File | Module Name |
|------|------|-------------|
| Bash | `bash.py` | `scl.capabilities.bash` |
| File Read | `fileread.py` | `scl.capabilities.fileread` |
| File Write | `filewrite.py` | `scl.capabilities.filewrite` |
| Git | `git.py` | `scl.capabilities.git` |
| Grep/Search | `grep.py` | `scl.capabilities.grep` |

Tools are registered via the capability registry and can be extended through the same mechanism.
