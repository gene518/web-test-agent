# Web AutoTest Agent 一键启动脚本

`start/` 提供两个平台的一键启动入口：

- `start/windows-start.ps1`：Windows PowerShell 脚本
- `start/macos-start.command`：macOS Shell 脚本

用户执行默认 `start` 模式时，脚本会准备依赖并打开 `web-agent-client/` 原生开发客户端；客户端随后通过脚本内部的 `backend` 模式准备并管理 LangGraph 后端。这样仍是一条命令启动完整应用，同时避免客户端与脚本相互递归启动。

## 前置依赖

运行前请准备：

- Git 2.x 或更高版本
- Node.js 22 LTS 或更高版本
- Python 3.11 或更高版本
- uv（用于管理后端 Python 依赖）
- pnpm 10.5.1
- Rust 1.88 或更高版本及平台构建工具

常用安装方式：

- macOS：`brew install git node@22 python@3.11 uv`
- Windows：通过 `winget` 安装 `Git.Git`、`OpenJS.NodeJS.LTS`、`Python.Python.3.11` 和 `astral-sh.uv`

macOS 还需要 Xcode Command Line Tools；Windows 还需要 Microsoft C++ Build Tools 和 WebView2。详见客户端 README。

## 使用方式

脚本支持 `start`（默认）、`end` 和 `logs` 三种用户模式。`backend` 是桌面客户端调用的内部模式，不需要手动执行。

macOS：

```bash
bash start/macos-start.command start
bash start/macos-start.command end
bash start/macos-start.command logs
```

Windows：

```powershell
.\start\windows-start.ps1
.\start\windows-start.ps1 -Mode end
.\start\windows-start.ps1 -Mode logs
```

## 启动行为

用户执行 `start` 时，两个平台脚本遵循同一顺序：

1. 读取 `web-agent/.env`
2. 检查 Node.js、pnpm 和 Rust
3. 必要时执行 `pnpm install --frozen-lockfile`
4. 启动 Tauri 桌面客户端
5. 客户端调用内部 `backend` 模式检查 Git、Python 和 uv，准备后端依赖及 Playwright Chromium
6. 启动 LangGraph 后端并连接客户端

默认后端地址为 `http://127.0.0.1:2024`。端口被占用时脚本会明确失败，不会静默换端口，以保证桌面客户端和后端使用同一地址。

Playwright 浏览器安装标记保存在平台应用数据目录：

- macOS：`~/Library/Application Support/WebAutoTestAgent/`
- Windows：`%LOCALAPPDATA%\WebAutoTestAgent\`

## 日志与配置

后端日志统一写入 `start/backend.log`。`logs` 模式会持续显示该文件最后 200 行。

启动配置统一维护在 `web-agent/.env`：

- `BACKEND_PORT=2024`：后端固定端口
- `START_FORCE_SETUP=1`：强制重新同步客户端、后端依赖并安装浏览器
- `START_INSTALL_PLAYWRIGHT_BROWSERS=true`：是否准备 Playwright Chromium
- `NO_RELOAD=0`：允许 LangGraph 热加载
- `SERVER_LOG_LEVEL=ERROR`：覆盖后端服务日志级别

不要为 `start/` 目录维护单独配置文件。
