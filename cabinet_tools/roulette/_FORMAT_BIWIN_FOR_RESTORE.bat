@echo off
setlocal
cd /d "%~dp0"

net session >nul 2>&1
if errorlevel 1 (
  echo Requesting Administrator...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b 0
)

echo.
echo  Wipe BIWIN ONLY for clean Macrium restore (NOT Samsung / H: ConfigScanner)
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Format-BiwinForMacriumRestore.ps1"
echo.
pause
exit /b %ERRORLEVEL%
