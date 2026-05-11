# Web AutoTest Agent 启动脚本说明

`start/` 下直接提供两个平台脚本：

- `start/windows-start.bat`：Windows 启动入口，双击运行（内部调用同目录的 `windows-start.ps1`）
- `start/windows-start.ps1`：Windows 实际启动逻辑（PowerShell 脚本）
- `start/macos-start.command`：macOS 启动脚本

## 前置依赖

启动脚本只做环境检查和友好提示，不会自动下载或安装系统级依赖。运行前请自行准备好以下工具：

- Git：版本 2.x 及以上
  - macOS：`brew install git`，或访问 https://git-scm.com/download/mac
  - Windows：访问 https://git-scm.com/download/win，或 `winget install -e --id Git.Git`
- Node.js：22 LTS 或更高版本
  - macOS：`brew install node@22`，或访问 https://nodejs.org/
  - Windows：访问 https://nodejs.org/，或 `winget install -e --id OpenJS.NodeJS.LTS`
- Python：3.11 或更高版本
  - macOS：`brew install python@3.11`，或访问 https://www.python.org/downloads/
  - Windows：访问 https://www.python.org/downloads/，或 `winget install -e --id Python.Python.3.11`（安装时务必勾选 “Add Python to PATH”，并在 “设置 → 应用 → 应用执行别名” 里关闭 `python.exe` / `python3.exe` 的 Microsoft Store 占位符）
- uv：用于管理后端 Python 依赖
  - macOS：`brew install uv`，或 `curl -LsSf https://astral.sh/uv/install.sh | sh`
  - Windows：`powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"`，或 `winget install -e --id astral-sh.uv`
  - 更多安装方式参见 https://docs.astral.sh/uv/getting-started/installation/
- pnpm：建议锁定 10.5.1
  - 通用：`corepack enable; corepack prepare pnpm@10.5.1 --activate`
  - 备选：`npm install -g pnpm@10.5.1`

启动脚本会在每次运行前检测上述依赖是否齐全、版本是否满足要求；有任何一项缺失或版本过低，脚本会输出明确的修复建议后直接退出。

## 使用方式

支持的参数：`start`（默认）、`end`、`logs`。不传参数时等同于 `start`。

### macOS

在 Finder 中双击 `start/macos-start.command` 即可启动，或在终端里执行：

```bash
bash start/macos-start.command start
bash start/macos-start.command end
bash start/macos-start.command logs
```

首次使用如果被系统提示 “无法打开，因为来自身份不明的开发者”，可右键选择 “打开” 或在 “系统设置 → 隐私与安全性” 中放行。

> Playwright 浏览器安装标记等运行时状态文件统一放在 `~/Library/Application Support/WebAutoTestAgent/`，不会污染仓库目录。

### Windows

推荐直接在资源管理器里双击 `start\windows-start.bat`。脚本会在当前窗口输出所有启动进度与错误信息，结束后按任意键关闭窗口。

也可以在命令行里带参数调用：

```bat
start\windows-start.bat
start\windows-start.bat end
start\windows-start.bat logs
```

不传参数等同于 `start`。

如果脚本触发执行策略拦截，执行一次 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` 放行即可。

> Playwright 浏览器安装标记等运行时状态文件统一放在 `%LOCALAPPDATA%\WebAutoTestAgent\`，不会污染仓库目录。

## 启动行为

脚本按以下顺序执行：

1. 检查项目配置（`web-agent/.env` 必须存在）
2. 检查 Git、Node.js、Python、uv、pnpm 是否就绪且版本满足要求
3. 同步后端依赖（`uv sync`）
4. 同步前端依赖（`pnpm install`）
5. 安装 Playwright Chromium 浏览器（首次启动）
6. 解析 Python 与端口
7. 启动后端、前端，并尝试打开浏览器

启动过程中会按阶段输出进度，例如 `"[3/11] 检查 Node.js"`。服务启动后，当前窗口会持续打印后端和前端运行日志；依赖安装和 setup 信息也只打印到当前控制台，不单独保存日志文件。

## 日志约定

- 后端日志只保留一个文件：`start/backend.log`
- 前端日志不落盘
- setup 日志不落盘

如果启动失败，先看当前控制台输出；如果后端已启动，再看 `start/backend.log`。

如果你想在新的终端窗口里持续查看后端日志，可执行：

- macOS：`bash start/macos-start.command logs`
- Windows：`start\windows-start.bat logs`

## 配置入口

启动相关配置统一放在 `web-agent/.env`：

- `OPEN_BROWSER=0`：启动后不自动打开浏览器
- `FRONTEND_OPEN_URL=`：自定义启动后打开的页面地址
- `START_FORCE_SETUP=1`：强制重新同步依赖
- `START_INSTALL_PLAYWRIGHT_BROWSERS=true`：启动时安装 Playwright Chromium 浏览器
- `NO_RELOAD=0`：启动后端时允许 LangGraph 热加载
- `SERVER_LOG_LEVEL=ERROR`：覆盖后端服务日志级别

不要再为 `start/` 目录维护单独配置文件。
