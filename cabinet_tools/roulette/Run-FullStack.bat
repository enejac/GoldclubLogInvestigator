@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Run Full Stack
echo.
echo === Run-FullStack ===
echo Folder: %CD%
echo.

if not exist "%~dp0Run-FullStack.ps1" (
  echo ERROR: Run-FullStack.ps1 not found next to this bat.
  goto END
)

echo Starting PowerShell full-stack (elevates via SYSTEM task if UAC RunAs is disabled)...
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Run-FullStack.ps1" %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
  echo PowerShell FAILED. Exit code: %RC%
  echo Stack was NOT reported healthy - do not treat this as OK.
) else (
  echo PowerShell finished OK ^(health checks passed^).
)

:END
echo.
echo Press any key to close...
pause >nul
exit /b %RC%