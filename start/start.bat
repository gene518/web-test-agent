@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "POWERSHELL_EXE=powershell.exe"

where powershell.exe >nul 2>nul
if errorlevel 1 (
  echo 未找到 powershell.exe，无法启动 Web AutoTest Agent。
  pause
  exit /b 1
)

"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%start.ps1"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%EXIT_CODE%"=="0" (
  echo 启动脚本已退出，错误码：%EXIT_CODE%
) else (
  echo 启动脚本已退出。
)
pause
exit /b %EXIT_CODE%
