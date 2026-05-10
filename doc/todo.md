### Describe your use case

- [ ] SCL——一个面向function call，skill，mcp的专属服务
	- [ ] 解决
		- [ ] 工具选择问题——一个专属RAG
			- [ ] 基于特定function call组合
			- [ ] 链接seekdb作为RAG
		- [ ] 工具过程？执行占用上下文问题——上下文是否回馈是否需要隔离
			- [ ] 压缩上下文
			- [ ] 是否支持甩手工具类型
		- [ ] MCP的文件传递问题？
		- [ ] 安全问题不考虑——沙箱启动方式交给商业化sevice mesh？
	- [ ] 纵向整合，横向兼容
		- [x] 分发方式
			- [x] 参数启动容器化处理——restful
			- [x] 文件目录扫描启动方式
			- [x] 支持SDK直接使用——代码使用
		- [ ] 运维方式
			- [x] 暴露可观测性指标
			- [ ] hook（websocket形式）
		- [ ] 执行过程多种模式
			- [ ] 工具注册
			- [ ] 工具注入
			- [ ] 提示词改写
			- [ ] 工具执行后脱手
			- [ ] 日志回馈
		- [ ] 支持SeekDB，PGvector（把数据库SQL注入即可）
		- [ ] 支持调试模式（要有个调试框架，最大化RAG三类查询的平衡点）
			- [ ] 其他非预设工具支持——商业化服务
	- [ ] 案例/论文
		- [ ] [目标测试集合](https://gorilla.cs.berkeley.edu/blogs/15_bfcl_v4_web_search.html)
		- [ ] RAG + 模型组合
			- [ ] BM25 + DeepSeek v4
			- [ ] Qwen embedding + Qwen跑目标测试集合
		- [ ] 测试的项目
			- [ ] RAG本身能选对不？
			- [ ] 基于历史记录能有多少改善
			- [ ] 基于RAG和历史记录
		- [ ] 指标
			- [ ] 正确率
			- [ ] 能节约多少Token（token在对话中以及tool传递的数量）

### Describe the solution you'd like

as above

### Describe alternatives you've considered

_No response_

### Additional context

_No response_


taskQueue.py?
- [ ] Queue size limit or backpressure handling.
- [ ] Batch processing support.

Cap.py?
5. [Missing] Serialization/deserialization methods (to_dict, from_dict) for persistence.
6. [Missing] Validation of function_impl code safety before sandbox execution.
7. [Missing] Versioning support for capability changes.
8. [Missing] Async support for embedding generation.

- notice refactor?

task.py as prompt template?
making service package for service?