# Web AutoTest Agent

本仓库包含 LangGraph + Deep Agents 后端，以及基于 Next.js 的 Agent Chat UI 前端。

- `web-agent/`: LangGraph 工作流、Specialist Agent、工具、测试和 `langgraph.json`。
- `web-agent/deep_agent/scheduler/`: 独立于 Agent 的定时任务扫描与执行模块，负责读取配置文件并串行执行 Playwright 测试。
- `web-poartl/`: 从 `langchain-ai/agent-chat-ui` 的 `5201e3ab6d35b22c86c1aad34acd32d1032e5279` 提交引入的 Agent Chat UI 前端。

旧的自定义 Portal REST/SSE API 和 Vite Portal 前端已删除。浏览器现在通过 `@langchain/langgraph-sdk` 直接连接 LangGraph server。

## 后端环境

```bash
cd /Users/jin/Documents/code/github/web-test-agent
uv sync --project web-agent --extra dev
```

后端配置读取 `web-agent/.env`。最少需要配置：

```bash
OPENAI_API_KEY=...
OPENAI_BASE_URL=...
MASTER_MODEL=openai:gpt-5.4
SPECIALIST_MODEL=openai:gpt-5.4
DEFAULT_AUTOMATION_PROJECT_ROOT=~/webautotest
SCHEDULER_CONFIG_PATH=~/webautotest/scheduler_tasks.json
```

不要把模型服务密钥放到 `web-poartl/.env`。

## 启动 LangGraph

推荐使用本地调试脚本：

```bash
cd /Users/jin/Documents/code/github/web-test-agent/web-agent
tests/debug/dev.sh
```

该脚本会根据 `web-agent/langgraph.json` 启动 `master` graph，优先使用 `http://127.0.0.1:2024`。

也可以直接启动 LangGraph：

```bash
cd /Users/jin/Documents/code/github/web-test-agent/web-agent
uv run --extra dev langgraph dev --host 127.0.0.1 --port 2024
```

当前 `master` graph 除了 `plan/generator/healer/general` 外，还支持一个 `scheduler` 节点：

- 该节点只负责修改 `SCHEDULER_CONFIG_PATH` 指向的已存在定时任务配置。
- 它不会直接执行测试，也不会创建新的定时任务。
- 真正的定时扫描与执行由下面的独立调度服务负责。

## 定时执行服务

示例配置文件见 [web-agent/scheduler_tasks.example.json](/Users/jin/Documents/code/github/web-test-agent/web-agent/scheduler_tasks.example.json)。

启动独立调度服务：

```bash
cd /Users/jin/Documents/code/github/web-test-agent
uv run --project web-agent web-agent-scheduler --config /absolute/path/to/scheduler_tasks.json
```

配置说明：

- 配置文件使用 JSON，按 `projects -> tasks` 组织，其他运行上下文尽量落在项目级。
- `headed=false` 表示无头执行；任务级 `headed` 可覆盖项目默认值。
- `schedule` 使用五段 Cron 表达式，例如 `0 9 * * *`。
- `locations` 为空时执行整个 Playwright 项目；非空时只执行列出的脚本或目录。
- 调度服务会在任务所属项目的测试根目录下写入 `scheduler-service.log`，默认路径是 `project_dir/test_case/scheduler-service.log`。
- 到点任务严格串行执行；如果多个任务同时到点，后续任务会进入串行队列，并在日志中记录冲突信息。

## 启动前端

```bash
cd /Users/jin/Documents/code/github/web-test-agent/web-poartl
corepack enable
corepack prepare pnpm@10.5.1 --activate
pnpm install
pnpm dev
```

前端运行地址是 `http://localhost:3000`，默认配置为：

```bash
NEXT_PUBLIC_API_URL=http://127.0.0.1:2024
NEXT_PUBLIC_ASSISTANT_ID=master
NEXT_PUBLIC_AUTH_SCHEME=
```

如果需要覆盖本地默认值，可以参考 `web-poartl/.env.example`。

## 验证

后端测试：

```bash
cd /Users/jin/Documents/code/github/web-test-agent/web-agent
uv run --extra dev pytest tests
```

前端构建：

```bash
cd /Users/jin/Documents/code/github/web-test-agent/web-poartl
pnpm build
```
