@echo off
setlocal EnableExtensions
title Fix ERROR 30 clock (C: only)
echo.
echo Sets this cabinet clock to 2026-08-10, stops Windows time sync,
echo deletes ruleta\var HeapDataDateTime / Password / DynamicPaytable,
echo then FullStack. Does NOT write G:\ or goldclub.vhd.
echo Does NOT overwrite config\licences or ruleta\licence.dll.
echo Do not type the LLAVE keypad until this finishes.
echo.
set "PS1=D:\usb_scripts\roulette\Fix-Error30Clock.ps1"
if not exist "%PS1%" set "PS1=%~dp0Fix-Error30Clock.ps1"
if not exist "%PS1%" (
  echo ERROR missing Fix-Error30Clock.ps1
  pause
  exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
echo exit %ERRORLEVEL%
pause
exit /b %ERRORLEVEL%
