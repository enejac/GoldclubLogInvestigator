@echo off
setlocal EnableExtensions
set "TARGET=%~dp0usb_scripts\roulette\PUSH-RULETA-102-684.cmd"
if not exist "%TARGET%" (
  echo ERROR: missing %TARGET%
  pause
  exit /b 1
)
call "%TARGET%" %*
exit /b %ERRORLEVEL%
