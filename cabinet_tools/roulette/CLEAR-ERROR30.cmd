@echo off
setlocal EnableExtensions
title Clear ERROR 30 leftovers - restart live Ruleta (keep licence)
echo.
echo Removes leftover USB/wrong-WIBU XML only, then restarts the Ruleta.exe already on disk.
echo Does NOT swap in 10.1. Does NOT overwrite config\licences or ruleta\licence.dll.
echo Does NOT use UAC RunAs (Secondary Logon is disabled on this cabinet).
echo A 10.2.0.0 beta will show LLAVE again; that is the date lock, not a bad licence file.
echo.
set "PS1=D:\usb_scripts\roulette\Clear-Error30.ps1"
if not exist "%PS1%" set "PS1=C:\goldclub\bin\Clear-Error30.ps1"
if not exist "%PS1%" set "PS1=D:\ConfigScanner\scripts\roulette\Clear-Error30.ps1"
if not exist "%PS1%" (
  echo ERROR missing Clear-Error30.ps1
  pause
  exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
echo exit %ERRORLEVEL%
pause
exit /b %ERRORLEVEL%
