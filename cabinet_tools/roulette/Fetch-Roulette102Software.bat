@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Fetch-Roulette102Software
set "TOOL=%~dp0"
set "LOG=%TOOL%fetch-roulette102-software.log"
set "SCRIPT=%TOOL%Fetch-Roulette102Software.ps1"

echo === Copy 10.2 software (pre-mounted drives, NO admin) ===
echo.
echo Auto-detects 10.2 source (R:, P:, staging\) and writable dest (G:, W:).
echo Override: Fetch-Roulette102Software.bat -SourceGoldclubRoot R:\ -DestGoldclubRoot G:\
echo.
echo Log: %LOG%
echo.

if not exist "%SCRIPT%" (
  echo ERROR: %SCRIPT% not found
  goto END
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
set "RC=%ERRORLEVEL%"

echo.
if %RC% equ 0 (echo SUCCESS) else (echo FAILED exit %RC%)
if exist "%LOG%" (
  echo.
  echo --- last log lines ---
  powershell -NoProfile -Command "Get-Content '%LOG%' -Tail 30"
)

:END
echo.
echo Press any key to close...
pause >nul
exit /b %RC%