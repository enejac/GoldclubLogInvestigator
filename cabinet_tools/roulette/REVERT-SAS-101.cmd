@echo off
setlocal EnableExtensions
title REVERT-SAS-101
echo Quarantine 10.2-signed DeviceManager and let GoldClub re-sign from 10.1 combo.
echo Does NOT touch licences or serialport. Does NOT rewrite the MAC.
set "PS1=D:\ConfigScanner\scripts\roulette\Revert-SasPaytables101.ps1"
if not exist "%PS1%" set "PS1=%~dp0Revert-SasPaytables101.ps1"
if not exist "%PS1%" (
  echo ERROR missing Revert-SasPaytables101.ps1
  exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
echo exit %ERRORLEVEL%
exit /b %ERRORLEVEL%
