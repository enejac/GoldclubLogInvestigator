@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Kill-ActiveGame -^> Kill-All -GameOnly
echo.
echo Kill-ActiveGame is merged into Kill-All (-GameOnly = UI only, services stay up).
echo.

if not exist "%~dp0Kill-All.ps1" (
  echo ERROR: Kill-All.ps1 not found next to this bat.
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Kill-All.ps1" -GameOnly %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
