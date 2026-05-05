# Web AutoTest Agent 调试文档

本仓库包含 LangGraph + Deep Agents 后端，以及基于 Next.js 的 Agent Chat UI 前端。

- `web-agent/`: LangGraph 工作流、Specialist Agent、工具、测试和 `langgraph.json`
- `web-agent/deep_agent/scheduler/`: 独立于 Agent 的定时任务扫描与执行模块，负责读取配置文件并串行执行 Playwright 测试
- `web-poartl/`: Agent Chat UI 前端

本目录用于本地一次性启动后端 LangGraph 服务和前端 Next.js 服务，并把日志直接写到 `test/` 根目录。后续调试、环境配置、调度服务和验证说明都只维护在这一个文档里。

## 启动前准备

首次启动或依赖变化后，先安装依赖：

```bash
cd /Users/jin/Documents/code/github/web-test-agent
uv sync --project web-agent --extra dev

cd /Users/jin/Documents/code/github/web-test-agent/web-poartl
corepack enable
corepack prepare pnpm@10.5.1 --activate
pnpm install
```

后端配置读取：

```text
/Users/jin/Documents/code/github/web-test-agent/web-agent/.env
```

最少需要包含：

```bash
OPENAI_API_KEY=...
OPENAI_BASE_URL=...
MASTER_MODEL=openai:gpt-5.4
SPECIALIST_MODEL=openai:gpt-5.4
DEFAULT_AUTOMATION_PROJECT_ROOT=~/webautotest
SCHEDULER_CONFIG_PATH=~/webautotest/scheduler_tasks.json
```

不要把模型服务密钥放到 `web-poartl/.env`。

## 后端单独启动

也可以直接启动 LangGraph：

```bash
cd /Users/jin/Documents/code/github/web-test-agent/web-agent
uv run --extra dev langgraph dev --host 127.0.0.1 --port 2024
```

如果只想调试后端、并保留 `web-agent/tests/debug/` 下的过滤日志工具，可以继续使用：

```bash
cd /Users/jin/Documents/code/github/web-test-agent/web-agent
tests/debug/dev.sh
```

当前 `master` graph 除了 `plan / generator / healer / general` 外，还支持一个 `scheduler` 节点：

- 该节点只负责修改 `SCHEDULER_CONFIG_PATH` 指向的已存在定时任务配置
- 它不会直接执行测试，也不会创建新的定时任务
- 真正的定时扫描与执行由下面的独立调度服务负责

## 一键启动

```bash
cd /Users/jin/Documents/code/github/web-test-agent
test/dev.sh
```

脚本会先检查并关闭相关端口上的旧监听进程：

- 后端：`127.0.0.1:2024`
- 前端：`127.0.0.1:3000`

同时会先尝试停止其他仍在运行的 `test/dev.sh` 进程，然后再清理固定端口上的旧监听进程。

然后按固定命令直接启动：

- 后端：`web-agent/.venv/bin/langgraph dev --host 127.0.0.1 --port 2024 --no-browser --no-reload`
- 前端：`pnpm exec next dev --hostname 127.0.0.1 --port 3000`

启动成功后脚本会默认打开：

```text
http://127.0.0.1:3000/?chatHistoryOpen=true
```

## 日志

脚本会生成这些日志文件：

```text
test/backend.log
test/frontend.log
```

`backend.log` 保留原始 `langgraph dev` 输出，不再做过滤或裁剪。
脚本不会再创建任何 `*.pid` 文件。

实时查看：

```bash
tail -F /Users/jin/Documents/code/github/web-test-agent/test/backend.log
tail -F /Users/jin/Documents/code/github/web-test-agent/test/frontend.log
```

## 前端单独启动

```bash
cd /Users/jin/Documents/code/github/web-test-agent/web-poartl
corepack enable
corepack prepare pnpm@10.5.1 --activate
pnpm install
pnpm dev
```

前端运行地址：

```text
http://127.0.0.1:3000
```

默认配置：

```bash
NEXT_PUBLIC_API_URL=http://127.0.0.1:2024
NEXT_PUBLIC_ASSISTANT_ID=master
NEXT_PUBLIC_AUTH_SCHEME=
```

如果需要覆盖本地默认值，可以参考 `web-poartl/.env.example`。

## 定时执行服务

示例配置文件见 [web-agent/scheduler_tasks.example.json](/Users/jin/Documents/code/github/web-test-agent/web-agent/scheduler_tasks.example.json)。

启动独立调度服务：

```bash
cd /Users/jin/Documents/code/github/web-test-agent
uv run --project web-agent web-agent-scheduler --config /absolute/path/to/scheduler_tasks.json
```

配置说明：

- 配置文件使用 JSON，按 `projects -> tasks` 组织，其他运行上下文尽量落在项目级
- `headed=false` 表示无头执行；任务级 `headed` 可覆盖项目默认值
- `schedule` 使用五段 Cron 表达式，例如 `0 9 * * *`
- `locations` 为空时执行整个 Playwright 项目；非空时只执行列出的脚本或目录
- 调度服务会在任务所属项目的测试根目录下写入 `scheduler-service.log`，默认路径是 `project_dir/test_case/scheduler-service.log`
- 到点任务严格串行执行；如果多个任务同时到点，后续任务会进入串行队列，并在日志中记录冲突信息

## 常用参数

```bash
NO_RELOAD=0 test/dev.sh
OPEN_BROWSER=0 test/dev.sh
FRONTEND_OPEN_URL=http://127.0.0.1:3000/?chatHistoryOpen=true test/dev.sh
SERVER_LOG_LEVEL=ERROR test/dev.sh
STARTUP_WAIT_SECONDS=60 test/dev.sh
```

默认会关闭 LangGraph 热加载，并禁止 `langgraph dev` 自动打开 LangSmith Studio。
端口固定为 `2024` 和 `3000`。如果清理其他 `dev.sh` 进程和监听进程后端口仍不可绑定，脚本会直接失败并提示当前监听进程。

## 停止服务

在 `test/dev.sh` 运行窗口按 `Ctrl+C`，脚本会同时停止前端、后端以及日志 tail 进程。

如果是在后台运行，可以先查出 `test/dev.sh` 的进程号，再手动停止：

```bash
pgrep -af "/Users/jin/Documents/code/github/web-test-agent/test/dev.sh"
kill <pid>
```

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
