# Web AutoTest Agent 后端

`web-agent/` 是 Web AutoTest Agent 的 Python/LangGraph 后端。它负责理解测试需求、编排 Plan / Generator / Healer 和 Scheduler 配置阶段、管理阶段产物，并提供独立 Scheduler 服务执行定时 Playwright 任务。桌面客户端通过 LangGraph Server 连接后端，Assistant ID 为 `web-autotest-agent`。跨模块总览见 [仓库 README](../README.md)。

## 功能和架构

主工作流从 `deep_agent/app.py` 加载，由 `deep_agent/web_autotest_agent_workflow.py` 编译。

同一入口还暴露无状态 `web-autotest-thread-title` 图，用 Master 模型为旧历史首个明确目标生成短标题。主工作流的新会话标题直接复用首轮意图分类结果，不增加额外模型调用；标题一旦写入 state，后续轮次不自动覆盖。

```text
用户请求
  -> Master 识别意图、抽取参数、缺参中断/恢复
     -> Plan 探索页面并实际保存 Markdown 测试计划
        -> Generator 读取计划并实际保存 Playwright .spec.ts
           -> Healer 执行测试、修改失败脚本并复测
     -> Scheduler 创建或更新定时任务配置，不直接执行测试
  -> 每个 Specialist 完成后由共享 Finalizer 独立整理该阶段结果
```

各 Specialist 通过独立 Playwright Test MCP 会话运行，并在阶段结束时交叉检查 MCP 结果、真实文件落盘和 Playwright 进程/摘要，不把单一工具返回值当作成功依据。一个会话的阶段产物记录在 LangGraph state 中，后续阶段可自动继承最近计划或脚本。

主要目录：

```text
web-agent/
├── deep_agent/
│   ├── agent/               # Master、Plan、Generator、Healer、Scheduler Agent
│   ├── core/                # 配置、取消、日志和本地运行时
│   ├── helpers/             # 阶段产物、摘要和 Specialist 公共逻辑
│   ├── model/               # 模型配置、能力和适配层
│   ├── scheduler/           # Cron、队列、执行器、总结和报告
│   ├── tools/               # MCP 通用管理和 Playwright 集成
│   └── assets/demo/         # 打包进 wheel 的 Playwright 工程模板
├── tests/                    # pytest 测试
├── langgraph.json            # LangGraph Server 图配置
├── scheduler_tasks.example.json
├── pyproject.toml
└── uv.lock
```

## 环境要求

- Python 3.11+
- uv
- Node.js 22+
- npm / npx
- Playwright Chromium

使用仓库一键启动脚本时，脚本会同步 Python 依赖、检查 Node.js 并根据 `START_INSTALL_PLAYWRIGHT_BROWSERS` 准备 Chromium。

## 配置

从仓库根目录创建本地配置：

```bash
cp web-agent/.env.example web-agent/.env
```

`web-agent/.env` 只用于本地，不得提交 API Key。Windows 便携包使用解压根目录下的 `config/.env`；运行时也可通过 `WEB_TEST_AGENT_ENV_FILE` 显式指定配置文件。完整模板见 [.env.example](.env.example)。

常用配置如下，完整示例以 `.env.example` 为准。

| 分组 | 变量 | 用途 |
| --- | --- | --- |
| 模型 | `MASTER_LLM__*` | Master 的 family、channel、model、API Key、Base URL 和 thinking |
| 模型 | `SPECIALIST_LLM__*` | Plan、Generator、Healer 共用的独立六字段配置 |
| 运行时 | `MAX_CONVERSATION_TURNS` | Master 对话保留轮数 |
| 运行时 | `LLM_TIMEOUT_SECONDS` | 单次模型调用超时秒数 |
| 运行时 | `SPECIALIST_RECURSION_LIMIT` | Specialist LangGraph 递归步数上限 |
| 启动 | `BACKEND_JOBS_PER_WORKER` | 源码启动时每个 LangGraph worker 的并发任务上限，默认 `4`，必须为正整数 |
| Playwright | `PWTEST_HEADED` | MCP 浏览器是否有头运行 |
| Playwright | `PLAYWRIGHT_BOOTSTRAP_WORKSPACE` | 是否自动补齐目标工程的 npm/Playwright 依赖 |
| Playwright | `PLAYWRIGHT_TEST_PACKAGE` | 自动安装的 Playwright Test 规格，当前支持 `@playwright/test@1.61.1` |
| 目录 | `DEFAULT_AUTOMATION_PROJECT_ROOT` | 自动化工程根目录，默认 `~/webautotest` |
| Scheduler | `SCHEDULER_CONFIG_PATH` | 调度 JSON 路径；留空时为 `web-agent/scheduler_tasks.json` |
| Scheduler | `SCHEDULER_POLL_INTERVAL_SECONDS` | 服务默认轮询间隔，配置文件中的同名字段可覆盖 |
| Scheduler | `SCHEDULER_LANGGRAPH_URL` | 创建只读监控对话并执行 scheduled-run 图的 API 地址，默认 `http://127.0.0.1:2024` |
| Scheduler | `SCHEDULER_MONITOR_HEARTBEAT_SECONDS` | 有新输出时发布监控心跳的最小间隔，默认 30 秒 |
| Scheduler | `SCHEDULER_AUTO_HEAL_ENABLED` | 是否允许高置信测试自动化问题调用一次 Healer，默认开启 |
| Scheduler | `SCHEDULER_AUTO_HEAL_CONFIDENCE_THRESHOLD` | 自动修复最低归因置信度，默认 `0.8` |
| 日志 | `LOG_LEVEL` | Python 日志级别 |
| 调试 | `AGENT_DEBUG_TRACE` / `AGENT_DEBUG_FULL_MESSAGES` | 本地深度日志；后者可包含敏感对话，仅用于受控调试 |
| LangSmith | `LANGSMITH_API_KEY` / `LANGSMITH_PROJECT` / `LANGSMITH_TRACING` | 可选的运行轨迹上报 |

模型配置使用 Pydantic 的 `__` 分隔符。Master 与 Specialist 不会互相继承或回退；两组的 `FAMILY`、`CHANNEL` 和 `MODEL` 都必须显式填写，`API_KEY` 和 `BASE_URL` 可按模型服务要求留空。`MODEL` 只填服务端的真实模型 ID，不支持 `openai:` 或 `anthropic:` 前缀。

`FAMILY` 可选 `openai`、`qwen`、`minimax`、`glm`、`generic`；`CHANNEL` 可选 `openai`、`dashscope_openai`、`minimax_openai`、`minimax_anthropic`、`zhipu_openai`、`generic_openai`、`generic_anthropic`，且必须与 family 匹配。`THINKING` 可选 `auto`、`enabled`、`disabled`，默认为 `auto`。例如：

```dotenv
MASTER_LLM__FAMILY=generic
MASTER_LLM__CHANNEL=generic_openai
MASTER_LLM__MODEL=gpt-5.6-terra
MASTER_LLM__API_KEY=
MASTER_LLM__BASE_URL=
MASTER_LLM__THINKING=disabled

SPECIALIST_LLM__FAMILY=generic
SPECIALIST_LLM__CHANNEL=generic_openai
SPECIALIST_LLM__MODEL=gpt-5.6-terra
SPECIALIST_LLM__API_KEY=
SPECIALIST_LLM__BASE_URL=
SPECIALIST_LLM__THINKING=disabled
```

## 启动 LangGraph 后端

推荐在仓库根目录使用平台脚本，它会完成环境检查和浏览器准备：

```bash
# macOS，只启动后端
bash start/macos-start.command backend
```

```powershell
# Windows PowerShell，只启动后端
.\start\windows-start.ps1 -Mode backend
```

默认地址为 `http://127.0.0.1:2024`，可在启动脚本进程中通过 `BACKEND_PORT` 覆盖。源码启动默认允许每个 worker 同时处理 4 个会话任务，可通过 `BACKEND_JOBS_PER_WORKER` 覆盖；Windows 便携版固定为 4。后端日志写入仓库根目录的 `start/backend.log`。

手动开发启动：

```bash
cd web-agent
uv sync --frozen --extra dev
uv run langgraph dev \
  --host 127.0.0.1 \
  --port 2024 \
  --no-browser \
  --allow-blocking \
  --n-jobs-per-worker 4 \
  --no-reload
```

`--allow-blocking` 用于兼容 MCP stdio 依赖内部的同步预检；不应在未验证的对外部署中直接照搬开发参数。

## 自动化工程和产物

Plan 首次使用某个工程名时，会在 `DEFAULT_AUTOMATION_PROJECT_ROOT` 下从内置 demo 模板创建工程。默认结构如下：

```text
~/webautotest/<project_name>/
├── package.json
├── playwright.config.ts
└── test_case/
    ├── shared/base-test.ts
    ├── aaaplanning_<plan>/aaa_<plan>.md  # 尚未生成脚本的计划
    └── <plan>/
        ├── aaa_<plan>.md                  # Generator 后保留的计划
        └── *.spec.ts                    # 一个文件一个用例
```

`project_name` 只能是单个目录段；相对 `project_dir` 相对默认自动化根目录解析。相对路径和符号链接规范化后若逃逸允许根目录，后端会拒绝执行。

## Scheduler

Scheduler 有两条链路：

1. 对话中的 Scheduler Agent 创建、修改或停用 JSON 任务。
2. `web-agent-scheduler` 常驻进程读取 JSON，到点后通过 LangGraph SDK 创建确定性的只读监控对话，并运行 `web-autotest-scheduled-run` 图。
3. scheduled-run 图执行 Playwright、持续发布用例/重试/失败和有变化心跳，结束后生成 schema v2 报告并做结构化失败归因。
4. 只有 `owner=test_automation`、`repair_allowed=true` 且置信度达到门槛时，才对失败 spec、关联计划和 `test_case/shared` 调用一次受限 Healer；不会修改产品代码或提交 Git。

项目根目录可放置精确命名的 `task-healer.md` 作为归因政策参考。文件必须是 UTF-8 普通文件且不超过 32 KiB，符号链接会被拒绝；文件缺失时模型依据失败证据自主判断。

第一条链路完成后会输出固定以 `**Scheduler 阶段**` 开头的配置阶段总结。成功和失败都包含状态与配置文件；成功时还包含项目目录、新建/更新操作、任务 ID、Cron、有头/无头模式、启用状态、测试范围和 Scheduler 日志。这些稳定字段由代码生成，模型补充内容只作为“说明”追加。Plan / Generator / Healer 多阶段全部成功后统一标记“当前请求已完成，无需补充信息”；单阶段成功的后续建议明确标为可选操作。桌面客户端可对其中的配置文件、项目目录、测试范围和日志路径进行安全的文件管理器定位。

该配置阶段总结不是测试执行结果。第二条链路在真实 Playwright 运行结束后，会另行生成下文的 JSON / Markdown 分析报告。

复制示例并启动：

```bash
cd web-agent
cp scheduler_tasks.example.json scheduler_tasks.json
uv run web-agent-scheduler

# 也可使用其他配置文件
uv run web-agent-scheduler --config ./scheduler_tasks.json
```

配置约束：

- `scheduler.poll_interval_seconds` 最少 5 秒。
- `scheduler.task_timeout_seconds` 是单次任务超时，默认 1800 秒。
- `scheduler.max_pending_runs` 限制等待队列，默认 100。
- `scheduler.misfire_grace_seconds` 限制漏跑补偿窗口，默认 300 秒。
- `projects[].project_name` 相对 `DEFAULT_AUTOMATION_PROJECT_ROOT`；`project_dir` 可以是绝对路径，相对值仍相对自动化根目录。
- `test_root_dir` 必须是项目内相对目录，默认 `test_case`。
- `schedule` 为五段 Cron；`timezone` 使用 IANA 时区名，例如 `Asia/Shanghai`。
- `locations` 只接受项目内相对文件或目录，留空时执行整个项目；不支持 glob。

执行器串行运行队列中的任务，超时、取消或异常时会清理完整 Playwright 进程树。它保留最多 5000 行控制台输出用于总结，并同时依据进程结果和 Playwright 用例摘要判定状态。

每次运行都生成 JSON 和 Markdown 报告：

```text
<project_dir>/<test_root_dir>/
├── scheduler-service.log
└── scheduler-reports/<task-id>-<digest>/
    ├── <scheduled-time>-<run-id>.json
    ├── <scheduled-time>-<run-id>.md
    ├── latest.json
    └── latest.md
```

总结会提取失败与重试用例、原因、问题类别和诊断摘录，并聚合同一任务最近 20 份历史报告，计算成功率、重试率和重复出现的共性问题。分析不依赖外部模型；即使注入的可选模型补充分析失败，确定性报告仍会保留。

## 测试、静态检查和打包

与 CI 一致的后端检查：

```bash
cd web-agent
uv sync --frozen --extra dev
uv run pytest -q --cov=deep_agent --cov-report=term-missing
uv run ruff check deep_agent tests
uv build
```

只运行 Scheduler 总结相关测试：

```bash
uv run pytest -q tests/test_scheduler_summary.py
```

`uv build` 生成的 wheel 必须包含 `deep_agent/assets/demo/` 中的 `package.json`、`playwright.config.ts` 和 `test_case/shared/base-test.ts`；CI 会显式检查这些运行资源。

## 开发约定

- 节点编排放在 Agent/Graph 层，文件、路径、日志和输出解析放在对应 helper/runtime 层。
- `*_agent.py` 只保留阶段配置、参数校验、workspace、prompt 和权限；事件流与工具状态机放在同目录 `runtime.py`。
- 结构化数据使用 Pydantic/类型契约，配置文件使用 JSON 解析和原子替换。
- 新增后端行为应补 pytest，共享行为需要覆盖取消、异常、路径边界和并发情况。

Git 只使用仓库的 `main` 长期分支；关键节点使用 `git tag -a` 创建 annotated tag。Codex worktree 保持 detached HEAD，完成后 Handoff 回本地 `main`，不创建临时发布分支。完整规则见 [AGENTS.md](../AGENTS.md)。
