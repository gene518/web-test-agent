@echo off
REM Windows entry point - launches the PowerShell script.
REM Double-click this file or run: windows-start.bat [start|end|logs]

setlocal
set "MODE=%~1"
if "%MODE%"=="" set "MODE=start"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows-start.ps1" -Mode "%MODE%"

set "PS_EXIT=%ERRORLEVEL%"
echo.
echo [windows-start.bat] Exit code: %PS_EXIT%
echo Press any key to close...
pause >nul
endlocal
exit /b %PS_EXIT%
