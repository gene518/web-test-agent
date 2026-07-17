param(
  [string]$OutputDirectory = "",
  [string]$ClientExecutable = "",
  [switch]$SkipSmokeTest
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$BackendDir = Join-Path $ProjectRoot "web-agent"
$ClientDir = Join-Path $ProjectRoot "web-agent-client"
if (-not $OutputDirectory) {
  $OutputDirectory = Join-Path $ProjectRoot "dist\windows-x64"
}
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$BuildRoot = Join-Path $OutputDirectory ".portable-build"
$PackageName = "Web-Test-Agent-Windows-x64"
$PackageRoot = Join-Path $BuildRoot $PackageName
$RuntimeDir = Join-Path $PackageRoot "runtime"
$AppDir = Join-Path $RuntimeDir "app"
$PythonDir = Join-Path $RuntimeDir "python"
$NodeDir = Join-Path $RuntimeDir "node"
$PlaywrightDir = Join-Path $RuntimeDir "playwright"
$BrowserDir = Join-Path $RuntimeDir "browsers"
$WebView2Dir = Join-Path $RuntimeDir "webview2"
$ConfigDir = Join-Path $PackageRoot "config"
$DataDir = Join-Path $PackageRoot "data\logs"
$WebView2Version = "150.0.4078.65"
$WebView2Url = "https://msedge.sf.dl.delivery.mp.microsoft.com/filestreamingservice/files/c00b9782-0422-4114-be27-8eec079b394d/Microsoft.WebView2.FixedVersionRuntime.$WebView2Version.x64.cab"

function Write-Step {
  param([string]$Message)
  Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Invoke-Checked {
  param(
    [string]$Command,
    [string[]]$Arguments,
    [string]$FailureMessage,
    [string]$WorkingDirectory = ""
  )
  if ($WorkingDirectory) {
    Push-Location $WorkingDirectory
  }
  try {
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
      throw "$FailureMessage (exit code: $LASTEXITCODE)"
    }
  } finally {
    if ($WorkingDirectory) {
      Pop-Location
    }
  }
}

if ($env:OS -ne "Windows_NT") {
  throw "This package must be built on Windows x64."
}
if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -ne "AMD64") {
  throw "Only an AMD64 Windows build host is supported."
}

$PythonExe = (Get-Command python.exe -ErrorAction Stop).Source
$NodeExe = (Get-Command node.exe -ErrorAction Stop).Source
$NpmExe = (Get-Command npm.cmd -ErrorAction Stop).Source
$UvExe = (Get-Command uv.exe -ErrorAction Stop).Source
if (-not $ClientExecutable) {
  $ClientExecutable = Join-Path $ClientDir "src-tauri\target\release\web-agent-client.exe"
}
$ClientExecutable = [IO.Path]::GetFullPath($ClientExecutable)
if (-not (Test-Path -LiteralPath $ClientExecutable -PathType Leaf)) {
  throw "Tauri release executable not found: $ClientExecutable"
}

$ClientPackage = Get-Content -LiteralPath (Join-Path $ClientDir "package.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$AppVersion = [string]$ClientPackage.version
$DemoPackage = Get-Content -LiteralPath (Join-Path $BackendDir "deep_agent\assets\demo\package.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$PlaywrightVersion = [string]$DemoPackage.devDependencies.'@playwright/test'
$ArchivePath = Join-Path $OutputDirectory "web-test-agent-$AppVersion-windows-x64-portable.zip"
$ChecksumPath = "$ArchivePath.sha256"

Write-Step "Create portable package layout"
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
Remove-Item -LiteralPath $BuildRoot -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $ArchivePath, $ChecksumPath -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $PackageRoot, $RuntimeDir, $AppDir, $PythonDir, $NodeDir, $PlaywrightDir, $BrowserDir, $WebView2Dir, $ConfigDir, $DataDir -Force | Out-Null
Copy-Item -LiteralPath $ClientExecutable -Destination (Join-Path $PackageRoot "Web Test Agent.exe")
Copy-Item -LiteralPath (Join-Path $ScriptDir "portable\README.txt") -Destination (Join-Path $PackageRoot "README.txt")

Write-Step "Prepare portable Python and backend"
Invoke-Checked -Command $UvExe -Arguments @(
  "sync", "--project", $BackendDir, "--extra", "dev", "--frozen", "--python", $PythonExe
) -FailureMessage "uv sync failed"
$PythonBase = (& $PythonExe -c "import sys; print(sys.base_prefix)").Trim()
if (-not (Test-Path -LiteralPath (Join-Path $PythonBase "python.exe") -PathType Leaf)) {
  throw "Invalid Python base directory: $PythonBase"
}
Copy-Item -Path (Join-Path $PythonBase "*") -Destination $PythonDir -Recurse -Force
foreach ($unusedPath in @("include", "libs", "Scripts", "Tools", "tcl", "Lib\test", "Lib\site-packages")) {
  Remove-Item -LiteralPath (Join-Path $PythonDir $unusedPath) -Recurse -Force -ErrorAction SilentlyContinue
}
$PortableSitePackages = Join-Path $PythonDir "Lib\site-packages"
New-Item -ItemType Directory -Path $PortableSitePackages -Force | Out-Null
Copy-Item -Path (Join-Path $BackendDir ".venv\Lib\site-packages\*") -Destination $PortableSitePackages -Recurse -Force
Get-ChildItem -LiteralPath $PortableSitePackages -Filter "__editable__*" -Force -ErrorAction SilentlyContinue |
  Remove-Item -Recurse -Force
Remove-Item -LiteralPath (Join-Path $PortableSitePackages "_virtualenv.pth") -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $PortableSitePackages "_virtualenv.py") -Force -ErrorAction SilentlyContinue
Copy-Item -LiteralPath (Join-Path $BackendDir "deep_agent") -Destination $AppDir -Recurse
Copy-Item -LiteralPath (Join-Path $BackendDir "langgraph.json") -Destination $AppDir
Copy-Item -LiteralPath (Join-Path $BackendDir "pyproject.toml") -Destination $AppDir
Copy-Item -LiteralPath (Join-Path $BackendDir "uv.lock") -Destination $AppDir
Copy-Item -LiteralPath (Join-Path $BackendDir ".env.example") -Destination (Join-Path $ConfigDir ".env")

Write-Step "Prepare portable Node.js and Playwright $PlaywrightVersion"
Copy-Item -LiteralPath $NodeExe -Destination (Join-Path $NodeDir "node.exe")
Invoke-Checked -Command $NpmExe -Arguments @(
  "install", "--prefix", $PlaywrightDir, "--no-package-lock", "--ignore-scripts",
  "--no-audit", "--no-fund", "--save-exact", "@playwright/test@$PlaywrightVersion"
) -FailureMessage "Playwright package installation failed"
$PortableNode = Join-Path $NodeDir "node.exe"
$PortablePlaywrightCli = Join-Path $PlaywrightDir "node_modules\playwright\cli.js"
$PreviousBrowserPath = $env:PLAYWRIGHT_BROWSERS_PATH
try {
  $env:PLAYWRIGHT_BROWSERS_PATH = $BrowserDir
  Invoke-Checked -Command $PortableNode -Arguments @($PortablePlaywrightCli, "install", "chromium") -FailureMessage "Chromium download failed"
} finally {
  $env:PLAYWRIGHT_BROWSERS_PATH = $PreviousBrowserPath
}

Write-Step "Download fixed WebView2 $WebView2Version"
$WebView2Cab = Join-Path $BuildRoot "webview2-x64.cab"
$WebView2Extract = Join-Path $BuildRoot "webview2-extracted"
Invoke-WebRequest -Uri $WebView2Url -OutFile $WebView2Cab -UseBasicParsing
New-Item -ItemType Directory -Path $WebView2Extract -Force | Out-Null
Invoke-Checked -Command "expand.exe" -Arguments @($WebView2Cab, "-F:*", $WebView2Extract) -FailureMessage "WebView2 extraction failed"
$WebView2Executable = Get-ChildItem -LiteralPath $WebView2Extract -Filter "msedgewebview2.exe" -Recurse -File | Select-Object -First 1
if (-not $WebView2Executable) {
  $WebView2Executable = Get-ChildItem -LiteralPath $WebView2Extract -Filter "msedge.exe" -Recurse -File | Select-Object -First 1
}
if (-not $WebView2Executable) {
  throw "The fixed WebView2 archive does not contain a browser executable."
}
Copy-Item -Path (Join-Path $WebView2Executable.Directory.FullName "*") -Destination $WebView2Dir -Recurse -Force

$PortablePython = Join-Path $PythonDir "python.exe"
$env:PYTHONPATH = "$AppDir;$PortableSitePackages"
$env:PYTHONUTF8 = "1"
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:WEB_TEST_AGENT_ENV_FILE = Join-Path $ConfigDir ".env"
$env:WEB_TEST_AGENT_NODE_EXECUTABLE = $PortableNode
$env:WEB_TEST_AGENT_PLAYWRIGHT_CLI = $PortablePlaywrightCli
$env:WEB_TEST_AGENT_PLAYWRIGHT_MODULES = Join-Path $PlaywrightDir "node_modules"
$env:PLAYWRIGHT_BROWSERS_PATH = $BrowserDir

Write-Step "Validate portable runtime"
Invoke-Checked -Command $PortablePython -Arguments @(
  "-c", "import deep_agent, langgraph_cli, langgraph_api, pydantic_core; print('portable Python imports OK')"
) -FailureMessage "Portable Python import check failed" -WorkingDirectory $AppDir
Invoke-Checked -Command $PortableNode -Arguments @($PortablePlaywrightCli, "--version") -FailureMessage "Portable Playwright CLI check failed"

if (-not $SkipSmokeTest) {
  Write-Step "Run Playwright and LangGraph smoke tests"
  $SmokeWorkspace = Join-Path $BuildRoot "smoke-workspace"
  New-Item -ItemType Directory -Path $SmokeWorkspace -Force | Out-Null
  Invoke-Checked -Command $PortablePython -Arguments @(
    "-c",
    "import sys; from deep_agent.core.config import AppSettings; from deep_agent.tools.playwright import PLAYWRIGHT_TEST_MCP_PROVIDER as p; p.prepare_workspace(AppSettings(), sys.argv[1])",
    $SmokeWorkspace
  ) -FailureMessage "Portable Playwright workspace provisioning failed" -WorkingDirectory $AppDir
  $SmokePlaywrightCli = Join-Path $SmokeWorkspace "node_modules\playwright\cli.js"
  @'
import { expect, test } from "@playwright/test";
test("portable Chromium", async ({ page }) => {
  await page.setContent("<h1>portable</h1>");
  await expect(page.locator("h1")).toHaveText("portable");
});
'@ | Set-Content -LiteralPath (Join-Path $SmokeWorkspace "portable.spec.ts") -Encoding UTF8
  Invoke-Checked -Command $PortableNode -Arguments @(
    $SmokePlaywrightCli, "test", "portable.spec.ts", "--reporter=line"
  ) -FailureMessage "Portable Chromium smoke test failed" -WorkingDirectory $SmokeWorkspace

  $SmokePort = 21249
  $SmokeStdout = Join-Path $BuildRoot "backend-smoke.log"
  $SmokeStderr = Join-Path $BuildRoot "backend-smoke-error.log"
  $env:OPENAI_API_KEY = "portable-build-smoke-key"
  $BackendProcess = Start-Process -FilePath $PortablePython -ArgumentList @(
    "-m", "langgraph_cli", "dev", "--host", "127.0.0.1", "--port", $SmokePort,
    "--no-browser", "--allow-blocking", "--no-reload", "--server-log-level", "ERROR",
    "--config", (Join-Path $AppDir "langgraph.json")
  ) -WorkingDirectory $AppDir -RedirectStandardOutput $SmokeStdout -RedirectStandardError $SmokeStderr -WindowStyle Hidden -PassThru
  try {
    $Ready = $false
    $Deadline = [DateTime]::UtcNow.AddSeconds(120)
    while ([DateTime]::UtcNow -lt $Deadline -and -not $BackendProcess.HasExited) {
      try {
        $Info = Invoke-RestMethod -Uri "http://127.0.0.1:$SmokePort/info" -TimeoutSec 2
        if ($Info.langgraph_py_version) {
          $Ready = $true
          break
        }
      } catch {
        Start-Sleep -Milliseconds 500
      }
    }
    if (-not $Ready) {
      $ErrorText = if (Test-Path -LiteralPath $SmokeStderr) { Get-Content -LiteralPath $SmokeStderr -Raw } else { "" }
      throw "Portable LangGraph smoke test failed. $ErrorText"
    }
  } finally {
    if (-not $BackendProcess.HasExited) {
      Stop-Process -Id $BackendProcess.Id -Force -ErrorAction SilentlyContinue
      $BackendProcess.WaitForExit()
    }
  }
  Remove-Item -LiteralPath $SmokeWorkspace -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath (Join-Path $AppDir ".langgraph_api") -Recurse -Force -ErrorAction SilentlyContinue
}

Get-ChildItem -LiteralPath $PackageRoot -Directory -Filter "__pycache__" -Recurse -ErrorAction SilentlyContinue |
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
$Manifest = [ordered]@{
  schemaVersion = 1
  product = "Web Test Agent"
  version = $AppVersion
  architecture = "x64"
  createdAt = [DateTime]::UtcNow.ToString("o")
  python = (& $PortablePython --version 2>&1 | Out-String).Trim()
  node = (& $PortableNode --version 2>&1 | Out-String).Trim()
  playwright = $PlaywrightVersion
  webview2 = $WebView2Version
}
$Manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $PackageRoot "portable-manifest.json") -Encoding UTF8

Write-Step "Create x64 portable ZIP"
Compress-Archive -LiteralPath $PackageRoot -DestinationPath $ArchivePath -CompressionLevel Optimal
$ArchiveHash = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash.ToLowerInvariant()
"$ArchiveHash  $([IO.Path]::GetFileName($ArchivePath))" | Set-Content -LiteralPath $ChecksumPath -Encoding ASCII
Write-Host "Portable ZIP: $ArchivePath" -ForegroundColor Green
Write-Host "SHA-256: $ArchiveHash" -ForegroundColor Green
