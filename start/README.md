# Web AutoTest Agent 启动脚本说明

`start/` 是项目唯一的启动根目录。用户入口统一为 `start/start.sh`，平台实现脚本放在 `start/script/`。

## 入口约定

- 官方入口：`start/start.sh [start|end|logs]`
- 默认不传参数时等价于 `start`
- 平台分发规则：
  - macOS：转发到 `start/script/macos-start.command`
  - Windows Bash / Git Bash / MSYS Bash：转发到 `powershell.exe -File start/script/windows-start.ps1`

示例：

```bash
bash start/start.sh start
bash start/start.sh end
bash start/start.sh logs
```

Windows 侧需要通过 Git Bash、MSYS Bash 或类似 Bash 环境执行这个 Shell 入口，不再保留 `.bat` 包装脚本。

## 平台脚本命名

- `start/script/macos-start.command`
- `start/script/windows-start.ps1`

这些文件是平台实现入口，不再作为主要用户文档入口宣传。

## 启动行为

首次启动会自动检查并准备：

- `uv`
- `pnpm@10.5.1`
- 后端依赖
- 前端依赖
- Playwright Chromium 浏览器

启动过程中会按阶段输出进度，例如 `"[2/9] 检查/准备 pnpm"`。服务启动后，当前窗口会持续打印后端和前端运行日志；依赖安装和 setup 信息也只打印到当前控制台，不单独保存日志文件。

## 日志约定

- 后端日志只保留一个文件：`start/backend.log`
- 前端日志不落盘
- setup 日志不落盘

如果启动失败，先看当前控制台输出；如果后端已启动，再看 `start/backend.log`。

如果你在新的终端窗口里只想持续查看后端日志，可以直接执行：

```bash
bash start/start.sh logs
```

## 配置入口

启动相关配置统一放在 `web-agent/.env`：

- `OPEN_BROWSER=0`：启动后不自动打开浏览器
- `FRONTEND_OPEN_URL=`：自定义启动后打开的页面地址
- `START_FORCE_SETUP=1`：强制重新同步依赖
- `START_INSTALL_PLAYWRIGHT_BROWSERS=true`：启动时安装 Playwright Chromium 浏览器
- `NO_RELOAD=0`：启动后端时允许 LangGraph 热加载
- `SERVER_LOG_LEVEL=ERROR`：覆盖后端服务日志级别

不要再为 `start/` 或 `start/script/` 目录维护单独配置文件。
