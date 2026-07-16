# Web Test Agent 桌面客户端

`pc-client/` 是仓库配套的 macOS / Windows 客户端，使用 Tauri v2、React、Vite 和 `@langchain/langgraph-sdk`。它直接连接 `web-agent/` 暴露的 LangGraph Server，不复制模型配置、消息或后端业务逻辑。

第一版提供以下能力：

- 新建对话和查看服务端历史对话
- 展示 Agent 消息、工具调用参数和工具结果
- 重新加入运行中的线程流
- 取消当前线程的 `running` / `pending` runs
- 展示 interrupt，并把下一条文本作为 `command.resume` 提交
- 选择仓库根目录、重启本地后端和查看后端日志

## 运行要求

目标机器仍需完整仓库以及根目录文档中要求的 Git、Node.js 22+、Python 3.11+、uv、pnpm 10.5.1 和 Playwright 环境。桌面安装包不携带 Node.js、Python、后端源码或模型密钥。

构建客户端还需要 Rust 1.88+ 和平台构建工具：

- macOS：Xcode Command Line Tools
- Windows：Microsoft C++ Build Tools 和 WebView2

模型、Base URL 和 API Key 继续只配置在 `web-agent/.env`。

## 开发启动

先安装前端依赖：

```bash
cd pc-client
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
3. 使用 `CLIENT_BACKEND_ONLY=1` 和 `OPEN_BROWSER=0` 调用现有平台启动脚本。
4. 等待 `/info` 就绪，再开放会话操作。
5. 应用退出时停止本次客户端管理的后端。

后端日志仍写入仓库的 `start/backend.log`，客户端“后端日志”窗口展示该文件最后 200 行。

## 验证与构建

通用检查：

```bash
pnpm typecheck
pnpm test
pnpm build
cd src-tauri
cargo test
cargo check
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
- Tauri HTTP 权限只开放 `localhost` / `127.0.0.1`。
