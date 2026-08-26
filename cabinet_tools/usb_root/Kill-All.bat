@echo off
setlocal EnableExtensions
rem USB root shortcut -> usb_scripts\...
set "TARGET=%~dp0usb_scripts\roulette\Kill-All.bat"
if not exist "%TARGET%" (
  echo ERROR: missing %TARGET%
  echo Expected layout: usb_scripts\roulette|slot|shared
  pause
  exit /b 1
)
call "%TARGET%" %*
exit /b %ERRORLEVEL%