@echo off
setlocal EnableExtensions
rem Launch RustDesk LAN on the EGM.
rem Lives next to _RUN_RUSTDESK.ps1 under usb_scripts\shared\
rem USB root shortcut: ..\..\..\..\_RUN_RUSTDESK.bat (forwarder)
rem Folder:  RustDesk-LAN-x64\rustdesk.exe on the stick root

set "PS1=%~dp0_RUN_RUSTDESK.ps1"
if not exist "%PS1%" (
  echo [ERROR] Missing %PS1%
  exit /b 1
)

rem Resolve USB stick root (where RustDesk-LAN-x64 lives)
set "USBROOT=%~dp0."
if exist "%~dp0..\..\..\RustDesk-LAN-x64\rustdesk.exe" (
  for %%I in ("%~dp0..\..\..") do set "USBROOT=%%~fI"
) else if exist "%~dp0..\..\RustDesk-LAN-x64\rustdesk.exe" (
  for %%I in ("%~dp0..\..") do set "USBROOT=%%~fI"
) else if exist "%~dp0..\RustDesk-LAN-x64\rustdesk.exe" (
  for %%I in ("%~dp0..") do set "USBROOT=%%~fI"
) else if exist "%~dp0RustDesk-LAN-x64\rustdesk.exe" (
  set "USBROOT=%~dp0."
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -UsbRoot "%USBROOT%"
set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" (
  echo [ERROR] exit %ERR%
  exit /b %ERR%
)
exit /b 0
