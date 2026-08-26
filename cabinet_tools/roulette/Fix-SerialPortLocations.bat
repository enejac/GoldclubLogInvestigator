@echo off
rem Align locations.json so Leds is COM7 (not legacy COM5 on io:238).
title Fix Serial Port Locations
cd /d "%~dp0"
echo.
echo Running Fix-SerialPortLocations.ps1 %*
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Fix-SerialPortLocations.ps1" %*
set "RC=%ERRORLEVEL%"
echo.
echo Exit code: %RC%
echo.
pause
exit /b %RC%
