# Web Test Agent 桌面客户端

`web-agent-client/` 是仓库配套的 macOS / Windows 客户端，使用 Tauri v2、React、Vite 和 `@langchain/langgraph-sdk`。它直接连接 `web-agent/` 暴露的 LangGraph Server，不复制模型配置、消息或后端业务逻辑。

第一版提供以下能力：

- 新建对话和查看服务端历史对话
- 展示 Agent 消息、工具调用参数和工具结果
- 重新加入运行中的线程流
- 取消当前线程的 `running` / `pending` runs
- 展示 interrupt，并把下一条文本作为 `command.resume` 提交
- 提供完整流程、独立 Plan、独立 Generator、独立 Healer 四个快捷任务模板
- 选择仓库根目录、重启本地后端和查看带 ANSI 颜色及主题的后端日志

## 快捷任务模板

新对话页的四个快捷入口保持在同一行，按钮只显示标题。点击后会把完整任务说明填入输入框，不会立即提交，用户可以先补齐工程名、URL、功能范围、计划文件或脚本范围等参数：

- `完整流程`：按 Plan -> Generator -> Healer 顺序连续执行，自动继承本轮上游产物。
- `独立 Plan`：探索真实页面并保存 Markdown 测试计划。
- `独立 Generator`：根据指定 Markdown 或本对话最近一次计划产物生成脚本。
- `独立 Healer`：仅运行、修复和复测指定脚本范围。

## 运行要求

目标机器仍需完整仓库以及根目录文档中要求的 Git、Node.js 22+、Python 3.11+、uv、pnpm 10.5.1 和 Playwright 环境。桌面安装包不携带 Node.js、Python、后端源码或模型密钥。

构建客户端还需要 Rust 1.88+ 和平台构建工具：

- macOS：Xcode Command Line Tools
- Windows：Microsoft C++ Build Tools 和 WebView2

模型、Base URL 和 API Key 继续只配置在 `web-agent/.env`。

## 开发启动

从仓库根目录一键启动客户端和后端：

```bash
# macOS
bash start/macos-start.command start

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
2. 端口为空时继续；确认是 LangGraph 时停止旧进程；发现其他服务时报告冲突并拒绝终止。
3. 调用现有平台启动脚本，并通过 `BACKEND_PORT` 固定后端端口。
4. 等待 `/info` 就绪，再开放会话操作。
5. 应用退出时停止本次客户端管理的后端。

后端日志仍写入仓库的 `start/backend.log`，客户端“后端日志”窗口展示该文件最后 200 行。窗口会解析 ANSI 转义序列，并提供 `macOS 控制台`、`深色`、`浅色` 三种持久化主题。

## 验证与构建

通用检查：

```bash
pnpm typecheck
pnpm test
pnpm build
pnpm test:e2e
cd src-tauri
cargo test
cargo check
```

默认端到端测试使用浏览器内的 Tauri 命令模拟验证快捷模板与日志窗口。需要验证真实历史会话续聊时，先运行本地后端和 `pnpm dev`，再执行：

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

## 关键约定

- Assistant ID 固定为 `web-autotest-agent`。
- 流模式固定为 `values`、`messages-tuple`、`custom`。
- 可见消息优先使用 `display_messages`，按消息 ID 或内容指纹去重。
- 历史消息由 LangGraph Server 持久化，客户端不重复保存。
- 历史会话继续执行前，后端会规范化工具调用与工具结果消息链，避免把孤立结果发送给模型。
- Tauri HTTP 权限只开放 `localhost` / `127.0.0.1`。
