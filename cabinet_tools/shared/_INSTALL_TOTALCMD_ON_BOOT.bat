@echo off
setlocal
cd /d "%~dp0"
net session >nul 2>&1
if errorlevel 1 (
  echo Requesting Administrator...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b 0
)
echo Install Total Commander on every EGM reboot (SYSTEM)...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-TotalCommanderOnBoot.ps1" %*
pause
