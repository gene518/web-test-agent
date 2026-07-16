# windows-start.ps1 - Web AutoTest Agent 一键启动入口
param(
  [ValidateSet("start", "backend", "end", "logs")]
  [string]$Mode = "start"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$BackendDir = Join-Path $ProjectRoot "web-agent"
$ClientDir = Join-Path $ProjectRoot "web-agent-client"
$PlaywrightProjectDir = Join-Path $BackendDir "deep_agent\assets\demo"
$BackendEnvFile = Join-Path $BackendDir ".env"
$BackendLogFile = Join-Path $ScriptDir "backend.log"
$AppStateDir = if ($env:LOCALAPPDATA) {
  Join-Path $env:LOCALAPPDATA "WebAutoTestAgent"
} else {
  Join-Path $env:USERPROFILE "AppData\Local\WebAutoTestAgent"
}
$PlaywrightMarkerFile = Join-Path $AppStateDir "playwright-chromium-installed"
$BackendHost = "127.0.0.1"
$RequestedBackendPort = $env:BACKEND_PORT
$script:BackendPort = 2024
$script:PythonExe = ""
$script:UvExe = ""
$script:NpxExe = ""
$script:PnpmExe = ""
$script:StartupStep = 0
$script:StartupTotalSteps = 9

function Write-SetupLog {
  param([string]$Message = "")
  Write-Host $Message
}

function Start-SetupStep {
  param([string]$Title)
  $script:StartupStep += 1
  Write-SetupLog
  Write-SetupLog "[$($script:StartupStep)/$($script:StartupTotalSteps)] $Title"
}

function Complete-SetupStep {
  param([string]$Title)
  Write-SetupLog "[$($script:StartupStep)/$($script:StartupTotalSteps)] $Title 完成"
}

function Fail-Startup {
  param([string]$Message)
  Write-Error "启动失败：$Message"
  exit 1
}

function Import-ProjectEnv {
  if (-not (Test-Path -LiteralPath $BackendEnvFile -PathType Leaf)) {
    Fail-Startup "未找到项目配置文件：$BackendEnvFile。请参考 web-agent/.env.example 创建并填写它。"
  }

  foreach ($line in Get-Content -LiteralPath $BackendEnvFile -Encoding UTF8) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) {
      continue
    }
    if ($trimmed.StartsWith("export ")) {
      $trimmed = $trimmed.Substring(7).Trim()
    }
    if ($trimmed -notmatch '^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
      continue
    }
    $key = $Matches[1]
    $value = $Matches[2].Trim()
    if ($value.Length -ge 2) {
      $first = $value.Substring(0, 1)
      $last = $value.Substring($value.Length - 1, 1)
      if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
        $value = $value.Substring(1, $value.Length - 2)
      }
    }
    [Environment]::SetEnvironmentVariable($key, $value, "Process")
  }

  if ($RequestedBackendPort) {
    $script:BackendPort = [int]$RequestedBackendPort
  } elseif ($env:BACKEND_PORT) {
    $script:BackendPort = [int]$env:BACKEND_PORT
  }
  Write-SetupLog "已加载项目配置：$BackendEnvFile"
}

function Resolve-CommandPath {
  param(
    [string[]]$Names,
    [string]$MissingMessage
  )
  foreach ($name in $Names) {
    $command = Get-Command $name -ErrorAction SilentlyContinue
    if ($command) {
      return $command.Source
    }
  }
  Fail-Startup $MissingMessage
}

function Check-Git {
  $git = Resolve-CommandPath -Names @("git.exe", "git") -MissingMessage "未找到 Git。请安装 Git 2.x 或更高版本。"
  $versionText = (& $git --version 2>$null) -join ""
  if ($versionText -notmatch '(\d+)\.(\d+)') {
    Fail-Startup "无法解析 Git 版本：$versionText"
  }
  if ([int]$Matches[1] -lt 2) {
    Fail-Startup "项目需要 Git 2.x 或更高版本，当前版本为 $versionText。"
  }
  Write-SetupLog "Git 已就绪：$git ($versionText)"
}

function Check-Node {
  $node = Resolve-CommandPath -Names @("node.exe", "node") -MissingMessage "未找到 Node.js。请安装 Node.js 22 LTS 或更高版本。"
  $versionText = ((& $node --version 2>$null) -join "").TrimStart("v")
  if ($versionText -notmatch '^(\d+)\.') {
    Fail-Startup "无法解析 Node.js 版本：$versionText"
  }
  if ([int]$Matches[1] -lt 22) {
    Fail-Startup "项目需要 Node.js 22 LTS 或更高版本，当前版本为 $versionText。"
  }
  $script:NpxExe = Resolve-CommandPath -Names @("npx.cmd", "npx") -MissingMessage "未找到 npx，请修复 Node.js 安装。"
  Write-SetupLog "Node.js 已就绪：$node (v$versionText)"
}

function Check-Pnpm {
  $script:PnpmExe = Resolve-CommandPath -Names @("pnpm.cmd", "pnpm") -MissingMessage "未找到 pnpm 10.5.1。请启用 Corepack 或安装 pnpm。"
  $versionText = ((& $script:PnpmExe --version 2>$null) -join "").Trim()
  Write-SetupLog "pnpm 已就绪：$($script:PnpmExe) ($versionText)"
}

function Check-Rust {
  $cargo = Resolve-CommandPath -Names @("cargo.exe", "cargo") -MissingMessage "未找到 Rust/Cargo。请通过 https://rustup.rs/ 安装 Rust 1.88 或更高版本。"
  $versionText = ((& $cargo --version 2>$null) -join "").Trim()
  Write-SetupLog "Rust 已就绪：$cargo ($versionText)"
}

function Check-Python {
  $candidates = @("python.exe", "python3.exe", "python", "python3")
  foreach ($candidate in $candidates) {
    $command = Get-Command $candidate -ErrorAction SilentlyContinue
    if (-not $command) {
      continue
    }
    $versionText = ((& $command.Source --version 2>&1) -join "").Trim()
    if ($versionText -match 'Python\s+(\d+)\.(\d+)') {
      $major = [int]$Matches[1]
      $minor = [int]$Matches[2]
      if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 11)) {
        $script:PythonExe = $command.Source
        Write-SetupLog "Python 已就绪：$($command.Source) ($versionText)"
        return
      }
    }
  }
  Fail-Startup "未找到 Python 3.11 或更高版本。请安装 Python 并加入 PATH。"
}

function Check-Uv {
  $script:UvExe = Resolve-CommandPath -Names @("uv.exe", "uv") -MissingMessage "未找到 uv。请安装 uv 后重试。"
  Write-SetupLog "uv 已就绪：$($script:UvExe)"
}

function Invoke-CheckedCommand {
  param(
    [string]$Command,
    [string[]]$Arguments,
    [string]$FailureMessage
  )
  & $Command @Arguments 2>&1 | ForEach-Object { Write-Host $_ }
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) {
    Fail-Startup "$FailureMessage（退出码：$exitCode）"
  }
}

function Sync-BackendDependencies {
  $langGraphExe = Join-Path $BackendDir ".venv\Scripts\langgraph.exe"
  if ((-not (Test-Path -LiteralPath $langGraphExe -PathType Leaf)) -or ($env:START_FORCE_SETUP -eq "1")) {
    Write-SetupLog "开始同步后端依赖..."
    Invoke-CheckedCommand -Command $script:UvExe -Arguments @("sync", "--project", $BackendDir, "--extra", "dev") -FailureMessage "后端依赖同步失败。"
  } else {
    Write-SetupLog "后端依赖已存在，跳过同步。"
  }
  if (-not (Test-Path -LiteralPath $langGraphExe -PathType Leaf)) {
    Fail-Startup "未找到 LangGraph 可执行文件：$langGraphExe"
  }
  return $langGraphExe
}

function Sync-ClientDependencies {
  $nodeModules = Join-Path $ClientDir "node_modules"
  if ((-not (Test-Path -LiteralPath $nodeModules -PathType Container)) -or ($env:START_FORCE_SETUP -eq "1")) {
    Write-SetupLog "开始同步桌面客户端依赖..."
    Push-Location $ClientDir
    try {
      Invoke-CheckedCommand -Command $script:PnpmExe -Arguments @("install", "--frozen-lockfile") -FailureMessage "桌面客户端依赖同步失败。"
    } finally {
      Pop-Location
    }
  } else {
    Write-SetupLog "桌面客户端依赖已存在，跳过同步。"
  }
}

function Install-PlaywrightBrowser {
  $shouldInstall = if ($env:START_INSTALL_PLAYWRIGHT_BROWSERS) { $env:START_INSTALL_PLAYWRIGHT_BROWSERS } else { "true" }
  if ($shouldInstall -ne "true") {
    Write-SetupLog "已跳过 Playwright 浏览器安装。"
    return
  }
  if ((Test-Path -LiteralPath $PlaywrightMarkerFile) -and ($env:START_FORCE_SETUP -ne "1")) {
    Write-SetupLog "Playwright 浏览器安装标记已存在，跳过安装。"
    return
  }

  $packageFile = Join-Path $PlaywrightProjectDir "package.json"
  $package = Get-Content -LiteralPath $packageFile -Encoding UTF8 -Raw | ConvertFrom-Json
  $playwrightVersion = $package.devDependencies.'@playwright/test'
  if (-not $playwrightVersion) {
    Fail-Startup "无法读取内置 demo 的 Playwright 版本。"
  }
  Write-SetupLog "开始安装 Playwright Chromium 浏览器..."
  Push-Location $PlaywrightProjectDir
  try {
    Invoke-CheckedCommand -Command $script:NpxExe -Arguments @("--yes", "playwright@$playwrightVersion", "install", "chromium") -FailureMessage "Playwright Chromium 浏览器安装失败。"
  } finally {
    Pop-Location
  }
  New-Item -ItemType Directory -Path $AppStateDir -Force | Out-Null
  New-Item -ItemType File -Path $PlaywrightMarkerFile -Force | Out-Null
}

function Test-PortAvailable {
  param([int]$Port)
  $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
  try {
    $listener.Start()
    return $true
  } catch {
    return $false
  } finally {
    $listener.Stop()
  }
}

function Start-Backend {
  param([string]$LangGraphExe)
  $arguments = @("dev", "--host", $BackendHost, "--port", $script:BackendPort, "--no-browser", "--allow-blocking")
  if (-not $env:NO_RELOAD -or $env:NO_RELOAD -eq "1") {
    $arguments += "--no-reload"
  }
  if ($env:SERVER_LOG_LEVEL) {
    $arguments += @("--server-log-level", $env:SERVER_LOG_LEVEL)
  }

  Write-SetupLog "启动后端：http://${BackendHost}:$($script:BackendPort)"
  [System.IO.File]::WriteAllText($BackendLogFile, "", [System.Text.UTF8Encoding]::new($false))
  Push-Location $BackendDir
  try {
    & $LangGraphExe @arguments 2>&1 | Tee-Object -FilePath $BackendLogFile -Append
    $exitCode = $LASTEXITCODE
  } finally {
    Pop-Location
  }
  if ($exitCode -ne 0) {
    Fail-Startup "后端进程已退出（退出码：$exitCode），请查看 $BackendLogFile。"
  }
}

function Stop-Backend {
  $connections = Get-NetTCPConnection -LocalPort $script:BackendPort -State Listen -ErrorAction SilentlyContinue
  $processIds = @($connections | Select-Object -ExpandProperty OwningProcess -Unique)
  if ($processIds.Count -eq 0) {
    Write-Host "未发现监听端口 $($script:BackendPort) 的后端进程。"
    return
  }
  foreach ($processId in $processIds) {
    Write-Host "停止后端进程：$processId"
    & taskkill.exe /PID $processId /T /F | Out-Null
  }
}

function Stop-StartScriptSessions {
  $scriptPathPattern = [Regex]::Escape([System.IO.Path]::GetFullPath($PSCommandPath))
  $sessions = @(
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
      Where-Object {
        $_.ProcessId -ne $PID -and
        $_.CommandLine -and
        $_.CommandLine -match $scriptPathPattern
      }
  )
  foreach ($session in $sessions) {
    Write-Host "停止启动脚本进程：$($session.ProcessId)"
    & taskkill.exe /PID $session.ProcessId /T /F | Out-Null
  }
}

function Show-BackendLogs {
  if (-not (Test-Path -LiteralPath $BackendLogFile -PathType Leaf)) {
    Write-Error "未找到后端日志文件：$BackendLogFile"
    exit 1
  }
  Get-Content -LiteralPath $BackendLogFile -Encoding UTF8 -Tail 200 -Wait
}

function Start-BackendMain {
  $script:StartupStep = 0
  $script:StartupTotalSteps = 9
  Start-SetupStep "检查项目配置"
  Import-ProjectEnv
  Complete-SetupStep "检查项目配置"

  Start-SetupStep "检查 Git"
  Check-Git
  Complete-SetupStep "检查 Git"

  Start-SetupStep "检查 Node.js"
  Check-Node
  Complete-SetupStep "检查 Node.js"

  Start-SetupStep "检查 Python"
  Check-Python
  Complete-SetupStep "检查 Python"

  Start-SetupStep "检查 uv"
  Check-Uv
  Complete-SetupStep "检查 uv"

  Start-SetupStep "同步后端依赖"
  $langGraphExe = Sync-BackendDependencies
  Complete-SetupStep "同步后端依赖"

  Start-SetupStep "安装 Playwright 浏览器"
  Install-PlaywrightBrowser
  Complete-SetupStep "安装 Playwright 浏览器"

  Start-SetupStep "检查后端端口"
  if (-not (Test-PortAvailable -Port $script:BackendPort)) {
    Fail-Startup "后端端口 $($script:BackendPort) 已被占用。"
  }
  Complete-SetupStep "检查后端端口"

  Start-SetupStep "启动后端"
  Write-SetupLog "请通过 web-agent-client 桌面客户端连接。"
  Start-Backend -LangGraphExe $langGraphExe
}

function Start-ClientMain {
  $script:StartupStep = 0
  $script:StartupTotalSteps = 6

  Start-SetupStep "检查项目配置"
  Import-ProjectEnv
  Complete-SetupStep "检查项目配置"

  Start-SetupStep "检查 Node.js"
  Check-Node
  Complete-SetupStep "检查 Node.js"

  Start-SetupStep "检查 pnpm"
  Check-Pnpm
  Complete-SetupStep "检查 pnpm"

  Start-SetupStep "检查 Rust"
  Check-Rust
  Complete-SetupStep "检查 Rust"

  Start-SetupStep "同步桌面客户端依赖"
  Sync-ClientDependencies
  Complete-SetupStep "同步桌面客户端依赖"

  Start-SetupStep "启动桌面客户端"
  Write-SetupLog "客户端将自动准备并管理 LangGraph 后端。"
  Push-Location $ClientDir
  try {
    Invoke-CheckedCommand -Command $script:PnpmExe -Arguments @("tauri", "dev") -FailureMessage "桌面客户端启动失败。"
  } finally {
    Pop-Location
  }
}

switch ($Mode) {
  "start" { Start-ClientMain }
  "backend" { Start-BackendMain }
  "end" {
    if (Test-Path -LiteralPath $BackendEnvFile -PathType Leaf) {
      Import-ProjectEnv
    }
    Stop-StartScriptSessions
    Stop-Backend
  }
  "logs" { Show-BackendLogs }
}
