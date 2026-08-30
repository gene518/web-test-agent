# Web AutoTest Agent 工程说明

## 1. 项目介绍

本项目是一个面向 Web 自动化测试的智能体工程，整体目标是把"理解测试需求、生成测试计划、生成 Playwright 脚本、调试修复失败脚本、定时执行测试"串成可持续使用的闭环。

从整体看，项目由三层组成：

1. 后端智能体层：位于 `web-agent/`，使用 LangGraph 编排主图，通过 Master 子图统一判断意图、补齐参数、分发到具体阶段。
2. 桌面客户端层：位于 `web-agent-client/`，基于 Tauri v2、React 和 Vite，负责对话、历史线程、人工中断补参、工具结果展示及本地后端生命周期管理。
3. 本地运行与测试层：位于 `start/`、`web-agent/tests/` 和内置 demo 工程，负责拉起后端、验证路由、验证阶段执行和调度服务。

分模块看，后端围绕一个 Master 和四类执行目标组织：

- `master`：统一入口，负责识别用户意图、抽取参数、处理普通问答、决定下一跳。
- `plan`：探索页面并保存 Markdown 测试计划。
- `generator`：读取测试计划并生成 Playwright `.spec.ts` 脚本。
- `healer`：运行失败脚本、定位问题、修改脚本并复测。
- `scheduler`：维护定时任务配置，并可由独立服务按 Cron 扫描执行。

## 2. 桌面客户端能力

### 2.1 快捷任务模板

新对话提供四个单行排列的快捷入口，按钮只显示标题，点击后把完整模板填入输入框，用户补齐 `{...}` 占位参数后即可提交：

- `plan+generator+healer`：在同一轮按 Plan -> Generator -> Healer 顺序连续执行，阶段间自动继承产物。
- `独立 Plan`：探索真实页面并保存 Markdown 测试计划。
- `独立 Generator`：读取指定或本对话最近生成的 Markdown，为目标用例生成 Playwright 脚本。
- `独立 Healer`：只运行、修复并复测指定范围内的失败脚本。

四个入口保持一行展示；输入框会随内容增高，超过五行后在内部滚动。

### 2.2 后端日志主题

“后端日志”窗口会解析 ANSI 控制符并显示日志级别、模块名等原始颜色，不再把转义序列当作乱码。主题可切换为 `macOS 控制台`、`深色` 或 `浅色`，选择结果会保存在本机。

### 2.3 历史会话续聊

点击历史会话后可以继续发送消息。后端会在再次调用模型前修复持久化工具消息链：忽略没有对应调用的孤立工具结果，并为未闭合的工具调用补齐结果，避免 OpenAI `function_call_output` 协议错误。

## 3. 目录结构

说明：本节重点展开服务端目录、核心类和入口函数。桌面客户端的详细结构见其子工程 README；本地缓存、运行日志、运行时数据、测试缓存、依赖目录和构建产物不在这里说明。

```text
web-test-agent/  # 仓库根目录。
├── README.md  # 当前工程说明文档。
├── DEVELOPMENT_GUIDE.md  # 开发规范和协作约束。
├── doc/  # 产品需求文档。
│   └── PRD-当前实现需求总结.md  # 当前版本需求与能力边界说明。
├── .github/  # GitHub 自动化配置。
│   └── workflows/
│       └── ci.yml  # 持续集成流程。
├── start/  # 启动根目录，平台脚本直接放在这里。
│   ├── macos-start.command  # macOS 一键启动客户端和后端，支持 start / end / logs。
│   ├── windows-start.ps1  # Windows 一键启动客户端和后端，支持 start / end / logs。
│   └── build-windows-x64-portable.ps1  # Windows x64 便携包构建脚本。
├── web-agent/  # 后端智能体工程。
│   ├── pyproject.toml  # Python 包配置与命令入口。
│   ├── uv.lock  # Python 依赖锁文件。
│   ├── langgraph.json  # LangGraph 主图入口配置。
│   ├── scheduler_tasks.example.json  # 定时任务配置示例。
│   ├── deep_agent/  # 后端核心源码包。
│   │   ├── app.py  # LangGraph 加载入口，导出 `agent_graph`。
│   │   ├── web_autotest_agent_workflow.py  # 主工作流定义，提供 `build_web_autotest_agent_workflow()`。
│   │   ├── helpers/  # 跨 Agent 的公共辅助能力，不承载节点编排。
│   │   │   ├── artifacts.py  # 阶段产物、工作区快照、阶段摘要等公共导出。
│   │   │   ├── artifact_helpers/  # 产物提取、manifest、阶段链和摘要实现。
│   │   │   └── specialist_helpers/  # Specialist 公共辅助能力。
│   │   │       ├── types.py  # `SpecialistRuntimeConfig` 与 `SpecialistExecutionContext`。
│   │   │       ├── workspace.py  # `SpecialistWorkspaceMixin`，处理项目目录和文件权限。
│   │   │       ├── display.py  # `SpecialistDisplayMixin`，处理阶段展示消息和总结。
│   │   │       ├── logging.py  # `SpecialistLoggingMixin`，处理事件日志和异常截断。
│   │   │       ├── input_resolution.py  # Specialist 输入归一化与 workspace 边界校验（通用入口）。
│   │   │       └── browser_close.py  # 统一识别 Playwright 浏览器关闭后的预期异常。
│   │   ├── agent/  # Master、Plan、Generator、Healer、Scheduler 等智能体实现。
│   │   │   ├── state.py  # `WorkflowState`，统一承载消息、意图、参数、阶段链和产物。
│   │   │   ├── base_agent.py  # `BaseAgent` 与 `BaseSpecialistAgent`，定义节点执行契约和 Specialist 公共骨架。
│   │   │   ├── finalizer/  # 主工作流最终汇总节点。
│   │   │   ├── master/  # Master 子图与共享服务。
│   │   │   │   ├── master_graph.py  # `build_master_graph()`，构建 Master 子图。
│   │   │   │   ├── master_agent.py  # `MasterAgent`，负责意图识别、补参、问答和最终总结。
│   │   │   │   ├── models/
│   │   │   │   │   └── intent.py  # `IntentClassification` 与参数抽取、缺参计算、阶段链推断。
│   │   │   │   └── nodes/
│   │   │   │       ├── intent_judge_node.py  # `IntentJudgeNode`，负责首次路由和阶段推进。
│   │   │   │       ├── resolve_stage_files_node.py  # `ResolveStageFilesNode`，继承历史产物并解析阶段文件。
│   │   │   │       ├── complete_params_node.py  # `CompleteParamsNode`，缺参中断与恢复。
│   │   │   │       └── general_test_node.py  # `GeneralTestNode`,处理普通测试问答。
│   │   │   ├── plan/
│   │   │   │   ├── plan_agent.py  # `PlanAgent`，阶段入口，只承担配置、参数校验、workspace、prompt、权限。
│   │   │   │   └── runtime.py  # `PlanRuntimeHelper`，承担事件流监听、`planner_save_plan` 状态机和产物抽取。
│   │   │   ├── generator/
│   │   │   │   ├── generator_agent.py  # `GeneratorAgent`，阶段入口，只承担配置、参数校验、workspace、prompt、权限。
│   │   │   │   └── runtime.py  # `GeneratorRuntimeHelper`，承担事件流监听、写文件状态机和脚本落盘校验。
│   │   │   ├── healer/
│   │   │   │   ├── healer_agent.py  # `HealerAgent`，阶段入口，只承担配置、参数校验、workspace、prompt、权限。
│   │   │   │   └── runtime.py  # `HealerRuntimeHelper`，承担事件流监听、验证范围采集和调试产物抽取。
│   │   │   └── scheduler/
│   │   │       └── scheduler_agent.py  # `SchedulerAgent`，把自然语言定时需求转成调度配置更新。
│   │   ├── config/  # 静态配置和文件过滤规则。
│   │   │   └── specialist_file_filter.py  # Specialist 文件查询范围和过滤规则。
│   │   ├── core/  # 基础设施、配置解析和显示消息逻辑。
│   │   │   ├── config.py  # `AppSettings` 与 `get_settings()`，集中管理环境变量和运行配置。
│   │   │   ├── autotest_project_directory.py  # 自动化工程目录解析与内置 demo 模板复制。
│   │   │   ├── cancellation.py  # LangGraph 用户取消信号识别。
│   │   │   ├── local_runtime_cleanup.py  # 本地 in-memory runtime 启动清理。
│   │   │   ├── runtime_logging.py  # 统一日志格式、状态摘要和敏感信息截断。
│   │   │   ├── model_message_history.py  # 修复发给模型的工具调用与工具结果消息链。
│   │   │   └── display_message/  # 用户可见消息提取、去重和汇总逻辑。
│   │   ├── scheduler/  # 独立定时执行服务。
│   │   │   ├── cli.py  # 定时服务命令行入口。
│   │   │   ├── cron.py  # `CronField` 与 `CronExpression`，解析和匹配 Cron 表达式。
│   │   │   ├── models.py  # `SchedulerRuntimeConfig` 等调度配置模型。
│   │   │   ├── analysis.py  # Playwright 输出解析、失败/重试原因归类。
│   │   │   ├── report_models.py  # Scheduler 总结报告的结构化数据契约。
│   │   │   ├── runner.py  # Playwright 子进程执行、超时与进程树清理。
│   │   │   ├── service.py  # Cron 扫描、misfire 补偿、队列治理与服务生命周期。
│   │   │   ├── summary.py  # 执行后总结、历史聚合及 JSON/Markdown 报告落盘。
│   │   │   ├── logs.py  # 项目级 Scheduler 运行日志写入。
│   │   │   ├── paths.py  # 项目、测试目标与日志路径的边界校验。
│   │   │   └── store.py  # 调度配置原子读写与任务更新。
│   │   ├── tools/  # MCP 与 Playwright 工具接入。
│   │   │   ├── mcp_manager.py  # `MCPServerProvider`、`_CachedToolsSession`、`MCPToolsManager`（通用编排，无业务特例）。
│   │   │   ├── tool_error_handling.py  # `GenericMCPToolErrorPolicy`，统一工具错误包装。
│   │   │   ├── tool_invocation.py  # 工具输出判错、直接调用底层实现等通用辅助。
│   │   │   └── playwright/
│   │   │       ├── mcp_provider.py  # `PlaywrightTestMCPProvider`，连接参数与 `post_process_tool` 钩子。
│   │   │       ├── planner_save_plan_wrapper.py  # `planner_save_plan` 阶段专属规则包装（路径校验、缺父目录重建）。
│   │   │       ├── allowlists.py  # Plan / Generator / Healer 各自允许调用的工具白名单。
│   │   │       └── tool_error_policy.py  # `PlaywrightMCPToolErrorPolicy`，区分可重试和不可重试错误。
│   │   └── assets/  # 内置 demo 模板与资源文件。
│   └── tests/  # 后端自动化测试与调试脚本。
└── web-agent-client/  # Tauri v2 桌面客户端，支持 macOS 和 Windows。
    ├── src/  # React 会话、历史、工具消息和任务取消界面。
    ├── src-tauri/  # 原生窗口、HTTP 权限和后端进程生命周期管理。
    └── README.md  # 开发、构建和运行说明。
```

### 3.1 服务端核心类与职责

- 入口与状态：`AppSettings` 负责统一环境变量和运行配置，`WorkflowState` 负责统一 LangGraph 全局状态，`build_web_autotest_agent_workflow()` 负责组装整个服务端主图。
- Master 路由层：`MasterAgent`、`IntentClassification`、`IntentJudgeNode`、`ResolveStageFilesNode` 和 `CompleteParamsNode` 共同完成意图识别、缺参补全和阶段切换；`FinalizeTurnNode` 位于 `agent/finalizer/`，由主工作流在多阶段完成后统一合并最终回复。
- Specialist 执行层：`BaseAgent` 定义统一执行接口，`BaseSpecialistAgent` 负责 Plan、Generator、Healer 三类阶段的公共执行骨架；`SpecialistRuntimeConfig`、`SpecialistExecutionContext` 和三个 `Mixin`（workspace、display、logging）负责运行时配置、目录权限、消息展示和日志处理。
- Specialist 分层约定：`*_agent.py` 只承担"阶段配置 + 参数校验 + workspace 解析 + prompt + 写权限"这类静态职责；事件流监听、工具状态机、产物抽取等运行期逻辑放在同目录下的 `runtime.py`（`PlanRuntimeHelper` / `GeneratorRuntimeHelper` / `HealerRuntimeHelper`）里，分层方式与 Master 的 `master_agent.py + master_graph.py + nodes/*.py` 保持一致。
- 通用输入与异常识别：Specialist 规范化的输入解析、workspace 边界校验、浏览器关闭预期异常识别等能力统一放在 `helpers/specialist_helpers/` 下的 `input_resolution.py` 与 `browser_close.py`，不再在各阶段重复实现。
- 阶段智能体：`PlanAgent` 负责生成测试计划，`GeneratorAgent` 负责生成脚本，`HealerAgent` 负责修复失败脚本，`SchedulerAgent` 负责把自然语言定时需求转换为配置更新。
- 工具与调度层：`MCPToolsManager` 只做通用编排（会话复用、白名单解析、错误处理器补齐），`MCPServerProvider.post_process_tool` 钩子承担各 server 的业务规则，例如 Playwright 的 `planner_save_plan` 路径校验与缺父目录自动重建（见 `tools/playwright/planner_save_plan_wrapper.py`）；`SchedulerService`、`PlaywrightTaskRunner`、`PendingScheduledRun`、`ScheduledRunResult` 负责定时任务的扫描、排队和执行；`CronExpression` 与 `CronField` 负责 Cron 解析与命中判断。

## 4. 运行架构图

```mermaid
flowchart TD
    用户["用户在桌面客户端输入请求"] --> 客户端["Tauri + React 会话界面"]
    客户端 --> 流式连接["LangGraph 流式接口"]
    流式连接 --> 入口["web-agent/langgraph.json 暴露 web-autotest-agent 主图"]
    入口 --> 应用入口["deep_agent/app.py 初始化配置和日志"]
    应用入口 --> 主图["web_autotest_agent_workflow.build_web_autotest_agent_workflow 构建主图"]

    主图 --> 主控子图["master_graph_node：Master 子图"]
    主控子图 --> 意图判断["intent_judge_node：识别意图和阶段链"]
    意图判断 --> 文件解析["resolve_stage_files_node：继承历史产物并解析阶段输入"]
    文件解析 --> 参数补全{"参数是否齐全"}
    参数补全 -- 否 --> 中断["complete_params_node：触发 interrupt 等待用户补参"]
    中断 --> 客户端
    客户端 --> 参数补全
    参数补全 -- 是 --> 阶段路由{"进入哪个阶段"}

    阶段路由 --> 计划["plan_node：探索页面并保存测试计划"]
    阶段路由 --> 生成["generator_node：读取计划并写入脚本"]
    阶段路由 --> 修复["healer_node：运行、修复并复测脚本"]
    阶段路由 --> 定时["scheduler_config_node：维护定时任务配置"]
    意图判断 --> 普通问答["general_test_node：测试专家问答"]

    计划 --> 产物["阶段产物和阶段摘要"]
    生成 --> 产物
    修复 --> 产物
    产物 --> 回流["return_to_master：回到 Master 推进下一阶段"]
    回流 --> 意图判断

    普通问答 --> 结束["END：当前轮结束"]
    定时 --> 结束
    意图判断 --> 最终汇总["finalize_turn_node：统一输出最终总结"]
    最终汇总 --> 结束

    计划 -.-> 工具["Playwright MCP 工具白名单和工作目录权限"]
    生成 -.-> 工具
    修复 -.-> 工具
```

运行状态可以按一次用户请求理解为七步：

1. 初始化：`app.py` 读取环境变量、配置日志、调用 `build_web_autotest_agent_workflow()` 编译图。
2. 等待输入：桌面客户端通过 LangGraph 流式接口连接 `web-autotest-agent` 主图，等待用户提交消息。
3. Master 路由：`IntentJudgeNode` 调用 `MasterAgent` 判断请求属于计划、生成、修复、普通问答或定时任务。
4. 参数准备：`ResolveStageFilesNode` 继承历史产物，`CompleteParamsNode` 在缺参时中断并等待补充。
5. 阶段执行：Plan、Generator、Healer 通过 `BaseSpecialistAgent` 准备工作目录、加载提示词、获取 MCP 工具并执行 Deep Agent。
6. 产物回流：阶段完成后写入 `artifact_history`、`latest_artifacts` 和 `pending_stage_summaries`，多阶段链路继续回到 Master。
7. 汇总结束：`FinalizeTurnNode` 将当前轮所有阶段摘要合成用户可见结论，随后进入下一轮等待。

## 5. 环境要求

源码开发需要以下工具：

- Git。
- Node.js 22 LTS 或更高版本。
- pnpm 10.5.1。
- Python 3.11 或更高版本。
- uv。
- Rust 1.88 或更高版本。
- macOS 需要 Xcode Command Line Tools；Windows 需要 Microsoft C++ Build Tools 和 WebView2 Runtime。

Windows x64 便携包已包含后端运行时、Node.js 和 Playwright Chromium，不需要上述源码开发环境；便携配置位于解压目录的 `config/.env`。

## 6. 快速启动

### 6.1 准备配置

```bash
cp web-agent/.env.example web-agent/.env
```

至少根据实际模型服务填写 API Key、Base URL 和模型名。不要把 `web-agent/.env` 提交到 Git。完整字段说明见 [后端 README](web-agent/README.md)。

### 6.2 一键启动桌面客户端和后端

```bash
# macOS
bash start/macos-start.command start
```

```powershell
# Windows PowerShell
.\start\windows-start.ps1 -Mode start
```

启动脚本会检查工具链、同步依赖、准备 Playwright Chromium，然后运行 Tauri 开发客户端。客户端会管理自己在 `127.0.0.1:2024` 上启动的本地 LangGraph 后端；如端口已被任何外部服务占用（包括其他 LangGraph 实例），会拒绝接管或终止该进程并报告冲突。

停止或查看日志：

```bash
bash start/macos-start.command end
bash start/macos-start.command logs
```

```powershell
.\start\windows-start.ps1 -Mode end
.\start\windows-start.ps1 -Mode logs
```

也可分别启动后端和客户端，详见 [后端 README](web-agent/README.md) 和 [客户端 README](web-agent-client/README.md)。

## 7. 配置和运行目录

- 后端源码配置：`web-agent/.env`。
- Windows 便携包配置：`config/.env`。
- 默认自动化工程根目录：`~/webautotest`，可通过 `DEFAULT_AUTOMATION_PROJECT_ROOT` 修改。
- 单个自动化工程：`<DEFAULT_AUTOMATION_PROJECT_ROOT>/<project_name>/`。
- Plan 计划：`test_case/aaaplanning_{plan-name}/aaa_{plan-name}.md`。
- Generator 脚本：`test_case/{plan-name}/*.spec.ts`，同目录保留测试计划。
- 后端日志：源码启动时为 `start/backend.log`；Windows 便携包为 `data/logs/backend.log`。
- Scheduler 默认配置：`web-agent/scheduler_tasks.json`。

当参数中使用相对 `project_dir` 时，后端会相对自动化根目录解析；`project_name` 只允许单个安全目录名。Scheduler 的 `test_root_dir` 和 `locations` 必须是项目内相对路径，不允许 `..`、符号链接逃逸或 glob。

## 8. Scheduler 执行和总结报告

Scheduler Agent 负责把自然语言需求写入 JSON 配置；真正的定时执行由独立常驻进程负责，不会随桌面客户端自动启动。

```bash
cp web-agent/scheduler_tasks.example.json web-agent/scheduler_tasks.json
cd web-agent
uv run web-agent-scheduler

# 或显式指定配置
uv run web-agent-scheduler --config ./scheduler_tasks.json
```

调度器使用五段 Cron，支持项目时区、misfire 补偿、有界等待队列和任务超时。Playwright 进程结束后，无论成功、失败、超时、取消还是执行器异常，都会进入总结阶段。总结会：

- 结合进程退出码和 Playwright 真实输出判定最终状态。
- 提取失败、重试、flaky、跳过、中断、超时和未执行用例。
- 保留失败与重试原因，归类当前问题，并聚合同一任务最近 20 份历史报告计算成功率、重试率和重复问题。
- 同时生成适合机器处理的 JSON 和便于人阅读的 Markdown。

报告和日志位于自动化项目内：

```text
<project_dir>/<test_root_dir>/
├── scheduler-service.log
└── scheduler-reports/
    └── <task-id>-<digest>/
        ├── <scheduled-time>-<run-id>.json
        ├── <scheduled-time>-<run-id>.md
        ├── latest.json
        └── latest.md
```

JSON 报告包含任务元数据、进程结果、用例统计、失败用例、重试用例、共性问题、历史稳定性、诊断摘录、分析警告和产物路径。控制台输出最多保留 5000 行用于分析；超限时报告会显式标记截断，完整输出仍可在 `scheduler-service.log` 中查看。

## 9. 阶段总结和本地产物打开

Plan、Generator、Healer 和 Scheduler 配置阶段都会输出结构稳定的阶段总结。Plan / Generator / Healer 总结包括项目目录、输入文件、实际落盘文件和验证结果；Scheduler 总结的成功和失败分支都固定包含状态和配置文件，成功时还包含项目目录、配置操作、任务 ID、Cron、执行模式、测试范围和 Scheduler 日志。桌面客户端会把规范阶段总结中可识别的本地文件和目录显示为可点击项：macOS 使用 Finder，Windows 使用文件资源管理器。

Scheduler 的对话阶段总结只表示定时任务配置已成功写入或写入失败，不表示测试已执行。真正的定时运行由独立 `web-agent-scheduler` 进程完成，执行结束后另行生成第 8 节所述 JSON / Markdown 分析报告。

该功能仅在 Tauri 桌面客户端可用，`pnpm dev` 的浏览器预览不能打开本地路径。原生端只允许两类可信根：已验证的仓库根目录，以及源码 `web-agent/.env` 或便携包 `config/.env` 中 `DEFAULT_AUTOMATION_PROJECT_ROOT` 指向的目录；未配置时后者默认为 `~/webautotest`。基准目录必须是某个可信根中的真实目录，目标必须已存在；规范化后会拒绝 `..`、NUL、符号链接逃逸和模型输出的任意系统路径。

## 10. 验证

提交集成改动前，按 CI 使用的参数运行以下检查。

```bash
cd web-agent
uv sync --frozen --extra dev
uv run pytest -q --cov=deep_agent --cov-report=term-missing
uv run ruff check deep_agent tests
uv build
```

```bash
cd web-agent-client
pnpm install --frozen-lockfile
pnpm typecheck
pnpm test
pnpm build
pnpm audit --prod
pnpm exec playwright install --with-deps chromium
pnpm test:e2e

cd src-tauri
cargo fmt --check
cargo clippy --all-targets --locked -- -D warnings
cargo test --locked
cargo check --locked
```

平台构建命令和真实后端 E2E 方式见 [客户端 README](web-agent-client/README.md)。

## 11. Git 工作流

本仓库只长期保留和发布 `main` 分支。不使用功能分支保存关键节点，关键节点使用 `main` 上的 annotated tag。

```bash
git switch main
git pull --ff-only origin main

# 完成修改和验证后在 main 提交
git add <files>
git commit -m "<message>"
git push origin main

# 需要记录关键节点时
git tag -a <tag-name> -m "<milestone description>"
git push origin <tag-name>
```

Codex 管理的 worktree 保持 detached HEAD，完成后通过 Handoff 回到本地 `main`，不点击 "Create branch here"。未经用户明确允许，不创建或推送其他分支；不强制推送 `main`，不改写已发布 tag。详细约束见 [AGENTS.md](AGENTS.md)。
