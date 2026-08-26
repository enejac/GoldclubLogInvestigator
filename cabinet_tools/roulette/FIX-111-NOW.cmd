@echo off
setlocal EnableExtensions
title FIX-111-NOW
echo Repair combo / MeterHost / TeamViewer / stack on this cabinet.
echo Does NOT touch licences or serialport.
set "PS1=%~dp0FIX-111-NOW.ps1"
if not exist "%PS1%" set "PS1=D:\FIX-111-NOW.ps1"
if not exist "%PS1%" set "PS1=C:\goldclub\bin\FIX-111-NOW.ps1"
if not exist "%PS1%" (
  echo ERROR missing FIX-111-NOW.ps1
  exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
echo exit %ERRORLEVEL%
exit /b %ERRORLEVEL%
