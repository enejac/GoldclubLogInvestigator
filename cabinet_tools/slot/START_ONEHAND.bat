@echo off
setlocal EnableExtensions
rem Soft RAM-clear without reboot, then start OneHand.
rem Real work is in START_ONEHAND.ps1 (LogDaemonRamClear -> SAS 0x7A soft meters cleared).
set "SCRIPT=%~dp0START_ONEHAND.ps1"
if not exist "%SCRIPT%" (
  echo ERROR: missing %SCRIPT%
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo START_ONEHAND failed with exit %RC%
  pause
)
exit /b %RC%