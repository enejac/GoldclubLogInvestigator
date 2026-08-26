@echo off
setlocal EnableExtensions
rem USB root shortcut: always reboot the EGM (PC UNC -> WinRM, on EGM -> local)
set "PS1=%~dp0usb_scripts\shared\_REBOOT.ps1"
if not exist "%PS1%" (
  echo ERROR: missing %PS1%
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -UsbRoot "%~dp0."
set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" (
  echo [ERROR] exit %ERR%
  pause
  exit /b %ERR%
)
exit /b 0