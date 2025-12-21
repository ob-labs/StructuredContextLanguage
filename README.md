# Structured Context Language

## Overview
People are familiar with SQL (Structured Query Language), which is used to interact with databases. Today, as we face Large Language Models (LLMs), the focus is shifting from prompt engineering to context engineering.

In this repository, we aim to build a Structured Context Language (SCL) to occupy a niche analogous to SQL, drawing inspiration from context engineering practices.

We hope that through this effort, we can distill a middleware solution. This middleware would provide a standard interface for AI agents, much like Hibernate serves as a standard ORM interface for Java applications.

## Deconstructing SCL

If we consider prompts as a query language for Large Language Models (LLM), then context engineering is undoubtedly an implementation of this query language. We can deconstruct context engineering along three independent dimensions:

- Business Content: Specific instructions for particular prompts and scenarios.
- Tool Invocation: Various tools the LLM can use to obtain additional external data.
- Memory Management: In multi-turn conversation scenarios, determining which historical content is relevant to the current query.

> We can view tool invocation as a spatial expansion of information and memory management as an expansion of information along the temporal dimension.

Considering that in engineering practice, we can implement interactions for memory management through tool invocation, the extended querying of information within context engineering can therefore be accomplished using a standardized interface and further summarized into a standardized workflow.

Inspired by the progressive loading mechanism of Claude Skill, we have also observed that autonomous selection of tools by the LLM can be achieved through progressive loading across different tools. Unlike stored procedures in SQL, which are defined and explicitly called for execution, progressive loading provides an additional layer of autonomy.

## Use case
> The Autonomy Slider —— Reference Karpathy's speech on Software 3.0. Show me the diff in vivid.

```
Configurable + Autonomy by LLM via feedback control
Autonomy by LLM via feedback control(metric or history)
Autonomy by LLM
Configurable
HardCode
```

- [ ] Should we make a middleware just input as prompt and output as result?(Autonomy)
- [ ] We provides workflow and let people able to config it.(Configurable)
- [ ] We provides sdk let people implements their own.(Hardcode)

- [x] Obversbility —— otel.
> 
```
docker run -p 8000:8000 -p 4317:4317 -p 4318:4318 ghcr.io/ctrlspice/otel-desktop-viewer:latest-amd64
export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4317"
export OTEL_TRACES_EXPORTER="otlp"
export OTEL_EXPORTER_OTLP_PROTOCOL="grpc"
```

- [ ] Function selction.
   - [x] "Progressive loading" base on RAG. (Autonomy)
   - [ ] Hard code memory tool invoke. (Autonomy or defualt? tbd)
   - [x] Hardcode control by human, as index hint for SQL.

- [ ] File format Autonomy, took PDF format as example.
    - [ ] Context auto into markdown.(Autonomy)
    - [ ] Context auto embedding for RAG.(Autonomy)
    - [ ] Or Hardcode control by human outside our process.

- [ ] Content Autonomy.
    - [ ] RAG support by default.(Autonomy)
    - [ ] Hard code as input prompt content.(Hardcode control by human)
```
for EMBEDDING service, using siliconflow fow now as poc
export EMBEDDING_API_KEY=<your_siliconflow_api_key>
```

```
docker run -d --name pgvector -e POSTGRES_PASSWORD=postgres -e POSTGRES_USER=postgres -e POSTGRES_DB=postgres -p 5432:5432 ankane/pgvector:v0.5.1
``

## todo
Article/Blog
Investigating how to reuse powermom?
Find some agent bench mark for testing.
 