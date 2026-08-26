@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Scan-CabinetSession
echo.
echo === Scan-CabinetSession ===
echo Folder: %CD%
echo.

if not exist "%~dp0Scan-CabinetSession.ps1" (
  echo ERROR: Scan-CabinetSession.ps1 not found next to this bat.
  echo Place both files in the same folder.
  goto END
)

echo Starting PowerShell scan...
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Scan-CabinetSession.ps1"
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
  echo PowerShell exit code: %RC%
) else (
  echo PowerShell finished OK.
)
if exist "%~dp0cabinet-session-scan.log" (
  echo Log: %~dp0cabinet-session-scan.log
) else (
  echo WARN: log file was not created.
)

:END
echo.
echo Press any key to close...
pause >nul
exit /b %RC%