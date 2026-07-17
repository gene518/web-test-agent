# Web AutoTest Agent 一键启动脚本

`start/` 提供两个平台的一键启动入口：

- `start/windows-start.ps1`：Windows PowerShell 脚本
- `start/macos-start.command`：macOS Shell 脚本

用户执行默认 `start` 模式时，脚本会准备依赖并打开 `web-agent-client/` 原生开发客户端；客户端随后通过脚本内部的 `backend` 模式准备并管理 LangGraph 后端。这样仍是一条命令启动完整应用，同时避免客户端与脚本相互递归启动。

## macOS 前置依赖

按下面顺序执行。第 1 步完成后，确认 `xcode-select -p` 能输出路径，再继续安装其他工具。

```bash
# 1. 安装 C/C++ 编译工具；弹窗完成后再执行后续命令
xcode-select --install
xcode-select -p

# 2. 尚未安装 Homebrew 时安装
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 3. 安装 Git、Node.js 22、Python 3.11 和 uv
brew install git node@22 python@3.11 uv
export PATH="$(brew --prefix node@22)/bin:$PATH"

# 4. Node.js 就绪后安装项目锁定的 pnpm
npm install --global pnpm@10.5.1

# 5. 编译工具就绪后安装 Rust stable（必须不低于 1.88）
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
. "$HOME/.cargo/env"

# 6. 验证
git --version && node --version && python3 --version && uv --version
pnpm --version && rustc --version && cargo --version
```

要让 Node.js 22 对以后打开的终端持续生效，再执行：

```bash
echo 'export PATH="'"$(brew --prefix node@22)"'/bin:$PATH"' >> "$HOME/.zshrc"
```

## Windows 11 前置依赖

先在“管理员 PowerShell”中按顺序执行。C++ Build Tools 是 Rust/Tauri 的编译前置；Windows ARM64 还需要 LLVM Clang 编译 `ring`；Node.js 是 pnpm 的安装前置。

```powershell
# 1. 更新 winget 软件源并安装 C++ 构建工具、WebView2
winget source update
winget install --exact --id Microsoft.VisualStudio.2022.BuildTools --override "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --add Microsoft.VisualStudio.Component.VC.Tools.ARM64 --includeRecommended"
winget install --exact --id Microsoft.EdgeWebView2Runtime

# Windows ARM64 必须安装；x64 系统跳过本行
if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") {
  winget install --exact --id LLVM.LLVM --architecture arm64
}

# 2. 安装与本机 CPU 匹配的 VC++ Runtime（uv 等原生工具依赖它）
$vcArch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "arm64" } else { "x64" }
$vcInstaller = Join-Path $env:TEMP "vc_redist.$vcArch.exe"
Invoke-WebRequest "https://aka.ms/vs/17/release/vc_redist.$vcArch.exe" -OutFile $vcInstaller
Start-Process $vcInstaller -ArgumentList "/install", "/quiet", "/norestart" -Wait

# 3. 安装基础运行时和包管理器
winget install --exact --id Git.Git
winget install --exact --id OpenJS.NodeJS.LTS
winget install --exact --id Python.Python.3.11
winget install --exact --id astral-sh.uv
winget install --exact --id Rustlang.Rustup
```

关闭该窗口并重新打开 PowerShell，让 `PATH` 生效，然后执行：

```powershell
# 4. Node.js 就绪后安装项目锁定的 pnpm
npm install --global pnpm@10.5.1

# 5. 初始化 Rust stable；rustup 会自动选择本机的 x64 或 ARM64 MSVC 目标
rustup default stable

# 6. 验证；任一命令失败都应先修复再启动项目
git --version; node --version; python --version; uv --version
pnpm --version; rustc --version; cargo --version
if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { clang --version }
```

建议把压缩包解压到较短路径，例如 `C:\web-test-agent`。若 PowerShell 阻止本次脚本执行，只对当前窗口放行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

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

## 打包并迁移到 Windows

在 macOS 仓库根目录执行：

```bash
bash start/package-for-windows.command
```

脚本把 ZIP 输出到仓库上一级目录。压缩包包含全部 Git 跟踪源码和当前 `web-agent/.env`，排除依赖、编译缓存、运行历史、日志及 `output/`；创建后会自动校验完整性和文件清单。`.env` 可能含 API Key，压缩包只能在可信设备间传递。

## Windows x64 免安装包

`start/build-windows-x64-portable.ps1` 在 Windows x64 构建机上生成解压即用的 ZIP。该包包含编译后的桌面客户端、Python/LangGraph 后端、Node.js、Playwright、Chromium 和固定版 WebView2；目标 Windows 11 x64 机器不需要仓库源码或开发工具链。

手动触发 GitHub Actions CI 后会上传：

```text
web-test-agent-0.1.0-windows-x64-portable.zip
web-test-agent-0.1.0-windows-x64-portable.zip.sha256
```

解压后双击 `Web Test Agent.exe`。运行配置位于包内 `config/.env`，后端日志位于 `data/logs/backend.log`。便携包只构建 x64，不包含 ARM64 变体。

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
