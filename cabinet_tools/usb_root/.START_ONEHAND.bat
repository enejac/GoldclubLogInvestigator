@echo off
setlocal EnableExtensions
rem USB root shortcut -> usb_scripts\slot\START_ONEHAND.bat
rem Soft RAM-clear (LogDaemonRamClear / SAS 0x7A) then start OneHand - no reboot.
set "TARGET=%~dp0usb_scripts\slot\START_ONEHAND.bat"
if not exist "%TARGET%" (
  echo ERROR: missing %TARGET%
  echo Expected layout: usb_scripts\roulette^|slot^|shared
  echo Deploy with: .\deploy_usb.ps1 -Dest ^<usb^>\ConfigScanner
  pause
  exit /b 1
)
call "%TARGET%" %*
exit /b %ERRORLEVEL%