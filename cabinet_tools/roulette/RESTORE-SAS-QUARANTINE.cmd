@echo off
setlocal EnableExtensions
title RESTORE-SAS-QUARANTINE
echo Restore signed DeviceManager from the 13:36 quarantine (stop services first).
echo Does NOT touch licences or serialport.
set "PS1=D:\usb_scripts\roulette\Restore-SasQuarantine.ps1"
if not exist "%PS1%" set "PS1=%~dp0Restore-SasQuarantine.ps1"
if not exist "%PS1%" (
  echo ERROR missing Restore-SasQuarantine.ps1
  exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
echo exit %ERRORLEVEL%
exit /b %ERRORLEVEL%
