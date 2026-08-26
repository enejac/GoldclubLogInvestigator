@echo off
setlocal EnableExtensions
title RESTART-STACK-NOW
echo Restart GoldClub stack so Ruleta 10.1 reloads remapped paytables.
echo Does NOT touch licences or serialport.
set "KILL=D:\usb_scripts\roulette\Kill-All.ps1"
set "RUN=D:\usb_scripts\roulette\Run-FullStack.ps1"
set "FIX=C:\goldclub\bin\Fix-CrashLoop.ps1"
if exist "%FIX%" (
  echo Fix-CrashLoop FilesOnly
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%FIX%" -Force -FilesOnly
)
if exist "%KILL%" (
  echo Kill-All
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%KILL%" -AlreadyElevated
)
if exist "%RUN%" (
  echo Run-FullStack
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%RUN%" -AlreadyElevated
  echo exit %ERRORLEVEL%
  exit /b %ERRORLEVEL%
)
echo ERROR missing Run-FullStack.ps1
exit /b 2
