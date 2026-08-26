@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Fetch-Roulette102Software-Admin
set "TOOL=%~dp0"
set "SCRIPT=%TOOL%Fetch-Roulette102Software-Admin.ps1"

net session >nul 2>&1
if errorlevel 1 (
  echo This variant mounts goldclub.vhd and REQUIRES Administrator.
  echo On UAC-blocked PCs use Fetch-Roulette102Software.bat instead.
  echo.
  echo Requesting Administrator...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  echo If the elevated window did not open, run this bat as Administrator.
  goto END
)

if not exist "%SCRIPT%" (
  echo ERROR: %SCRIPT% not found
  goto END
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
set "RC=%ERRORLEVEL%"
echo.
echo Exit code: %RC%
:END
echo.
echo Press any key to close...
pause >nul
exit /b %RC%