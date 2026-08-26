@echo off
setlocal EnableExtensions
rem USB root shortcut: start RustDesk LAN on EGM (logic in usb_scripts\shared)
set "PS1=%~dp0usb_scripts\shared\_RUN_RUSTDESK.ps1"
if not exist "%PS1%" (
  echo ERROR: missing %PS1%
  pause
  exit /b 1
)
rem UsbRoot = this stick root (RustDesk-LAN-x64 lives here)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -UsbRoot "%~dp0."
set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" (
  echo [ERROR] exit %ERR%
  exit /b %ERR%
)
exit /b 0