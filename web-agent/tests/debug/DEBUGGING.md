# Web Agent LangGraph 调试方案

本文档用于本地调试 `web-agent` 的 LangGraph/Deep Agents 执行链路。推荐流程是：改 `.env` 调试配置，使用 `langgraph dev` 导出日志，在 LangGraph Studio 里发起对话，然后用 `tail -F` 观察日志并按关键词定位链路。

## 1. 调整配置

在 `web-agent/.env` 中临时打开深度调试日志：

```bash
LOG_LEVEL=INFO
AGENT_DEBUG_TRACE=true
AGENT_DEBUG_FULL_MESSAGES=true
AGENT_DEBUG_MAX_CHARS=12000
```

说明：

- `AGENT_DEBUG_TRACE=true`: 输出节点 state、模型调用、工具调用、Specialist 上下文等结构化事件。
- `AGENT_DEBUG_FULL_MESSAGES=true`: 输出完整 system/user/ai/tool 消息和最终 system prompt，只建议本机临时调试。
- `AGENT_DEBUG_MAX_CHARS=12000`: 控制单段 prompt、消息或工具输出写入日志时的最大字符数。

## 2. 启动并导出日志

`langgraph dev` 没有独立的 `--log-file` 参数，因此使用本目录的启动脚本把 stdout/stderr 导出到文件。
脚本会自动切到 `web-agent` 目录启动 LangGraph，并默认写入 `tests/debug/langgraph-dev.log`。
同时它会把第三方日志里的 UTC `Z` 时间戳转成本地时间，并过滤 `watchfiles.main` 的文件变更噪声。
默认会优先尝试端口 `2024`；如果该端口清理后仍不可绑定，则自动省略 `--port`，交给 `langgraph dev` 自己选择可用端口。

```bash
cd web-agent
tests/debug/dev.sh
```

可选环境变量：

```bash
PORT=2025 tests/debug/dev.sh
LOG_FILE=/tmp/langgraph-dev.log tests/debug/dev.sh
PORT_STRICT=1 tests/debug/dev.sh
```

- `PORT_STRICT=1`: 如果首选端口不可用则直接失败，不走 auto-discover 回退。

## 3. 在 Studio 发起对话

在 LangGraph Studio 里选择 `master` graph，直接发起一次对话即可。你不需要手动指定 `thread_id`，也不需要把任何 session id 传进项目。

项目会从 LangGraph 运行时自动读取 `thread_id` 并写入关键日志。日志里的 `session_id` 只是检索别名，默认等于这个 `thread_id`。

如果要定位某次对话的 `thread_id`，在日志里看第一条节点入参日志即可：

```bash
tail -F tests/debug/langgraph-dev.log
```

如果当前目录已经是 `web-agent/tests/debug`，直接运行：

```bash
tail -F langgraph-dev.log
```

日志里会出现类似字段：

```text
event=node_enter trace={'session_id': '...', 'thread_id': '...', 'run_id': ..., 'node_name': 'master_node', 'event_name': 'node_enter'}
```

复制其中的 `thread_id`，后续按它查询整条链路。

## 4. 查看日志关键词

实时观察：

```bash
tail -F tests/debug/langgraph-dev.log
```

只看某一次对话：

```bash
tail -F tests/debug/langgraph-dev.log | grep "<thread_id>"
```

关键事件关键词：

```text
node_enter
node_exit
node_error
route_decision
specialist_context
model_start
model_end
tool_start
tool_end
tool_error
planner_save_plan
deep_agent_end
```

常用组合：

```bash
tail -F tests/debug/langgraph-dev.log | grep -E "node_enter|node_exit|route_decision"
tail -F tests/debug/langgraph-dev.log | grep -E "model_start|model_end"
tail -F tests/debug/langgraph-dev.log | grep -E "tool_start|tool_end|tool_error|planner_save_plan"
tail -F tests/debug/langgraph-dev.log | grep -E "specialist_context|system_prompt|allowed_tool_names|loaded_tools"
tail -F tests/debug/langgraph-dev.log | grep -E "SystemMessage|HumanMessage|AIMessage|ToolMessage"
```

如果日志已经落盘，也可以用 `rg` 回查：

```bash
rg "<thread_id>" tests/debug/langgraph-dev.log
rg "model_start|model_end|tool_start|tool_end|node_enter|node_exit" tests/debug/langgraph-dev.log
rg "SystemMessage|HumanMessage|AIMessage|ToolMessage|planner_save_plan" tests/debug/langgraph-dev.log
```

## 5. 字段含义

- `thread_id`: LangGraph 自动创建或携带的 thread id，用于串联一次或多轮对话。
- `session_id`: 日志检索别名，默认等于 `thread_id`。
- `run_id`: 如果 LangGraph runtime config 中存在则打印；本地调试不需要手动传入。
- `node_name`: 当前节点，例如 `master_node`、`plan_node`。
- `event_name`: 当前事件，例如 `node_enter`、`model_start`、`tool_end`、`planner_save_plan`。

调试结束后建议把 `.env` 改回：

```bash
AGENT_DEBUG_TRACE=false
AGENT_DEBUG_FULL_MESSAGES=false
AGENT_DEBUG_MAX_CHARS=4000
```
