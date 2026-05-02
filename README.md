# Structured Context Language (SCL)

## Vision

Everyone is familiar with SQL for interacting with databases. In the era of large language models, our focus is shifting from prompt engineering to context engineering.

In this project, we aim to build a Structured Context Language (SCL), drawing on the practices of context engineering to occupy a niche similar to that of SQL.

Through this practice, we hope to distill a middleware layer. This middleware will provide a standardized interface for agents, playing a role analogous to Hibernate for Java applications.

## Deconstructing SCL

If we treat a prompt as a query language for large language models (LLMs), then context engineering is certainly one implementation of that query language. We can deconstruct context engineering along three independent dimensions:

- **Business content**: Concrete instructions tailored to specific prompts and scenarios.
- **Tool calling**: Various tools available to the LLM to fetch additional external data.
- **Memory management**: In multi-turn conversations, deciding which historical content is relevant to the current query.

> We can view tool calling as a spatial expansion of information, and memory management as a temporal expansion of information.

In engineering practice, we can manage memory through tool calls. Therefore, within context engineering, the expansion of information can be accomplished via a standardized interface, which can be further distilled into a standardized workflow.

Inspired by the progressive loading mechanism of Claude Skills, we also see that between different tools, progressive loading can allow the model to autonomously select tools. Unlike stored procedures that are explicitly defined and called in SQL, this provides extra autonomy through progressive loading.

## SCL Agent Loop: A Unified Agent Runtime

Building on the above ideas, SCL provides a standardized agent loop as the runtime middleware for context engineering. It unifies the processing of these three dimensions into an event-driven execution model.

### Design Principles

- **Minimalist YOLO Mode**  
  No built-in TODO lists, planning, sub-agents, or background bash processes. Developers externalize state through files, compose tools through bash, and implement skill execution by spawning new tasks. We want the framework to do one thing——run an agent——and give the user full control and observability.

- **Unified Provider Interface**  
  A single API supporting Anthropic, OpenAI, Google, xAI, Groq, Cerebras, OpenRouter, and any OpenAI-compatible endpoint. Provides streaming, tool calls with TypeBox schema validation, reasoning/thinking support, seamless cross-provider context handoff, and token and cost tracking.

- **Tool Registration and Selection**  
  Built-in tool registry that maintains tool metadata and descriptions. The agent uses a RAG-based mechanism to progressively load available tools, injecting only the relevant tool definitions into the context when needed. This avoids context bloat from full tool descriptions and preserves model autonomy while respecting progressive loading.

- **Pluggable Content Compression**  
  A hot-swappable interface for content compression strategies. During long conversations, a customizable compressor can distill historical messages to reduce token consumption while retaining critical information, improving stability for long-running agents.

- **Prompt Templates**  
  Structured template support for business content, facilitating reuse, version management, and team collaboration. Templates can incorporate proven prompt patterns from context engineering practice.

- **Multiple Runtime Forms**  
  - **RESTful (containerized)** — Deployed as a service, providing API access.  
  - **Local TUI** — Interactive terminal usage with slash commands and session management.  
  - **Library** — Directly imported for secondary development and deep integration.

- **Observability**  
  Transparent event streaming across the entire workflow: tool call parameters and results, every incremental model output, and internal state changes are all traceable. This is essential for debugging, evaluation, and building trust.

- **Built-in Toolset**  
  Out-of-the-box tools covering common coding and operations tasks:
  - File read/write
  - Search (grep, find)
  - Bash
  - Git
  - Create cron jobs
  - Other tools available for function call branching

  Tools can be extended through the registration mechanism, supporting both native implementations and CLI-wrapped commands.

### How It Reflects SCL’s Philosophy

- **Unified Information Expansion**  
  Both tool calling (spatial expansion) and memory management (temporal expansion) are handled within the agent loop through the same standardized tool interface. Memory management is no longer a special internal mechanism; it is invoked, recorded, and observed like any other tool.

- **Progressive Loading Engineered**  
  The RAG-based tool selection extends the progressive loading concept from “skill files” to all tools. The model first understands the need, then fetches tool descriptions on demand, and autonomously decides which resources to use. This balances context efficiency with autonomy.

- **Middleware Positioning**  
  Just as Hibernate provides a standard abstraction for persistence in Java applications, the SCL Agent Loop aims to provide a standardized interface for context management in agent applications. It encapsulates provider differences, tool execution, compression strategies, and runtime modes, allowing developers to focus on business prompts and task-flow design.
