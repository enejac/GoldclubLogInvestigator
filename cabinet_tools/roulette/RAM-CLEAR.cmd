@echo off
setlocal EnableExtensions
title RAM Clear - official ramclear.d (keep licences)
echo.
echo Official GoldClub RAM clear (backup + cleanup + IdleMode).
echo Wipes roulette meters / Aurum var / ruleta\var / SAS bins.
echo Does NOT overwrite config\licences or ruleta\licence.dll.
echo Does NOT run ClearWibu (pass -ClearWibu to clear WIBU RAM slots).
echo Does NOT edit serialport layout.json / locations.json.
echo Then restarts FullStack. Use -SkipRestart to wipe only.
echo Does NOT use UAC RunAs (Secondary Logon is disabled on this cabinet).
echo.
set "HERE=%~dp0"
set "PS1=%HERE%Ram-Clear.ps1"
if not exist "%PS1%" set "PS1=D:\usb_scripts\roulette\Ram-Clear.ps1"
if not exist "%PS1%" set "PS1=%HERE%usb_scripts\roulette\Ram-Clear.ps1"
if not exist "%PS1%" (
  echo ERROR missing Ram-Clear.ps1
  pause
  exit /b 2
)
echo Script: %PS1%
echo Press Ctrl+C to abort, or any key to continue...
pause >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
echo exit %ERRORLEVEL%
pause
exit /b %ERRORLEVEL%
