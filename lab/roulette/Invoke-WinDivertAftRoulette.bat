@echo off
rem Roulette AFT inject wrapper (same console + pause).
title Roulette AFT Inject
cd /d "%~dp0"
echo.
echo Starting Invoke-WinDivertAftRoulette.ps1 %*
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Invoke-WinDivertAftRoulette.ps1" %*
set "RC=%ERRORLEVEL%"
echo.
echo Exit code: %RC%
echo.
pause
exit /b %RC%
