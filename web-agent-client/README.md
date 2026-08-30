# Web Test Agent 桌面客户端

`web-agent-client/` 是仓库配套的 macOS / Windows 客户端，使用 Tauri v2、React、Vite 和 `@langchain/langgraph-sdk`。它直接连接 `web-agent/` 暴露的 LangGraph Server，不复制模型配置、消息或后端业务逻辑。整体架构见 [仓库 README](../README.md)。

当前客户端提供以下能力：

- 新建对话和查看服务端历史对话
- 展示 Agent 消息、工具调用参数和工具结果
- 重新加入运行中的线程流
- 取消当前线程的 `running` / `pending` runs
- 展示 interrupt，并把下一条文本作为 `command.resume` 提交
- 提供 plan+generator+healer、独立 Plan、独立 Generator、独立 Healer 四个快捷任务模板
- 展示 Plan / Generator / Healer / Scheduler 配置阶段的结构化总结，并在系统文件管理器中定位落盘文件和目录
- 选择仓库根目录、重启本地后端和查看带 ANSI 颜色及主题的后端日志

## 客户端结构

```text
web-agent-client/
├── src/
│   ├── components/        # 对话、工具、配置和日志界面
│   ├── hooks/             # LangGraph 会话、run 和流式状态
│   ├── lib/               # 客户端 API、消息归一化和产物路径识别
│   └── App.tsx            # 顶层界面和交互编排
├── src-tauri/
│   ├── src/backend/       # 仓库解析、后端启停、日志和文件管理器
│   ├── capabilities/      # Tauri 网络和窗口权限
│   ├── Cargo.toml
│   └── tauri.conf.json
├── tests/e2e/              # Playwright 客户端端到端测试
├── package.json
└── pnpm-lock.yaml
```

## 快捷任务模板

新对话页的四个快捷入口保持在同一行，按钮只显示标题。点击后会把完整任务说明填入输入框，不会立即提交，用户可以先补齐工程名、URL、功能范围、计划文件或脚本范围等参数：

- `plan+generator+healer`：按 Plan -> Generator -> Healer 顺序连续执行，自动继承本轮上游产物。
- `独立 Plan`：探索真实页面并保存 Markdown 测试计划。
- `独立 Generator`：根据指定 Markdown 或本对话最近一次计划产物生成脚本。
- `独立 Healer`：仅运行、修复和复测指定脚本范围。

开始页使用 50 字简介概括 Agent 的页面探索、用例规划、脚本生成与失败修复能力。输入框会按内容自动增高，最多显示五行，超过后使用内部滚动。

## 运行要求

目标机器仍需完整仓库以及根目录文档中要求的 Git、Node.js 22+、Python 3.11+、uv、pnpm 10.5.1 和 Playwright 环境。桌面安装包不携带 Node.js、Python、后端源码或模型密钥。

构建客户端还需要 Rust 1.88+ 和平台构建工具：

- macOS：Xcode Command Line Tools
- Windows：Microsoft C++ Build Tools；运行界面使用系统安装并自动更新的 WebView2 Runtime

模型、Base URL 和 API Key 继续只配置在 `web-agent/.env`。

Windows x64 免安装包是独立的发布形态，不受上述源码开发环境要求约束。它随包携带本地后端和 Playwright Chromium，界面复用 Windows 11 自带的 WebView2 Runtime；解压后直接双击 `Web Test Agent.exe`，便携配置位于 `config/.env`。当前 NSIS 安装包仍然只包含客户端，完整离线运行请使用 `*-windows-x64-portable.zip`。

## 开发启动

从仓库根目录一键启动客户端和后端：

```bash
# macOS
bash start/macos-start.command start
```

```powershell
# Windows PowerShell
.\start\windows-start.ps1 -Mode start
```

`start` 模式会准备客户端依赖并执行 Tauri 开发启动；客户端再通过平台脚本的内部 `backend` 模式启动后端，因此不会发生递归。关闭客户端或执行对应脚本的 `end` 模式会停止本次开发客户端和后端。

也可以在客户端目录手动启动：

先安装前端依赖：

```bash
cd web-agent-client
pnpm install --frozen-lockfile
```

启动原生客户端：

```bash
pnpm tauri dev
```

客户端会自动向上查找仓库根目录；安装后的应用无法自动定位时，会提示选择包含 `web-agent/langgraph.json` 和 `start/` 平台脚本的目录。选择结果和后端端口保存在客户端本机，默认端口是 `2024`。

仅预览 React 界面时可运行 `pnpm dev`。浏览器预览不会启动或停止后端，会直接尝试连接 `http://127.0.0.1:2024`。

## 后端生命周期

原生客户端每次启动或点击“保存并重启”都会：

1. 检查目标端口的 `/info`。
2. 先停止本客户端此前启动并验证归属的后端；端口为空时继续，若仍被任何外部服务占用（包括其他 LangGraph 实例），则报告冲突并拒绝接管或终止。
3. 调用现有平台启动脚本，并通过 `BACKEND_PORT` 固定后端端口。
4. 等待 `/info` 就绪，再开放会话操作。
5. 应用退出时停止本次客户端管理的后端。

后端日志仍写入仓库的 `start/backend.log`，客户端“后端日志”窗口展示该文件最后 200 行。窗口会解析 ANSI 转义序列，并提供 `macOS 控制台`、`深色`、`浅色` 三种持久化主题。

## 阶段总结和产物打开

Plan、Generator 和 Healer 每个阶段结束后，后端会输出项目目录、输入文件、真实落盘文件、验证范围和状态。Scheduler Agent 完成定时任务配置后，会输出固定的 `**Scheduler 阶段**` 总结：成功和失败都包含状态与配置文件，成功时另包含项目目录、配置操作、任务 ID、Cron、执行模式、测试范围和 Scheduler 日志；模型生成的补充内容只作为说明。

客户端仅在识别到 `**Plan 阶段**`、`**Generator 阶段**`、`**Healer 阶段**` 或 `**Scheduler 阶段**` 这类规范总结时，把反引号中像文件或目录的值显示为可点击按钮。

点击后：

- macOS 目录使用 Finder 打开，文件使用 `open -R` 定位。
- Windows 目录使用文件资源管理器打开，文件使用 `/select` 定位。
- 命令使用参数数组直接启动，不经过 shell。

路径不直接信任模型输出。原生端只允许以下可信根：

- 经 `web-agent/langgraph.json` 和当前平台启动脚本验证的仓库根目录。
- 源码模式 `web-agent/.env` 的 `DEFAULT_AUTOMATION_PROJECT_ROOT`。
- Windows 便携模式 `config/.env` 的 `DEFAULT_AUTOMATION_PROJECT_ROOT`。
- 对应配置未填写时的默认自动化根 `~/webautotest`。

当 `DEFAULT_AUTOMATION_PROJECT_ROOT` 是相对路径时，源码模式相对 `web-agent/` 解析，便携模式相对便携包根目录解析。基准目录必须真实存在且位于某个可信根内；目标文件或目录也必须存在。原生端会去除 `:line[:column]` 位置后缀，规范化路径，并拒绝 NUL、`..`、符号链接逃逸以及不在可信根内的绝对路径。浏览器预览模式不能调用系统文件管理器，会显示明确提示。

Scheduler 对话总结只反映“配置是否写入”，不代表测试已执行。独立 Scheduler 服务不由客户端自动常驻启动；其每次真实运行的分析报告位于 `<project_dir>/<test_root_dir>/scheduler-reports/<task-id>-<digest>/`，同时提供按次命名的 `.json` / `.md` 和 `latest.json` / `latest.md`。报告内容、常驻进程启动方式和配置见 [后端 README](../web-agent/README.md)。

## 验证与构建

从仓库根目录运行通用检查：

```bash
cd web-agent-client
pnpm typecheck
pnpm test
pnpm build
pnpm audit --prod
pnpm exec playwright install chromium
pnpm test:e2e
cd src-tauri
cargo fmt --check
cargo clippy --all-targets --locked -- -D warnings
cargo test --locked
cargo check --locked
```

默认端到端测试使用浏览器内的 Tauri 命令模拟验证快捷模板、输入框高度与日志窗口。需要验证真实历史会话续聊时，先运行本地后端和 `pnpm dev`，再执行：

```bash
E2E_REAL_BACKEND=1 pnpm test:e2e
```

macOS 构建：

```bash
pnpm tauri build --bundles app,dmg
```

Windows 构建：

```powershell
pnpm tauri build --bundles nsis
```

GitHub Actions 会在 Windows runner 上执行前端测试、Rust 测试和 NSIS 构建，并上传未签名安装包。当前工程不包含代码签名、公证或应用商店发布配置。

手动触发 CI 时还会构建并实测 Windows 11 x64 免安装包。构建过程会验证便携 Python 导入、Playwright 模块复制、Chromium 实际运行和 LangGraph `/info` 健康检查，然后上传 ZIP 及 SHA-256 文件。

## 关键约定

- Assistant ID 固定为 `web-autotest-agent`。
- 流模式固定为 `values`、`messages-tuple`、`custom`。
- 可见消息优先使用 `display_messages`，按消息 ID 或内容指纹去重。
- 历史消息由 LangGraph Server 持久化，客户端不重复保存。
- 历史会话继续执行前，后端会规范化工具调用与工具结果消息链，避免把孤立结果发送给模型。
- Tauri HTTP 权限只开放 `localhost` / `127.0.0.1`。

## Git 工作流

客户端与后端在同一仓库集成，只使用 `main` 作为长期及可发布分支。完成前端、Rust 和 E2E 检查后直接在本地 `main` 提交并推送；需要保留发布或里程碑时，在 `main` 上使用 annotated tag：

```bash
git tag -a <tag-name> -m "<milestone description>"
git push origin main
git push origin <tag-name>
```

Codex worktree 保持 detached HEAD，通过 Handoff 把已完成工作移回本地 `main`，不使用 "Create branch here"。未经用户明确要求，不创建或推送其他分支，不强推 `main`，不改写已发布 tag。

完整 Git 约束见 [AGENTS.md](../AGENTS.md)。
