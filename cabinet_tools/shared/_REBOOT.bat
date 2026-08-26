@echo off
setlocal EnableExtensions
rem Reboot EGM: from PC (UNC) via WinRM, or locally when run on the cabinet.
set "PS1=%~dp0_REBOOT.ps1"
if not exist "%PS1%" (
  echo [ERROR] Missing %PS1%
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -UsbRoot "%~dp0."
set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" (
  echo [ERROR] exit %ERR%
  exit /b %ERR%
)
exit /b 0