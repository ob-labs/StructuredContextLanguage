<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://img.shields.io/badge/SCL-v0.1.0-blue?style=for-the-badge&labelColor=1a1a2e">
  <img alt="SCL v0.1.0" src="https://img.shields.io/badge/SCL-v0.1.0-blue?style=for-the-badge">
</picture>
<br>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://img.shields.io/badge/python-≥3.11-3776AB?style=flat&logo=python&logoColor=white">
  <img alt="Python ≥3.11" src="https://img.shields.io/badge/python-≥3.11-3776AB?style=flat&logo=python&logoColor=white">
</picture>
<a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-green?style=flat" alt="License"></a>
<a href="https://github.com/Teingi/StructuredContextLanguage/actions/workflows/test.yaml"><img src="https://github.com/Teingi/StructuredContextLanguage/actions/workflows/test.yaml/badge.svg" alt="CI"></a>

# Structured Context Language (SCL)

**A standardized agent loop runtime for context engineering.**  
SCL is a middleware layer for LLM-powered agents — analogous to what SQL is for databases and Hibernate is for Java.

---

## Overview

Context engineering decomposes LLM interaction into three independent dimensions, unified into one event-driven runtime:

| Dimension | Description | Nature |
|-----------|-------------|--------|
| **Business Content** | Instructions tailored to specific prompts and scenarios | The "query" |
| **Tool Calling** | Functions, MCP, and skills that fetch or mutate external data | Spatial expansion |
| **Memory Management** | Selecting relevant history from multi-turn conversations | Temporal expansion |

SCL provides a pluggable, observable agent loop that handles all three dimensions through a **standardized interface** — so you focus on your business logic, not the plumbing.

---

## Features

- **Unified Provider Interface** — Anthropic, OpenAI, Google, xAI, Groq, OpenRouter, and any OpenAI-compatible endpoint via a single API
- **RAG-based Tool Selection** — Progressively loads relevant tool definitions using BM25 + embedding hybrid search, avoiding context bloat
- **File-first Persistence** — File system as the backbone; REST API validates and delegates to file watcher for decoupling
- **Pluggable Storage** — File system, OceanBase, and PostgreSQL/pgvector backends via a common `StoreBase` interface
- **Composite Embedding** — Cache → local (SentenceTransformer) → web API (OpenAI-compatible) with automatic fallback
- **Observability** — Full OpenTelemetry instrumentation (traces, metrics, structured logs) across all components
- **Built-in Toolset** — File read/write, grep, bash, git, cron — extensible via capability registration
- **Multiple Runtime Forms** — RESTful service, interactive TUI, or direct library import
- **Minimalist Core** — No built-in planners, sub-agents, or background processes; you control the orchestration

---

## Quick Start

```bash
# Install
pip install -e .

# Start the service
scl

# Submit a task via REST API
curl -X POST http://localhost:8080/tasks \
  -H "Content-Type: application/json" \
  -d '{"system_prompt": "You are a helpful assistant.", "capacity": ["bash", "file_read"]}'

# Or drop a file into the watch directory
echo '{"system_prompt": "Hello"}' > ./todo_folder/task.json
```

See the [Getting Started Guide](docs/04-getting-started.md) for full instructions.

---

## Use as a Library

```python
from scl.meta.task import Task
from scl.queue.task_queue import TaskQueue
from scl.processor.task_processor import TaskProcessor

queue = TaskQueue()
processor = TaskProcessor(queue)
processor.start()

task = Task(system_prompt="You are a helpful assistant.")
queue.add(task)  # auto-notifies the processor
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [Overview & Philosophy](docs/01-overview.md) | Vision, design principles, and philosophy |
| [Architecture](docs/02-architecture.md) | Component breakdown, data flow, and system design |
| [Core Concepts](docs/03-core-concepts.md) | Task, Capability, CapRegistry, Embedding, Storage, Queues, Processors |
| [Getting Started](docs/04-getting-started.md) | Installation, configuration, and quick start |
| [SDK Reference](docs/05-sdk-reference.md) | API reference and common usage patterns |
| [Development Roadmap](docs/06-development.md) | Status, roadmap, and contributing |

### Research

- [A Way to Auto-Scaling Capabilities for Agents](docs/blog/A%20way%20to%20auto%20scaling%20capabilities%20for%20Agent.md) — RAG-based tool selection evaluated against BFCL, MCPToolBench++, and ToolE benchmarks

---

## Architecture in Brief

```
Listeners (REST / File Watch / Internal)
        │
        ▼  (write files)
todo_folder/  ─── file-based persistence layer
        │
        ▼
  Queue System  ─── TaskQueue, CapabilityTaskQueues, Awaiting Queues
        │
        ▼
 Processors  ─── TaskProcessor, CapTaskProcessor, Awaiting Processors
        │
        ▼
 Core Services  ─── CapRegistry (RAG), Embedding, Storage, LLM Chat
        │
        ▼
 Observability  ─── OpenTelemetry (traces, metrics, logs)
```

---

## Project Status

**Current version: 0.1.0** — Active development.

| Area | Status |
|------|--------|
| Core agent loop | ✅ Stable |
| RAG tool selection | ✅ Stable (BM25 + Embedding hybrid) |
| REST & file watch | ✅ Operational |
| Storage backends | ✅ fsstore, ⏳ OceanBase, ⏳ pgvector |
| Docker deployment | ✅ Ready |
| WebSocket hooks | 📋 Planned |
| Debug framework | 📋 Planned |

See [Development Roadmap](docs/06-development.md).

---

## Benchmarks

SCL's RAG-based capability selection has been evaluated against industry benchmarks:

| Benchmark | Type | Top-5 Recall |
|-----------|------|-------------|
| BFCL (multiple) | Function call selection | 95.2% |
| BFCL (parallel) | Parallel function calls | 93.8% |
| MCPToolBench++ (single) | MCP tool selection | 99.6% |
| ToolE (Qwen3-Embedding) | Tool selection | 83.7% |

Details in the [research blog](docs/blog/A%20way%20to%20auto%20scaling%20capabilities%20for%20Agent.md).

---

## Contributing

```bash
# Setup
pip install -e ".[dev]"

# Run checks
make lint        # ruff linter
make typecheck   # mypy
make test        # pytest with coverage
make check       # all of the above

# See all targets
make help
```

Pull requests are welcome. Please maintain OpenTelemetry instrumentation and structured logging for new components.

---

## License

[Apache License 2.0](LICENSE)
