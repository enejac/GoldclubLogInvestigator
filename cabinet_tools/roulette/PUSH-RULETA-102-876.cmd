@echo off
setlocal EnableExtensions
title Push Ruleta 10.2.0.876 (keep licence)
echo.
echo Copies D:\ConfigScanner\software_versions\Ruleta_v10.2.0.876_build40119
echo onto C:\goldclub\ruleta (7 surgical files + BuildVersion.txt).
echo Does NOT overwrite config\licences or ruleta\licence.dll.
echo Deletes ruleta\var\HeapDataDateTime.dat and Password.dat, then FullStack.
echo.
set "PS1=D:\usb_scripts\roulette\Push-Ruleta102876.ps1"
if not exist "%PS1%" set "PS1=%~dp0Push-Ruleta102876.ps1"
if not exist "%PS1%" (
  echo ERROR missing Push-Ruleta102876.ps1
  pause
  exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
echo exit %ERRORLEVEL%
pause
exit /b %ERRORLEVEL%
