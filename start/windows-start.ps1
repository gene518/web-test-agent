# windows-start.ps1 - Web AutoTest Agent Windows 启动脚本
# 使用方式：
#   右键此文件 -> "使用 PowerShell 运行"
#   或在 PowerShell 中执行：
#     .\start\windows-start.ps1 [start|end|logs]
#
# 如果触发执行策略拦截，执行：
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

param(
  [ValidateSet("start", "end", "logs")]
  [string]$Mode = "start"
)

$ErrorActionPreference = "Stop"

# Windows PowerShell 默认会继承系统代码页（中文系统通常是 CP936）。LangGraph CLI
# 在应用加载前会输出 Unicode 警告符号，因此必须在启动任何 Python 子进程前统一为 UTF-8。
$Utf8Encoding = New-Object System.Text.UTF8Encoding $false
[Console]::InputEncoding = $Utf8Encoding
[Console]::OutputEncoding = $Utf8Encoding
$OutputEncoding = $Utf8Encoding
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# ScriptPath 指向 .ps1 自身
$ScriptPath = $MyInvocation.MyCommand.Path
$ScriptDir = Split-Path -Parent $ScriptPath

$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")

$StartDir = Join-Path $ProjectRoot "start"

$BackendDir = Join-Path $ProjectRoot "web-agent"

$FrontendDir = Join-Path $ProjectRoot "web-portal"

$BackendEnvFile = Join-Path $BackendDir ".env"

$StartScriptPath = $ScriptPath

$BackendLogFile = Join-Path $StartDir "backend.log"



# 平台相关的持久化状态目录：用 Windows 标准的 %LOCALAPPDATA%\WebAutoTestAgent，

# 不再在项目 start/ 下创建 .cache，保持仓库目录干净。

$AppStateDir = if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA "WebAutoTestAgent" } else { Join-Path $env:USERPROFILE ".webautotestagent" }



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

$ClientBackendOnly = "0"



$BackendProcess = $null

$FrontendProcess = $null

$CleanedUp = $false

$StartupStepIndex = 0

$StartupTotalSteps = 13

$StopStepIndex = 0

$StopTotalSteps = 4



New-Item -ItemType Directory -Force -Path $AppStateDir | Out-Null



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

    if ($Value.Length -ge 2) {

      $DoubleQuote = [string][char]34

      $SingleQuote = [string][char]39

      $StartsAndEndsWithDoubleQuote = $Value.StartsWith($DoubleQuote) -and $Value.EndsWith($DoubleQuote)

      $StartsAndEndsWithSingleQuote = $Value.StartsWith($SingleQuote) -and $Value.EndsWith($SingleQuote)

      if ($StartsAndEndsWithDoubleQuote -or $StartsAndEndsWithSingleQuote) {

        $Value = $Value.Substring(1, $Value.Length - 2)

      }

    }



    [Environment]::SetEnvironmentVariable($Key, $Value, "Process")

  }



  Write-SetupLog "已加载项目配置：$BackendEnvFile"

}



function Import-ValidatedProjectEnvFile {
  Import-ProjectEnvFile
  if (-not $env:MASTER_LLM__MODEL -and -not $env:MASTER_MODEL) {
    Write-SetupLog "提示：Master 模型未配置，将使用项目默认值。"
  }
  if (-not $env:SPECIALIST_LLM__MODEL -and -not $env:SPECIALIST_MODEL) {
    Write-SetupLog "提示：Specialist 模型未配置，将使用项目默认值。"
  }

  if (-not $env:OPENAI_API_KEY -and -not $env:MASTER_LLM__API_KEY -and -not $env:SPECIALIST_LLM__API_KEY) {
    Write-SetupLog "提示：未检测到模型 API Key；V2 配置请分别填写 Master 与 Specialist 的独立密钥。"
  }
}


function Add-PathIfExists {

  param([string]$PathValue)

  if (Test-Path $PathValue) {

    $env:PATH = "$PathValue;$env:PATH"

  }

}



function Check-Git {

  $GitCommand = Get-Command "git.exe" -ErrorAction SilentlyContinue

  if (-not $GitCommand) {

    $GitCommand = Get-Command "git" -ErrorAction SilentlyContinue

  }



  if (-not $GitCommand) {

    Fail-Startup "未找到 Git。请先安装 Git 后再运行本脚本。推荐方式：访问 https://git-scm.com/download/win 下载 Git for Windows 安装包，或使用 winget 安装 ``winget install -e --id Git.Git``。"

  }



  $GitVersion = (& $GitCommand.Source --version 2>$null)

  Write-SetupLog "Git 已就绪：$($GitCommand.Source) ($GitVersion)"

}



function Resolve-NodeCommand {

  foreach ($Cmd in (Get-Command "node.exe" -All -ErrorAction SilentlyContinue)) {

    if ($Cmd.Source -and ($Cmd.Source -match "WindowsApps")) {

      continue

    }

    return $Cmd

  }

  return $null

}



function Check-Node {

  $NodeCommand = Resolve-NodeCommand

  if (-not $NodeCommand) {

    Fail-Startup "未找到 Node.js（已跳过 Windows 应用商店别名）。请先安装 Node.js 22 LTS 或更高版本后重试。推荐方式：访问 https://nodejs.org/ 下载安装包，或使用 winget 安装 ``winget install -e --id OpenJS.NodeJS.LTS``。"

  }



  $NodeVersionRaw = (& $NodeCommand.Source --version 2>$null).TrimStart("v")

  $NodeMajor = 0

  if (-not [int]::TryParse(($NodeVersionRaw -split '\.')[0], [ref]$NodeMajor)) {

    Fail-Startup "无法解析 Node.js 版本号：$NodeVersionRaw。请确认已正确安装 Node.js 22 LTS 或更高版本。"

  }



  if ($NodeMajor -lt 22) {

    Fail-Startup "检测到 Node.js 版本为 $NodeVersionRaw，项目需要 Node.js 22 LTS 或更高版本。请升级后重试。"

  }



  Write-SetupLog "Node.js 已就绪：$($NodeCommand.Source) (v$NodeVersionRaw)"

}



function Resolve-PythonCommand {

  $Candidates = @("python.exe", "python3.exe", "python")

  foreach ($Name in $Candidates) {

    foreach ($Cmd in (Get-Command $Name -All -ErrorAction SilentlyContinue)) {

      if ($Cmd.Source -and ($Cmd.Source -match "WindowsApps")) {

        continue

      }

      return [PSCustomObject]@{ Source = $Cmd.Source; Args = @() }

    }

  }



  $PyLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue

  if ($PyLauncher) {

    return [PSCustomObject]@{ Source = $PyLauncher.Source; Args = @("-3") }

  }



  return $null

}



function Check-Python {

  $PythonInfo = Resolve-PythonCommand

  if (-not $PythonInfo) {

    Fail-Startup "未找到 Python（已跳过 Windows 应用商店别名）。请先安装 Python 3.11 或更高版本后重试。推荐方式：访问 https://www.python.org/downloads/ 下载安装包，或使用 winget 安装 ``winget install -e --id Python.Python.3.11``。安装时务必勾选 ``Add Python to PATH``。安装完成后，建议在 ``设置 -> 应用 -> 应用执行别名`` 中关闭 python.exe 与 python3.exe 的 Microsoft Store 占位符。"

  }



  $PythonArgs = @($PythonInfo.Args) + @("--version")

  $PythonVersionOutput = (& $PythonInfo.Source @PythonArgs 2>&1 | Out-String).Trim()

  $PythonVersionMatch = [regex]::Match($PythonVersionOutput, "(\d+)\.(\d+)(?:\.(\d+))?")

  if (-not $PythonVersionMatch.Success) {

    Fail-Startup "无法解析 Python 版本号：$PythonVersionOutput。请确认已正确安装 Python 3.11 或更高版本。"

  }



  $PythonMajor = [int]$PythonVersionMatch.Groups[1].Value

  $PythonMinor = [int]$PythonVersionMatch.Groups[2].Value

  if (($PythonMajor -lt 3) -or (($PythonMajor -eq 3) -and ($PythonMinor -lt 11))) {

    Fail-Startup "检测到 Python 版本为 $($PythonVersionMatch.Value)，项目需要 Python 3.11 或更高版本。请升级后重试。"

  }



  $DisplaySource = if ($PythonInfo.Args.Count -gt 0) { "$($PythonInfo.Source) $($PythonInfo.Args -join ' ')" } else { $PythonInfo.Source }

  Write-SetupLog "Python 已就绪：$DisplaySource ($($PythonVersionMatch.Value))"

}



function Check-Uv {

  Add-PathIfExists (Join-Path $env:USERPROFILE ".local\bin")

  Add-PathIfExists (Join-Path $env:LOCALAPPDATA "Programs\uv")



  $UvCommand = Get-Command "uv" -ErrorAction SilentlyContinue

  if (-not $UvCommand) {

    $UvCommand = Get-Command "uv.exe" -ErrorAction SilentlyContinue

  }



  if (-not $UvCommand) {

    Fail-Startup "未找到 uv。请先安装 uv 后重试。推荐方式：执行 ``powershell -NoProfile -ExecutionPolicy Bypass -Command ""irm https://astral.sh/uv/install.ps1 | iex""``，或使用 winget 安装 ``winget install -e --id astral-sh.uv``，或访问 https://docs.astral.sh/uv/getting-started/installation/ 查看更多方式。"

  }



  Write-SetupLog "uv 已就绪：$($UvCommand.Source)"

  return $UvCommand.Source

}



function Check-Pnpm {

  $PnpmCommand = Get-Command "pnpm.cmd" -ErrorAction SilentlyContinue

  if (-not $PnpmCommand) {

    $PnpmCommand = Get-Command "pnpm" -ErrorAction SilentlyContinue

  }



  if (-not $PnpmCommand) {

    Fail-Startup "未找到 pnpm。请先安装 pnpm@10.5.1 后重试。推荐方式：启用 Corepack ``corepack enable; corepack prepare pnpm@10.5.1 --activate``，或使用 npm 全局安装 ``npm install -g pnpm@10.5.1``。"

  }



  $PnpmVersion = (& $PnpmCommand.Source --version 2>$null)

  Write-SetupLog "pnpm 已就绪：$($PnpmCommand.Source) ($PnpmVersion)"

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



function Build-Frontend {

  param([string]$PnpmExe)

  $env:NEXT_PUBLIC_API_URL = "http://${BackendHost}:${BackendPort}"

  $env:NEXT_PUBLIC_ASSISTANT_ID = $AssistantId

  $env:NEXT_PUBLIC_AUTH_SCHEME = $AuthScheme

  Write-SetupLog "开始构建前端生产版本..."

  $NextBuildDir = Join-Path $FrontendDir ".next"

  if (Test-Path $NextBuildDir) {

    Remove-Item -LiteralPath $NextBuildDir -Recurse -Force

  }

  Push-Location $FrontendDir

  try {

    Invoke-LoggedExternalCommand -Command { & $PnpmExe build } -FailureMessage "前端生产构建失败。"

  } finally {

    Pop-Location

  }

}



function Install-PlaywrightBrowsers {

  $InstallBrowsers = if ($env:START_INSTALL_PLAYWRIGHT_BROWSERS) { $env:START_INSTALL_PLAYWRIGHT_BROWSERS } else { "true" }

  $MarkerFile = Join-Path $AppStateDir "playwright-chromium-installed"

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

  $SingleQuote = [string][char]39

  return $SingleQuote + $Value.Replace($SingleQuote, $SingleQuote + $SingleQuote) + $SingleQuote

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

  # 说明：

  # - LangGraph / Python logging 会把 INFO 级日志写到 stderr，PowerShell 默认会把 native exe 的

  #   stderr 行包装成 ErrorRecord，被 Tee-Object 或控制台渲染成看起来很像报错的红字块。

  #   这里显式把 ErrorRecord 取回它的 Exception.Message，其他对象转成字符串，保证写入

  #   backend.log 和当前窗口的全都是干净的文本。

  # - `--allow-blocking`：MCP stdio 会话建立时底层 anyio 会调 `os.access` 等同步 IO，

  #   LangGraph dev 的 BlockingCallDetector 会把这类调用判为非法并中断连接。业务侧

  #   已经把自家同步 IO 包到 asyncio.to_thread，但第三方 MCP 客户端内部仍有同步

  #   预检，这里统一在 dev 入口放行 blocking，避免误杀。

  $CommandText = "Set-Location -LiteralPath $BackendDirQuoted; `$ErrorActionPreference = 'Continue'; & $LangGraphExeQuoted dev --host $BackendHost --port $BackendPort --no-browser --allow-blocking$NoReloadArg$ServerLogLevelArg 2>&1 | ForEach-Object { if (`$_ -is [System.Management.Automation.ErrorRecord]) { `$_.Exception.Message } else { [string]`$_ } } | Tee-Object -FilePath $BackendLogQuoted -Append"

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

  $CommandText = "Set-Location -LiteralPath $FrontendDirQuoted; `$env:NEXT_PUBLIC_API_URL=$ApiUrlQuoted; `$env:NEXT_PUBLIC_ASSISTANT_ID=$AssistantIdQuoted; `$env:NEXT_PUBLIC_AUTH_SCHEME=$AuthSchemeQuoted; & $PnpmExeQuoted exec next start --hostname $FrontendHost --port $FrontendPort"

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



function Get-AncestorProcessIds {

  # 返回当前 PowerShell 进程自身 + 所有祖先进程 PID 的集合。

  # 用途：在 `end` 模式下避免把正在执行脚本的 cmd/PowerShell 自己当成 "启动脚本进程" 杀掉。

  $AncestorIds = New-Object System.Collections.Generic.HashSet[int]

  $CurrentId = $PID

  while ($CurrentId -gt 0) {

    if (-not $AncestorIds.Add([int]$CurrentId)) {

      break

    }

    $ProcessInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$CurrentId" -ErrorAction SilentlyContinue

    if (-not $ProcessInfo) {

      break

    }

    $ParentId = [int]$ProcessInfo.ParentProcessId

    if ($ParentId -le 0 -or $ParentId -eq $CurrentId) {

      break

    }

    $CurrentId = $ParentId

  }

  return $AncestorIds

}



function Get-ScriptProcessIds {

  param([string]$ScriptPath)



  $Ancestors = Get-AncestorProcessIds



  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |

    Where-Object {

      $_.CommandLine -and

      $_.CommandLine -like "*$ScriptPath*" -and

      (-not $Ancestors.Contains([int]$_.ProcessId))

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

  $ClientBackendOnly = if ($env:CLIENT_BACKEND_ONLY) { $env:CLIENT_BACKEND_ONLY } else { "0" }

  $script:StartupTotalSteps = if ($ClientBackendOnly -eq "1") { 11 } else { 13 }

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

  $UvExe = Check-Uv

  Complete-SetupStep "检查 uv"



  Start-SetupStep "检查 pnpm"

  $PnpmExe = Check-Pnpm

  Complete-SetupStep "检查 pnpm"



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

  if ($ClientBackendOnly -eq "1") {

    if (-not (Test-PortBindable -HostName $BackendHost -Port $BackendPort)) {

      Fail-Startup "后端端口 $BackendPort 已被占用，客户端模式不会改用其他端口。"

    }

  } else {

    $BackendPort = Resolve-Port -Name "后端" -HostName $BackendHost -PreferredPort $BackendPort

    $FrontendPort = Resolve-Port -Name "前端" -HostName $FrontendHost -PreferredPort $FrontendPort

  }

  Complete-SetupStep "解析 Python 与端口"



  if ($ClientBackendOnly -ne "1") {

    Start-SetupStep "构建前端生产版本"

    Build-Frontend -PnpmExe $PnpmExe

    Complete-SetupStep "构建前端生产版本"

  }



  $FrontendOpenUrl = if ($env:FRONTEND_OPEN_URL) { $env:FRONTEND_OPEN_URL } else { "http://${FrontendHost}:${FrontendPort}/?chatHistoryOpen=true" }



  Start-SetupStep "启动后端"

  Start-Backend -LangGraphExe $LangGraphExe

  Complete-SetupStep "启动后端"



  if ($ClientBackendOnly -ne "1") {

    Start-SetupStep "启动前端并尝试打开页面"

    Start-Frontend -PnpmExe $PnpmExe

    Open-FrontendUrl -Url $FrontendOpenUrl

    Complete-SetupStep "启动前端并尝试打开页面"

  }



  Write-Host ""

  Write-Host "Web AutoTest Agent 已启动。"

  if ($ClientBackendOnly -eq "1") {

    Write-Host "运行模式：桌面客户端后端专用"

  } else {

    Write-Host "前端地址：$FrontendOpenUrl"

  }

  Write-Host "后端地址：http://${BackendHost}:${BackendPort}"

  Write-Host "后端日志：$BackendLogFile"

  Write-Host "关闭本窗口或按 Ctrl+C 会停止本地服务。"



  while ($true) {

    if ($BackendProcess.HasExited) {

      Write-Host "后端进程已退出，请查看 $BackendLogFile"

      exit 1

    }

    if (($ClientBackendOnly -ne "1") -and $FrontendProcess.HasExited) {

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

  Stop-ScriptSessions -Label "windows-start.bat" -ScriptPath $StartScriptPath

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

    Write-Host "用法：windows-start.ps1 [start|end|logs]"

    exit 1

  }

}

