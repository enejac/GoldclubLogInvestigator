@echo off
setlocal EnableExtensions
rem USB root shortcut -> usb_scripts\roulette\RAM-CLEAR.cmd
set "TARGET=%~dp0usb_scripts\roulette\RAM-CLEAR.cmd"
if not exist "%TARGET%" (
  echo ERROR: missing %TARGET%
  echo Expected layout: usb_scripts\roulette\RAM-CLEAR.cmd
  pause
  exit /b 1
)
call "%TARGET%" %*
exit /b %ERRORLEVEL%
