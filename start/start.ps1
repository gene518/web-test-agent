$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
$BackendDir = Join-Path $ProjectRoot "web-agent"
$FrontendDir = Join-Path $ProjectRoot "web-poartl"
$ConfigTemplate = Join-Path $ScriptDir "config.env.example"
$ConfigFile = Join-Path $ScriptDir "config.env"
$LogDir = Join-Path $ScriptDir "logs"
$CacheDir = Join-Path $ScriptDir ".cache"
$SetupLogFile = Join-Path $LogDir "setup.log"
$BackendLogFile = Join-Path $LogDir "backend.log"
$FrontendLogFile = Join-Path $LogDir "frontend.log"

$BackendHost = "127.0.0.1"
$FrontendHost = "127.0.0.1"
$BackendPort = if ($env:BACKEND_PORT) { [int]$env:BACKEND_PORT } else { 2024 }
$FrontendPort = if ($env:FRONTEND_PORT) { [int]$env:FRONTEND_PORT } else { 3000 }
$StartupWaitSeconds = if ($env:STARTUP_WAIT_SECONDS) { [int]$env:STARTUP_WAIT_SECONDS } else { 90 }
$AssistantId = if ($env:NEXT_PUBLIC_ASSISTANT_ID) { $env:NEXT_PUBLIC_ASSISTANT_ID } else { "web-autotest-agent" }
$AuthScheme = if ($env:NEXT_PUBLIC_AUTH_SCHEME) { $env:NEXT_PUBLIC_AUTH_SCHEME } else { "" }

$BackendProcess = $null
$FrontendProcess = $null
$CleanedUp = $false

New-Item -ItemType Directory -Force -Path $LogDir, $CacheDir | Out-Null
Set-Content -Path $SetupLogFile -Value "" -Encoding UTF8
Set-Content -Path $BackendLogFile -Value "" -Encoding UTF8
Set-Content -Path $FrontendLogFile -Value "" -Encoding UTF8

function Write-SetupLog {
  param([string]$Message)
  Write-Host $Message
  Add-Content -Path $SetupLogFile -Value $Message -Encoding UTF8
}

function Fail-Startup {
  param([string]$Message)
  Write-SetupLog "启动失败：$Message"
  Write-SetupLog "请查看日志：$SetupLogFile"
  exit 1
}

function Ensure-ConfigFile {
  if (Test-Path $ConfigFile) {
    return
  }

  Copy-Item -Path $ConfigTemplate -Destination $ConfigFile
  Write-SetupLog "已生成配置文件：$ConfigFile"
  Write-SetupLog "请先填写 config.env 里的模型服务信息，然后重新双击 start.bat。"
  exit 1
}

function Import-EnvFile {
  Get-Content -Path $ConfigFile -Encoding UTF8 | ForEach-Object {
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

  if (-not $env:OPENAI_API_KEY) {
    Write-SetupLog "提示：OPENAI_API_KEY 为空；如果你的模型服务需要 Key，请先填写 $ConfigFile。"
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
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" *>> $SetupLogFile
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
    & $CorepackCommand.Source enable *>> $SetupLogFile
    & $CorepackCommand.Source prepare pnpm@10.5.1 --activate *>> $SetupLogFile
  } else {
    $NpmCommand = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
    if (-not $NpmCommand) {
      Fail-Startup "未找到 pnpm、corepack 或 npm，无法准备前端依赖。"
    }
    Write-SetupLog "未找到 pnpm/corepack，使用 npm 全局安装 pnpm@10.5.1..."
    & $NpmCommand.Source install -g pnpm@10.5.1 *>> $SetupLogFile
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
    & $UvExe sync --project $BackendDir --extra dev *>> $SetupLogFile
    if ($LASTEXITCODE -ne 0) {
      Fail-Startup "后端依赖同步失败。"
    }
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
      & $PnpmExe install *>> $SetupLogFile
      if ($LASTEXITCODE -ne 0) {
        Fail-Startup "前端依赖同步失败。"
      }
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
    & $NpxCommand.Source --yes playwright install chromium *>> $SetupLogFile
    if ($LASTEXITCODE -ne 0) {
      Fail-Startup "Playwright Chromium 浏览器安装失败。"
    }
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
      Write-SetupLog "$Name 进程已退出。最近日志："
      if (Test-Path $LogFile) {
        Get-Content -Path $LogFile -Tail 40 | ForEach-Object { Write-SetupLog $_ }
      }
      return $false
    }
    if ((Get-Date) -ge $Deadline) {
      Write-SetupLog "$Name 未能在 ${StartupWaitSeconds}s 内监听 ${HostName}:${Port}。最近日志："
      if (Test-Path $LogFile) {
        Get-Content -Path $LogFile -Tail 40 | ForEach-Object { Write-SetupLog $_ }
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

function Start-LoggedPowerShellProcess {
  param([string]$CommandText)
  return Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-Command",
    $CommandText
  ) -WindowStyle Hidden -PassThru
}

function Start-Backend {
  param([string]$LangGraphExe)
  Write-SetupLog "启动后端：http://${BackendHost}:${BackendPort}"
  $BackendDirQuoted = Quote-PowerShellSingle $BackendDir
  $LangGraphExeQuoted = Quote-PowerShellSingle $LangGraphExe
  $BackendLogQuoted = Quote-PowerShellSingle $BackendLogFile
  $CommandText = "Set-Location -LiteralPath $BackendDirQuoted; & $LangGraphExeQuoted dev --host $BackendHost --port $BackendPort --no-browser --no-reload *>> $BackendLogQuoted"
  $script:BackendProcess = Start-LoggedPowerShellProcess -CommandText $CommandText
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
  $FrontendLogQuoted = Quote-PowerShellSingle $FrontendLogFile
  $ApiUrlQuoted = Quote-PowerShellSingle "http://${BackendHost}:${BackendPort}"
  $AssistantIdQuoted = Quote-PowerShellSingle $AssistantId
  $AuthSchemeQuoted = Quote-PowerShellSingle $AuthScheme
  $CommandText = "Set-Location -LiteralPath $FrontendDirQuoted; `$env:NEXT_PUBLIC_API_URL=$ApiUrlQuoted; `$env:NEXT_PUBLIC_ASSISTANT_ID=$AssistantIdQuoted; `$env:NEXT_PUBLIC_AUTH_SCHEME=$AuthSchemeQuoted; & $PnpmExeQuoted exec next dev --hostname $FrontendHost --port $FrontendPort *>> $FrontendLogQuoted"
  $script:FrontendProcess = Start-LoggedPowerShellProcess -CommandText $CommandText
  if (-not (Wait-ForPort -Name "前端" -HostName $FrontendHost -Port $FrontendPort -Process $script:FrontendProcess -LogFile $FrontendLogFile)) {
    Fail-Startup "前端启动失败。"
  }
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

try {
  Ensure-ConfigFile
  Import-EnvFile
  $UvExe = Ensure-Uv
  $PnpmExe = Ensure-Pnpm
  $LangGraphExe = Sync-BackendDependencies -UvExe $UvExe
  Sync-FrontendDependencies -PnpmExe $PnpmExe
  Install-PlaywrightBrowsers
  $BackendPort = Resolve-Port -Name "后端" -HostName $BackendHost -PreferredPort $BackendPort
  $FrontendPort = Resolve-Port -Name "前端" -HostName $FrontendHost -PreferredPort $FrontendPort

  $FrontendOpenUrl = "http://${FrontendHost}:${FrontendPort}/?chatHistoryOpen=true"

  Start-Backend -LangGraphExe $LangGraphExe
  Start-Frontend -PnpmExe $PnpmExe
  Start-Process $FrontendOpenUrl | Out-Null

  Write-Host ""
  Write-Host "Web AutoTest Agent 已启动。"
  Write-Host "前端地址：$FrontendOpenUrl"
  Write-Host "后端地址：http://${BackendHost}:${BackendPort}"
  Write-Host "后端日志：$BackendLogFile"
  Write-Host "前端日志：$FrontendLogFile"
  Write-Host "安装日志：$SetupLogFile"
  Write-Host "关闭本窗口或按 Ctrl+C 会停止本地服务。"

  while ($true) {
    if ($BackendProcess.HasExited) {
      Write-Host "后端进程已退出，请查看 $BackendLogFile"
      exit 1
    }
    if ($FrontendProcess.HasExited) {
      Write-Host "前端进程已退出，请查看 $FrontendLogFile"
      exit 1
    }
    Start-Sleep -Seconds 1
  }
} finally {
  Cleanup
}
