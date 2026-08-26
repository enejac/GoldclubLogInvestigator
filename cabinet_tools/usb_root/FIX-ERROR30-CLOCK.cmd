@echo off
setlocal EnableExtensions
set "TARGET=%~dp0usb_scripts\roulette\FIX-ERROR30-CLOCK.cmd"
if not exist "%TARGET%" (
  echo ERROR: missing %TARGET%
  pause
  exit /b 1
)
call "%TARGET%" %*
exit /b %ERRORLEVEL%
