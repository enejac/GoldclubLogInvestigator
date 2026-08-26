@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Enable-WinRM
set "SCRIPT=%~dp0Enable-WinRM.ps1"

if not exist "%SCRIPT%" (
  echo ERROR: Enable-WinRM.ps1 not found next to this bat.
  pause
  exit /b 1
)

net session >nul 2>&1
if errorlevel 1 (
  echo WinRM setup needs Administrator on the cabinet.
  echo goldclub is NOT admin - UAC will ask for Windows admin ^(gcadmin^).
  echo.
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  if errorlevel 1 (
    echo Elevation cancelled or failed.
    echo Log on as gcadmin, or right-click this bat - Run as administrator.
    pause
    exit /b 1
  )
  exit /b 0
)

echo [%date% %time%] Enable WinRM for fast remote commands...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -NoElevate %*
set ERR=%ERRORLEVEL%
echo [%date% %time%] Enable-WinRM finished exit=%ERR%
echo Log: C:\goldclub\var\log\enable_winrm.log
if %ERR% NEQ 0 pause
exit /b %ERR%