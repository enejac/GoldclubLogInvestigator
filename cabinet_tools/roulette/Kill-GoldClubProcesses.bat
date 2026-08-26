@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Kill-GoldClubProcesses -^> Kill-All
echo.
echo Kill-GoldClubProcesses is merged into Kill-All (services + game + nginx).
echo.

if not exist "%~dp0Kill-All.ps1" (
  echo ERROR: Kill-All.ps1 not found next to this bat.
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Kill-All.ps1" %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
