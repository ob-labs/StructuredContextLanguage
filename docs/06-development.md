# Development

## Project Status

Current version: **0.1.0** — Active development phase.

## Development Setup

```bash
# Clone and install
git clone https://github.com/Teingi/StructuredContextLanguage.git
cd StructuredContextLanguage
pip install -e ".[dev]"

# Run tests
pytest

# Lint and type check
ruff check .
mypy scl/
```

## Development Roadmap

### Vision: SCL as a dedicated service for Function Call, Skill, and MCP

- **Tool Selection** — A dedicated RAG system for capability discovery
  - [x] BM25-based keyword search
  - [x] Embedding-based semantic search
  - [x] Hybrid scoring (BM25 + Embedding linear combination)
  - [ ] Connect to SeekDB as RAG backend
  - [ ] Debug framework to optimize RAG triple-query balance

- **Context Isolation & Compression**
  - [ ] Compress context to reduce token usage
  - [ ] Support fire-and-forget tool execution (detached from context)

- **Integration & Compatibility**
  - [x] RESTful API (containerized deployment)
  - [x] File directory watch (local/disk-based)
  - [x] SDK for direct code usage
  - [ ] MCP file transfer support
  - [ ] WebSocket hooks for real-time events
  - [ ] Support SeekDB, PGvector (SQL-injectable)

- **Runtime Modes**
  - [ ] Tool registration
  - [ ] Tool injection
  - [ ] Prompt rewriting
  - [ ] Post-execution detachment
  - [ ] Log feedback loop

- **Security**
  - Sandbox execution — defer to commercial service mesh

### Benchmark Evaluation (see [research](blog/A%20way%20to%20auto%20scaling%20capabilities%20for%20Agent.md))

- [x] BFCL test suite (irrelevance, live, parallel, multiple)
- [x] MCPToolBench++ evaluation
- [x] MetaTool / ToolE evaluation
- [ ] RAG + Model combination experiments:
  - [ ] BM25 + DeepSeek v4
  - [ ] Qwen embedding + Qwen
- [ ] Metrics tracking:
  - [ ] Accuracy / recall
  - [ ] Token savings in conversation and tool delivery

### Code Quality

- [ ] Queue size limit and backpressure handling
- [ ] Batch processing support
- [ ] Capability serialization/deserialization (`to_dict`, `from_dict`)
- [ ] Versioning support for capability changes
- [ ] Async support for embedding generation
- [ ] Validation of `function_impl` code safety before sandbox execution

## Project Structure

```
StructuredContextLanguage/
├── main.py                    # Application entry point
├── scl/                       # Core library
│   ├── cap_reg.py             # Capability Registry
│   ├── config.py              # Configuration
│   ├── llm_chat.py            # LLM interaction orchestration
│   ├── capabilities/          # Built-in tools
│   ├── embeddings/            # Embedding backends
│   ├── listener/              # Input listeners
│   ├── meta/                  # Data models
│   ├── otel/                  # Observability
│   ├── processor/             # Task processors
│   ├── queue/                 # Queue implementations
│   └── storage/               # Storage backends
├── tests/                     # Test suite
├── example/                   # Example code and benchmarks
│   ├── BFCL/                  # BFCL benchmark scripts
│   ├── mcptool/               # MCPTool benchmark scripts
│   └── MetaTool/              # MetaTool benchmark scripts
├── docs/                      # Documentation
├── Dockerfile                 # Container support
├── docker-compose.yml         # Orchestration
├── prometheus.yml             # Prometheus config for metrics
└── pyproject.toml             # Project metadata
```

## Contributing

### Guidelines

1. **Run tests before submitting**: `pytest`
2. **Follow linting rules**: `ruff check .`
3. **Add type hints**: all new code should have proper type annotations
4. **Add OpenTelemetry instrumentation**: traces and metrics for new components
5. **Write documentation**: update relevant docs for API changes

### Code Style

- Python ≥ 3.11 with type hints
- Line length: 100 characters
- OpenTelemetry instrumentation by default
- Structured logging at INFO and DEBUG levels
- Every status change / business action should have:
  - A trace span (`@tracer.start_as_current_span`)
  - A log record (`logger.info/debug`)
  - A metric update (`counter.add()`)

## Releases

- Versioning follows `scl/__init__.py` (`__version__`)
- CHANGELOG is maintained in commit history
- See [pyproject.toml](../pyproject.toml) for project metadata
