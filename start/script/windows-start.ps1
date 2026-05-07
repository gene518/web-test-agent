param(
  [string]$Mode = "start"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
$StartDir = Join-Path $ProjectRoot "start"
$BackendDir = Join-Path $ProjectRoot "web-agent"
$FrontendDir = Join-Path $ProjectRoot "web-portal"
$BackendEnvFile = Join-Path $BackendDir ".env"
$StartScriptPath = $MyInvocation.MyCommand.Path
$CacheDir = Join-Path $ScriptDir ".cache"
$BackendLogFile = Join-Path $StartDir "backend.log"

$BackendHost = "127.0.0.1"
$FrontendHost = "127.0.0.1"
$BackendPort = 2024
$FrontendPort = 3000
$StartupWaitSeconds = 90
$NoReload = "1"
$ServerLogLevel = ""
$AssistantId = "web-autotest-agent"
$AuthScheme = ""
$OpenBrowser = "1"

$BackendProcess = $null
$FrontendProcess = $null
$CleanedUp = $false
$StartupStepIndex = 0
$StartupTotalSteps = 9
$StopStepIndex = 0
$StopTotalSteps = 4

New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null

function Write-SetupLog {
  param([string]$Message)
  Write-Host $Message
}

function Start-SetupStep {
  param([string]$Title)
  $script:StartupStepIndex += 1
  Write-SetupLog ""
  Write-SetupLog "[$($script:StartupStepIndex)/$($script:StartupTotalSteps)] $Title"
}

function Complete-SetupStep {
  param([string]$Title)
  Write-SetupLog "[$($script:StartupStepIndex)/$($script:StartupTotalSteps)] $Title 完成"
}

function Start-StopStep {
  param([string]$Title)
  $script:StopStepIndex += 1
  Write-SetupLog ""
  Write-SetupLog "[$($script:StopStepIndex)/$($script:StopTotalSteps)] $Title"
}

function Complete-StopStep {
  param([string]$Title)
  Write-SetupLog "[$($script:StopStepIndex)/$($script:StopTotalSteps)] $Title 完成"
}

function Invoke-LoggedExternalCommand {
  param(
    [scriptblock]$Command,
    [string]$FailureMessage,
    [switch]$IgnoreFailure
  )

  $global:LASTEXITCODE = 0
  & $Command
  $ExitCode = $LASTEXITCODE
  if ($IgnoreFailure) {
    return
  }
  if ($ExitCode -ne 0) {
    Fail-Startup $FailureMessage
  }
}

function Fail-Startup {
  param([string]$Message)
  Write-SetupLog "启动失败：$Message"
  exit 1
}

function Ensure-BackendEnvFile {
  if (Test-Path $BackendEnvFile) {
    return
  }

  Fail-Startup "未找到项目配置文件：$BackendEnvFile。请先参考 $(Join-Path $BackendDir '.env.example') 创建并填写它。"
}

function Write-MissingConfigHint {
  param(
    [string]$Key,
    [string]$Hint
  )

  $Value = [Environment]::GetEnvironmentVariable($Key, "Process")
  if (-not [string]::IsNullOrWhiteSpace($Value)) {
    return
  }

  Write-SetupLog "提示：$BackendEnvFile 中 $Key 为空；$Hint"
}

function Import-ProjectEnvFile {
  if (-not (Test-Path $BackendEnvFile)) {
    Write-SetupLog "未找到项目配置文件：$BackendEnvFile，关闭脚本将使用默认端口。"
    return
  }

  Get-Content -Path $BackendEnvFile -Encoding UTF8 | ForEach-Object {
    $Line = $_.Trim()
    if (-not $Line -or $Line.StartsWith("#")) {
      return
    }

    $Match = [regex]::Match($Line, "^\s*([^#=\s]+)\s*=\s*(.*)\s*$")
    if (-not $Match.Success) {
      return
    }

    $Key = $Match.Groups[1].Value.Trim()
    $Value = $Match.Groups[2].Value.Trim()
    if (($Value.StartsWith('"') -and $Value.EndsWith('"')) -or ($Value.StartsWith("'") -and $Value.EndsWith("'"))) {
      $Value = $Value.Substring(1, $Value.Length - 2)
    }

    [Environment]::SetEnvironmentVariable($Key, $Value, "Process")
  }

  Write-SetupLog "已加载项目配置：$BackendEnvFile"
}

function Import-ValidatedProjectEnvFile {
  Import-ProjectEnvFile
  Write-MissingConfigHint -Key "MASTER_MODEL" -Hint "将使用项目默认值。"
  Write-MissingConfigHint -Key "SPECIALIST_MODEL" -Hint "将使用项目默认值。"

  if (-not $env:OPENAI_API_KEY -and -not $env:OPENAI_BASE_URL) {
    Write-SetupLog "提示：$BackendEnvFile 中 OPENAI_API_KEY 和 OPENAI_BASE_URL 都为空，请确认模型服务配置。"
  } elseif (-not $env:OPENAI_API_KEY) {
    Write-SetupLog "提示：$BackendEnvFile 中 OPENAI_API_KEY 为空；如果你的模型服务需要 Key，请先补齐。"
  }
}

function Add-PathIfExists {
  param([string]$PathValue)
  if (Test-Path $PathValue) {
    $env:PATH = "$PathValue;$env:PATH"
  }
}

function Ensure-Uv {
  Add-PathIfExists (Join-Path $env:USERPROFILE ".local\bin")
  Add-PathIfExists (Join-Path $env:LOCALAPPDATA "Programs\uv")

  $UvCommand = Get-Command "uv" -ErrorAction SilentlyContinue
  if ($UvCommand) {
    Write-SetupLog "uv 已就绪：$($UvCommand.Source)"
    return $UvCommand.Source
  }

  Write-SetupLog "未找到 uv，开始自动安装 uv..."
  Invoke-LoggedExternalCommand -Command {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  } -FailureMessage "uv 安装失败。"
  Add-PathIfExists (Join-Path $env:USERPROFILE ".local\bin")
  Add-PathIfExists (Join-Path $env:LOCALAPPDATA "Programs\uv")

  $UvCommand = Get-Command "uv" -ErrorAction SilentlyContinue
  if (-not $UvCommand) {
    Fail-Startup "uv 安装后仍不可用，请重开终端或检查 PATH。"
  }

  Write-SetupLog "uv 安装完成：$($UvCommand.Source)"
  return $UvCommand.Source
}

function Ensure-Pnpm {
  $NodeCommand = Get-Command "node.exe" -ErrorAction SilentlyContinue
  if (-not $NodeCommand) {
    Fail-Startup "未找到 Node.js。请先安装 Node.js 22 LTS 或更高版本后重试。"
  }

  $PnpmCommand = Get-Command "pnpm.cmd" -ErrorAction SilentlyContinue
  if ($PnpmCommand) {
    Write-SetupLog "pnpm 已就绪：$($PnpmCommand.Source)"
    return $PnpmCommand.Source
  }

  $CorepackCommand = Get-Command "corepack.cmd" -ErrorAction SilentlyContinue
  if ($CorepackCommand) {
    Write-SetupLog "未找到 pnpm，使用 corepack 准备 pnpm@10.5.1..."
    Invoke-LoggedExternalCommand -Command { & $CorepackCommand.Source enable } -FailureMessage "corepack enable 失败。" -IgnoreFailure
    Invoke-LoggedExternalCommand -Command { & $CorepackCommand.Source prepare pnpm@10.5.1 --activate } -FailureMessage "corepack 准备 pnpm 失败。"
  } else {
    $NpmCommand = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
    if (-not $NpmCommand) {
      Fail-Startup "未找到 pnpm、corepack 或 npm，无法准备前端依赖。"
    }
    Write-SetupLog "未找到 pnpm/corepack，使用 npm 全局安装 pnpm@10.5.1..."
    Invoke-LoggedExternalCommand -Command { & $NpmCommand.Source install -g pnpm@10.5.1 } -FailureMessage "pnpm 安装失败。"
  }

  $PnpmCommand = Get-Command "pnpm.cmd" -ErrorAction SilentlyContinue
  if (-not $PnpmCommand) {
    Fail-Startup "pnpm 准备后仍不可用，请检查 Node.js 安装。"
  }

  Write-SetupLog "pnpm 准备完成：$($PnpmCommand.Source)"
  return $PnpmCommand.Source
}

function Sync-BackendDependencies {
  param([string]$UvExe)

  $LangGraphExe = Join-Path $BackendDir ".venv\Scripts\langgraph.exe"
  if ((-not (Test-Path $LangGraphExe)) -or ($env:START_FORCE_SETUP -eq "1")) {
    Write-SetupLog "开始同步后端依赖..."
    Invoke-LoggedExternalCommand -Command { & $UvExe sync --project $BackendDir --extra dev } -FailureMessage "后端依赖同步失败。"
  } else {
    Write-SetupLog "后端依赖已存在，跳过同步。"
  }

  if (-not (Test-Path $LangGraphExe)) {
    Fail-Startup "未找到 LangGraph 可执行文件：$LangGraphExe"
  }
  return $LangGraphExe
}

function Sync-FrontendDependencies {
  param([string]$PnpmExe)

  $NodeModules = Join-Path $FrontendDir "node_modules"
  if ((-not (Test-Path $NodeModules)) -or ($env:START_FORCE_SETUP -eq "1")) {
    Write-SetupLog "开始同步前端依赖..."
    Push-Location $FrontendDir
    try {
      Invoke-LoggedExternalCommand -Command { & $PnpmExe install } -FailureMessage "前端依赖同步失败。"
    } finally {
      Pop-Location
    }
  } else {
    Write-SetupLog "前端依赖已存在，跳过同步。"
  }
}

function Install-PlaywrightBrowsers {
  $InstallBrowsers = if ($env:START_INSTALL_PLAYWRIGHT_BROWSERS) { $env:START_INSTALL_PLAYWRIGHT_BROWSERS } else { "true" }
  $MarkerFile = Join-Path $CacheDir "playwright-chromium-installed"
  if ($InstallBrowsers -ne "true") {
    Write-SetupLog "已跳过 Playwright 浏览器安装。"
    return
  }
  if ((Test-Path $MarkerFile) -and ($env:START_FORCE_SETUP -ne "1")) {
    Write-SetupLog "Playwright 浏览器安装标记已存在，跳过安装。"
    return
  }

  $NpxCommand = Get-Command "npx.cmd" -ErrorAction SilentlyContinue
  if (-not $NpxCommand) {
    Fail-Startup "未找到 npx.cmd，无法安装 Playwright 浏览器。"
  }

  Write-SetupLog "开始安装 Playwright Chromium 浏览器..."
  Push-Location $FrontendDir
  try {
    Invoke-LoggedExternalCommand -Command { & $NpxCommand.Source --yes playwright install chromium } -FailureMessage "Playwright Chromium 浏览器安装失败。"
  } finally {
    Pop-Location
  }
  New-Item -ItemType File -Force -Path $MarkerFile | Out-Null
}

function Test-PortBindable {
  param([string]$HostName, [int]$Port)
  $Address = [System.Net.IPAddress]::Parse($HostName)
  $Listener = [System.Net.Sockets.TcpListener]::new($Address, $Port)
  try {
    $Listener.Start()
    return $true
  } catch {
    return $false
  } finally {
    $Listener.Stop()
  }
}

function Test-PortAcceptsConnections {
  param([string]$HostName, [int]$Port)
  $Client = [System.Net.Sockets.TcpClient]::new()
  try {
    $AsyncResult = $Client.BeginConnect($HostName, $Port, $null, $null)
    if (-not $AsyncResult.AsyncWaitHandle.WaitOne(500)) {
      return $false
    }
    $Client.EndConnect($AsyncResult)
    return $true
  } catch {
    return $false
  } finally {
    $Client.Close()
  }
}

function Get-FreePort {
  param([string]$HostName)
  $Address = [System.Net.IPAddress]::Parse($HostName)
  $Listener = [System.Net.Sockets.TcpListener]::new($Address, 0)
  try {
    $Listener.Start()
    return $Listener.LocalEndpoint.Port
  } finally {
    $Listener.Stop()
  }
}

function Resolve-Port {
  param([string]$Name, [string]$HostName, [int]$PreferredPort)
  if (Test-PortBindable -HostName $HostName -Port $PreferredPort) {
    return $PreferredPort
  }
  $FreePort = Get-FreePort -HostName $HostName
  Write-SetupLog "$Name 默认端口 $PreferredPort 已被占用，改用 $FreePort。"
  return $FreePort
}

function Wait-ForPort {
  param(
    [string]$Name,
    [string]$HostName,
    [int]$Port,
    [System.Diagnostics.Process]$Process,
    [string]$LogFile
  )

  $Deadline = (Get-Date).AddSeconds($StartupWaitSeconds)
  while (-not (Test-PortAcceptsConnections -HostName $HostName -Port $Port)) {
    if ($Process.HasExited) {
      if ($LogFile -and (Test-Path $LogFile)) {
        Write-SetupLog "$Name 进程已退出。最近日志："
        Get-Content -Path $LogFile -Tail 40 | ForEach-Object { Write-SetupLog $_ }
      } else {
        Write-SetupLog "$Name 进程已退出，请查看当前控制台输出。"
      }
      return $false
    }
    if ((Get-Date) -ge $Deadline) {
      if ($LogFile -and (Test-Path $LogFile)) {
        Write-SetupLog "$Name 未能在 ${StartupWaitSeconds}s 内监听 ${HostName}:${Port}。最近日志："
        Get-Content -Path $LogFile -Tail 40 | ForEach-Object { Write-SetupLog $_ }
      } else {
        Write-SetupLog "$Name 未能在 ${StartupWaitSeconds}s 内监听 ${HostName}:${Port}，请查看当前控制台输出。"
      }
      return $false
    }
    Start-Sleep -Milliseconds 500
  }
  return $true
}

function Quote-PowerShellSingle {
  param([string]$Value)
  return "'" + ($Value -replace "'", "''") + "'"
}

function Start-HiddenPowerShellProcess {
  param([string]$CommandText)
  return Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-Command",
    $CommandText
  ) -WindowStyle Hidden -PassThru
}

function Start-ConsolePowerShellProcess {
  param([string]$CommandText)
  return Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-Command",
    $CommandText
  ) -NoNewWindow -PassThru
}

function Start-Backend {
  param([string]$LangGraphExe)
  Write-SetupLog "启动后端：http://${BackendHost}:${BackendPort}"
  $BackendDirQuoted = Quote-PowerShellSingle $BackendDir
  $LangGraphExeQuoted = Quote-PowerShellSingle $LangGraphExe
  $BackendLogQuoted = Quote-PowerShellSingle $BackendLogFile
  $NoReloadArg = if ($NoReload -eq "1") { " --no-reload" } else { "" }
  $ServerLogLevelArg = if ($ServerLogLevel) { " --server-log-level " + (Quote-PowerShellSingle $ServerLogLevel) } else { "" }
  $CommandText = "Set-Location -LiteralPath $BackendDirQuoted; & $LangGraphExeQuoted dev --host $BackendHost --port $BackendPort --no-browser$NoReloadArg$ServerLogLevelArg 2>&1 | Tee-Object -FilePath $BackendLogQuoted -Append"
  $script:BackendProcess = Start-ConsolePowerShellProcess -CommandText $CommandText
  if (-not (Wait-ForPort -Name "后端" -HostName $BackendHost -Port $BackendPort -Process $script:BackendProcess -LogFile $BackendLogFile)) {
    Fail-Startup "后端启动失败。"
  }
}

function Start-Frontend {
  param([string]$PnpmExe)
  $env:NEXT_PUBLIC_API_URL = "http://${BackendHost}:${BackendPort}"
  $env:NEXT_PUBLIC_ASSISTANT_ID = $AssistantId
  $env:NEXT_PUBLIC_AUTH_SCHEME = $AuthScheme

  Write-SetupLog "启动前端：http://${FrontendHost}:${FrontendPort}"
  $FrontendDirQuoted = Quote-PowerShellSingle $FrontendDir
  $PnpmExeQuoted = Quote-PowerShellSingle $PnpmExe
  $ApiUrlQuoted = Quote-PowerShellSingle "http://${BackendHost}:${BackendPort}"
  $AssistantIdQuoted = Quote-PowerShellSingle $AssistantId
  $AuthSchemeQuoted = Quote-PowerShellSingle $AuthScheme
  $CommandText = "Set-Location -LiteralPath $FrontendDirQuoted; `$env:NEXT_PUBLIC_API_URL=$ApiUrlQuoted; `$env:NEXT_PUBLIC_ASSISTANT_ID=$AssistantIdQuoted; `$env:NEXT_PUBLIC_AUTH_SCHEME=$AuthSchemeQuoted; & $PnpmExeQuoted exec next dev --hostname $FrontendHost --port $FrontendPort"
  $script:FrontendProcess = Start-ConsolePowerShellProcess -CommandText $CommandText
  if (-not (Wait-ForPort -Name "前端" -HostName $FrontendHost -Port $FrontendPort -Process $script:FrontendProcess -LogFile "")) {
    Fail-Startup "前端启动失败。"
  }
}

function Open-FrontendUrl {
  param([string]$Url)

  if ($OpenBrowser -ne "1") {
    Write-SetupLog "已跳过自动打开浏览器，请手动访问：$Url"
    return
  }

  try {
    Start-Process $Url | Out-Null
    Write-SetupLog "已自动打开前端页面：$Url"
  } catch {
    Write-SetupLog "无法自动打开浏览器，请手动访问：$Url"
  }
}

function Show-Logs {
  if (-not (Test-Path $BackendLogFile)) {
    Write-Host "未找到后端日志文件：$BackendLogFile"
    exit 1
  }

  Write-Host "持续查看后端日志：$BackendLogFile"
  Get-Content -Path $BackendLogFile -Tail 200 -Wait
}

function Get-ChildProcessIds {
  param([int]$ParentProcessId)
  Get-CimInstance Win32_Process -Filter "ParentProcessId=$ParentProcessId" -ErrorAction SilentlyContinue |
    ForEach-Object { [int]$_.ProcessId }
}

function Stop-ProcessTree {
  param([System.Diagnostics.Process]$Process)
  if (-not $Process -or $Process.HasExited) {
    return
  }

  $Ids = New-Object System.Collections.Generic.List[int]
  function Collect-Ids {
    param([int]$ProcessId)
    foreach ($ChildId in Get-ChildProcessIds -ParentProcessId $ProcessId) {
      Collect-Ids -ProcessId $ChildId
    }
    $Ids.Add($ProcessId)
  }

  Collect-Ids -ProcessId $Process.Id
  foreach ($ProcessId in $Ids) {
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
  }
}

function Cleanup {
  if ($script:CleanedUp) {
    return
  }
  $script:CleanedUp = $true

  $HasProcess = (
    ($script:FrontendProcess -and -not $script:FrontendProcess.HasExited) -or
    ($script:BackendProcess -and -not $script:BackendProcess.HasExited)
  )
  if (-not $HasProcess) {
    return
  }

  Write-Host "正在停止本地服务..."
  Stop-ProcessTree -Process $script:FrontendProcess
  Stop-ProcessTree -Process $script:BackendProcess
}

function Get-ScriptProcessIds {
  param([string]$ScriptPath)

  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
      $_.CommandLine -and
      $_.CommandLine -like "*$ScriptPath*" -and
      $_.ProcessId -ne $PID -and
      $_.ParentProcessId -ne $PID
    } |
    Select-Object -ExpandProperty ProcessId -Unique
}

function Stop-ProcessTreeById {
  param([int]$ProcessId)

  $Ids = New-Object System.Collections.Generic.List[int]

  function Collect-IdsById {
    param([int]$CurrentId)

    foreach ($ChildId in Get-ChildProcessIds -ParentProcessId $CurrentId) {
      Collect-IdsById -CurrentId $ChildId
    }
    $Ids.Add($CurrentId)
  }

  Collect-IdsById -CurrentId $ProcessId
  foreach ($Id in $Ids) {
    Stop-Process -Id $Id -Force -ErrorAction SilentlyContinue
  }
}

function Stop-ScriptSessions {
  param(
    [string]$Label,
    [string]$ScriptPath
  )

  $ProcessIds = @(Get-ScriptProcessIds -ScriptPath $ScriptPath)
  if ($ProcessIds.Count -eq 0) {
    Write-SetupLog "未发现运行中的 $Label 进程。"
    return
  }

  foreach ($ProcessId in $ProcessIds) {
    Write-SetupLog "停止 $Label 进程：$ProcessId"
    Stop-ProcessTreeById -ProcessId $ProcessId
  }
}

function Get-ListenerProcessIds {
  param([int]$Port)

  $GetNetTcpCommand = Get-Command "Get-NetTCPConnection" -ErrorAction SilentlyContinue
  if (-not $GetNetTcpCommand) {
    return @()
  }

  try {
    return @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop |
      Select-Object -ExpandProperty OwningProcess -Unique)
  } catch {
    return @()
  }
}

function Stop-ListenerProcesses {
  param(
    [string]$Label,
    [int]$Port
  )

  $ProcessIds = @(Get-ListenerProcessIds -Port $Port)
  if ($ProcessIds.Count -eq 0) {
    Write-SetupLog "$Label 端口 $Port 当前没有监听进程。"
    return
  }

  foreach ($ProcessId in $ProcessIds) {
    Write-SetupLog "停止 $Label 监听进程：$ProcessId"
    Stop-ProcessTreeById -ProcessId $ProcessId
  }
}

function Start-Main {
  trap {
    Cleanup
    exit 130
  }

  Set-Content -Path $BackendLogFile -Value "" -Encoding UTF8

  Start-SetupStep "检查项目配置"
  Ensure-BackendEnvFile
  Import-ValidatedProjectEnvFile
  $BackendPort = if ($env:BACKEND_PORT) { [int]$env:BACKEND_PORT } else { 2024 }
  $FrontendPort = if ($env:FRONTEND_PORT) { [int]$env:FRONTEND_PORT } else { 3000 }
  $StartupWaitSeconds = if ($env:STARTUP_WAIT_SECONDS) { [int]$env:STARTUP_WAIT_SECONDS } else { 90 }
  $NoReload = if ($env:NO_RELOAD) { $env:NO_RELOAD } else { "1" }
  $ServerLogLevel = if ($env:SERVER_LOG_LEVEL) { $env:SERVER_LOG_LEVEL } else { "" }
  $AssistantId = if ($env:NEXT_PUBLIC_ASSISTANT_ID) { $env:NEXT_PUBLIC_ASSISTANT_ID } else { "web-autotest-agent" }
  $AuthScheme = if ($env:NEXT_PUBLIC_AUTH_SCHEME) { $env:NEXT_PUBLIC_AUTH_SCHEME } else { "" }
  $OpenBrowser = if ($env:OPEN_BROWSER) { $env:OPEN_BROWSER } else { "1" }
  Complete-SetupStep "检查项目配置"

  Start-SetupStep "检查/准备 uv"
  $UvExe = Ensure-Uv
  Complete-SetupStep "检查/准备 uv"

  Start-SetupStep "检查/准备 pnpm"
  $PnpmExe = Ensure-Pnpm
  Complete-SetupStep "检查/准备 pnpm"

  Start-SetupStep "同步后端依赖"
  $LangGraphExe = Sync-BackendDependencies -UvExe $UvExe
  Complete-SetupStep "同步后端依赖"

  Start-SetupStep "同步前端依赖"
  Sync-FrontendDependencies -PnpmExe $PnpmExe
  Complete-SetupStep "同步前端依赖"

  Start-SetupStep "安装 Playwright 浏览器"
  Install-PlaywrightBrowsers
  Complete-SetupStep "安装 Playwright 浏览器"

  Start-SetupStep "解析 Python 与端口"
  $BackendPort = Resolve-Port -Name "后端" -HostName $BackendHost -PreferredPort $BackendPort
  $FrontendPort = Resolve-Port -Name "前端" -HostName $FrontendHost -PreferredPort $FrontendPort
  Complete-SetupStep "解析 Python 与端口"

  $FrontendOpenUrl = if ($env:FRONTEND_OPEN_URL) { $env:FRONTEND_OPEN_URL } else { "http://${FrontendHost}:${FrontendPort}/?chatHistoryOpen=true" }

  Start-SetupStep "启动后端"
  Start-Backend -LangGraphExe $LangGraphExe
  Complete-SetupStep "启动后端"

  Start-SetupStep "启动前端并尝试打开页面"
  Start-Frontend -PnpmExe $PnpmExe
  Open-FrontendUrl -Url $FrontendOpenUrl
  Complete-SetupStep "启动前端并尝试打开页面"

  Write-Host ""
  Write-Host "Web AutoTest Agent 已启动。"
  Write-Host "前端地址：$FrontendOpenUrl"
  Write-Host "后端地址：http://${BackendHost}:${BackendPort}"
  Write-Host "后端日志：$BackendLogFile"
  Write-Host "关闭本窗口或按 Ctrl+C 会停止本地服务。"

  while ($true) {
    if ($BackendProcess.HasExited) {
      Write-Host "后端进程已退出，请查看 $BackendLogFile"
      exit 1
    }
    if ($FrontendProcess.HasExited) {
      Write-Host "前端进程已退出，请查看当前控制台输出。"
      exit 1
    }
    Start-Sleep -Seconds 1
  }
}

function Stop-Main {
  Start-StopStep "加载项目配置"
  Import-ProjectEnvFile
  $BackendPort = if ($env:BACKEND_PORT) { [int]$env:BACKEND_PORT } else { 2024 }
  $FrontendPort = if ($env:FRONTEND_PORT) { [int]$env:FRONTEND_PORT } else { 3000 }
  Complete-StopStep "加载项目配置"

  Start-StopStep "停止启动脚本进程"
  Stop-ScriptSessions -Label "start.ps1" -ScriptPath $StartScriptPath
  Complete-StopStep "停止启动脚本进程"

  Start-StopStep "停止后端服务"
  Stop-ListenerProcesses -Label "后端" -Port $BackendPort
  Complete-StopStep "停止后端服务"

  Start-StopStep "停止前端服务"
  Stop-ListenerProcesses -Label "前端" -Port $FrontendPort
  Complete-StopStep "停止前端服务"

  Write-Host ""
  Write-Host "本地服务关闭完成。"
  Write-Host "后端端口：${BackendHost}:${BackendPort}"
  Write-Host "前端端口：${FrontendHost}:${FrontendPort}"
}

switch ($Mode.ToLowerInvariant()) {
  "start" { Start-Main }
  "end" { Stop-Main }
  "logs" { Show-Logs }
  default {
    Write-Host "用法：powershell -File start/script/windows-start.ps1 [start|end|logs]"
    exit 1
  }
}
