@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Unlock-WriteFilter
set "TOOL=%~dp0"
set "LOG=%TOOL%unlock-writefilter.log"
set "SCRIPT=%TOOL%Unlock-WriteFilter.ps1"

net session >nul 2>&1
if errorlevel 1 (
  echo Write filter unlock requires Administrator on the cabinet.
  echo Click Yes on UAC, or run from an elevated Command Prompt.
  echo.
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  echo If the elevated window did not open, run this bat as Administrator.
  goto END
)

if not exist "%SCRIPT%" (
  echo ERROR: Unlock-WriteFilter.ps1 not found next to this bat.
  goto END
)

echo === Check EWF/UWF write filter and unlock ===
echo Log: %LOG%
echo.
echo Adds -Reboot by default (GoldClub needs reboot after Disable-UWF).
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -Reboot %*
set "RC=%ERRORLEVEL%"

echo.
if %RC% equ 0 (echo UNLOCKED or already open) else if %RC% equ 10 (echo DISABLE scheduled - reboot pending) else (echo exit %RC%)
if exist "%LOG%" (
  echo.
  echo --- last log lines ---
  powershell -NoProfile -Command "Get-Content '%LOG%' -Tail 25"
)

:END
echo.
echo Press any key to close...
pause >nul
exit /b %RC%