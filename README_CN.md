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

**面向上下文工程的标准化智能体运行时中间件。**  
SCL 致力于成为 LLM 时代的上下文工程基础设施——如同 SQL 之于数据库，Hibernate 之于 Java。

---

## 概述

上下文工程可以从三个独立维度进行解构，SCL 将它们统一到一个事件驱动的运行时中：

| 维度 | 说明 | 性质 |
|------|------|------|
| **业务内容** | 面向具体场景的提示词指令 | 即"查询本身" |
| **工具调用** | 函数调用、MCP、Skill 等获取或变更外部数据的能力 | 信息的空间扩展 |
| **记忆管理** | 多轮会话中筛选相关历史内容 | 信息的时间扩展 |

SCL 通过一套**标准化接口**处理上述三个维度，让你专注于业务逻辑而非基础设施。

---

## 功能特性

- **统一供应商接口** — 一个 API 支持 Anthropic、OpenAI、Google、xAI、Groq、OpenRouter 及任何兼容 OpenAI 的端点
- **RAG 驱动的工具选择** — 通过 BM25 + Embedding 混合搜索渐进式加载工具定义，避免上下文膨胀
- **文件优先的持久化** — 文件系统为核心，REST API 负责校验后写入文件，由文件监听器解耦处理
- **可插拔存储后端** — 文件系统、OceanBase、PostgreSQL/pgvector，统一 `StoreBase` 接口
- **复合 Embedding** — 缓存 → 本地 (SentenceTransformer) → Web API (兼容 OpenAI) 自动降级
- **可观测性** — 全链路 OpenTelemetry 埋点（调用链、指标、结构化日志）
- **内置工具集** — 文件读写、grep、bash、git、cron — 通过注册机制可扩展
- **多种运行形态** — RESTful 服务、交互式 TUI、或作为库直接引用
- **极简核心** — 不内置规划器、子智能体或后台进程；编排逻辑完全由你掌控

---

## 快速开始

```bash
# 安装
pip install -e .

# 启动服务
scl

# 通过 REST API 提交任务
curl -X POST http://localhost:8080/tasks \
  -H "Content-Type: application/json" \
  -d '{"system_prompt": "你是一个助手。", "capacity": ["bash", "file_read"]}'

# 或者直接往监听目录扔文件
echo '{"system_prompt": "你好"}' > ./todo_folder/task.json
```

详见[入门指南](doc/04-getting-started.md)。

---

## 作为库使用

```python
from scl.meta.task import Task
from scl.queue.task_queue import TaskQueue
from scl.processor.task_processor import TaskProcessor

queue = TaskQueue()
processor = TaskProcessor(queue)
processor.start()

task = Task(system_prompt="你是一个助手。")
queue.add(task)  # 自动通知处理器
```

---

## 文档

| 文档 | 说明 |
|------|------|
| [概览与设计理念](doc/01-overview.md) | 愿景、设计原则与哲学 |
| [架构](doc/02-architecture.md) | 组件分解、数据流与系统设计 |
| [核心概念](doc/03-core-concepts.md) | Task、Capability、CapRegistry、Embedding、Storage、Queue、Processor |
| [入门指南](doc/04-getting-started.md) | 安装、配置与快速上手 |
| [SDK 参考](doc/05-sdk-reference.md) | API 参考与常见使用模式 |
| [开发路线图](doc/06-development.md) | 项目状态、路线图与贡献指南 |

### 研究

- [面向 Agent 的能力自动缩放方案](doc/blog/A%20way%20to%20auto%20scaling%20capabilities%20for%20Agent.md) — 基于 RAG 的工具选择，在 BFCL、MCPToolBench++ 和 ToolE 基准上的评估

---

## 架构概览

```
监听器 (REST / 文件监听 / 内部任务)
        │
        ▼  (写入文件)
todo_folder/  ─── 文件级持久化层
        │
        ▼
  队列系统  ─── TaskQueue、CapabilityTaskQueues、等待队列
        │
        ▼
 处理器  ─── TaskProcessor、CapTaskProcessor、等待处理器
        │
        ▼
 核心服务  ─── CapRegistry (RAG)、Embedding、Storage、LLM Chat
        │
        ▼
 可观测性  ─── OpenTelemetry (调用链、指标、日志)
```

---

## 项目状态

**当前版本: 0.1.0** — 活跃开发中

| 模块 | 状态 |
|------|------|
| 核心 Agent 循环 | ✅ 稳定 |
| RAG 工具选择 | ✅ 稳定（BM25 + Embedding 混合） |
| REST 与文件监听 | ✅ 可用 |
| 存储后端 | ✅ fsstore, ⏳ OceanBase, ⏳ pgvector |
| Docker 部署 | ✅ 就绪 |
| WebSocket 钩子 | 📋 计划中 |
| 调试框架 | 📋 计划中 |

详见[开发路线图](doc/06-development.md)。

---

## 基准测试

SCL 的 RAG 能力选择在行业基准上的表现：

| 基准 | 类型 | Top-5 召回率 |
|------|------|-------------|
| BFCL (multiple) | 函数调用选择 | 95.2% |
| BFCL (parallel) | 并行函数调用 | 93.8% |
| MCPToolBench++ (single) | MCP 工具选择 | 99.6% |
| ToolE (Qwen3-Embedding) | 工具选择 | 83.7% |

详见[研究论文](doc/blog/A%20way%20to%20auto%20scaling%20capabilities%20for%20Agent.md)。

---

## 参与贡献

```bash
# 环境准备
pip install -e ".[dev]"

# 运行检查
make lint        # ruff 代码检查
make typecheck   # mypy 类型检查
make test        # pytest + 覆盖率
make check       # 全量检查

# 查看所有目标
make help
```

欢迎提交 Pull Request。新组件请保持 OpenTelemetry 埋点和结构化日志的规范。

---

## 许可证

[Apache License 2.0](LICENSE)
