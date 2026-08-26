@echo off
setlocal EnableExtensions
cd /d "%~dp0"

net session >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b 0
)

echo Checking BIWIN boot readiness (read-only)...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Mount-UsbDiskNow.ps1" -EfiOnly
set ERR=%ERRORLEVEL%
echo.
if %ERR% EQU 0 (echo PASS - disk bootable for EGM) else (echo FAIL - see log)
pause
exit /b %ERR%
