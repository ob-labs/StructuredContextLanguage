# Project Overview

## Vision

Everyone is familiar with SQL for interacting with databases. In the era of large language models, our focus is shifting from **prompt engineering** to **context engineering**.

**Structured Context Language (SCL)** aims to become a standardized approach to context engineering — occupying a niche similar to what SQL provides for databases.

Through this practice, we hope to distill a **middleware layer** that provides a standardized interface for agents, analogous to Hibernate for Java applications.

## Deconstructing Context Engineering

If we treat a prompt as a query language for LLMs, context engineering is an implementation of that query language. We deconstruct context engineering along **three independent dimensions**:

| Dimension | Description | Analogy |
|-----------|-------------|---------|
| **Business Content** | Concrete instructions tailored to specific prompts and scenarios | The "query" |
| **Tool Calling** | Various tools available to the LLM to fetch additional external data | **Spatial** expansion of information |
| **Memory Management** | In multi-turn conversations, deciding which historical content is relevant | **Temporal** expansion of information |

> Tool calling expands information in **space**; memory management expands it in **time**.

In engineering practice, memory management can be achieved through tool calls. Therefore, within context engineering, information expansion can be accomplished via a **standardized interface** and further distilled into a **standardized workflow**.

Inspired by [Claude Skills](https://docs.anthropic.com/en/docs/agents-and-tools/claude-skills)' progressive loading mechanism, SCL extends the concept: tools can progressively load so the model autonomously selects what it needs, unlike explicitly defined stored procedures in SQL.

## Design Principles

### Minimalist YOLO Mode
No built-in TODO lists, planning, sub-agents, or background processes. Developers externalize state through files, compose tools through bash, and implement skill execution by spawning new tasks. The framework does one thing — **run an agent** — and gives the user full control and observability.

### Unified Provider Interface
A single API supporting Anthropic, OpenAI, Google, xAI, Groq, Cerebras, OpenRouter, and any OpenAI-compatible endpoint. Features:
- Streaming and tool calls with TypeBox schema validation
- Reasoning/thinking support
- Seamless cross-provider context handoff
- Token and cost tracking

### Tool Registration and Selection (RAG-based)
Built-in tool registry maintaining metadata and descriptions. The agent uses a **RAG-based mechanism** to progressively load available tools, injecting only relevant tool definitions into the context when needed. This avoids context bloat and preserves model autonomy.

### Pluggable Content Compression
A hot-swappable interface for content compression strategies. During long conversations, a customizable compressor distills historical messages to reduce token consumption while retaining critical information.

### Prompt Templates
Structured template support for business content, facilitating reuse, version management, and team collaboration.

### Multiple Runtime Forms
- **RESTful (containerized)** — Deployed as a service with API access
- **Local TUI** — Interactive terminal usage with slash commands and session management
- **Library** — Direct import for secondary development

### Observability
Full OpenTelemetry integration: tool call parameters and results, incremental model outputs, and internal state changes are all traceable via traces, metrics, and structured logs.

### Built-in Toolset
Out-of-the-box tools covering common tasks:
- File read/write
- Search (grep, find)
- Bash execution
- Git operations
- Cron job management
- Extensible via registration mechanism

## Project Status

Current version: **0.1.0**

SCL is in **active development**. See [06-development.md](06-development.md) for the current roadmap and status.

## Relationships with Ecosystem

| Concept | Relation to SCL |
|---------|----------------|
| **Function Call** | A capability type - direct tool invocation |
| **MCP (Model Context Protocol)** | A capability type - standardized protocol for tool access |
| **Skill** | A capability type with progressive disclosure semantics |
| **RAG** | Core mechanism for tool selection and capability injection |
| **OpenTelemetry** | Observability foundation |
