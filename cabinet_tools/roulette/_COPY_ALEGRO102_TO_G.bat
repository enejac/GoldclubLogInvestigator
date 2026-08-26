@echo off
setlocal
cd /d "%~dp0"
set "LOG=%~dp0copy-alegro102-to-g.log"
set "SCRIPT=%~dp0Copy-Alegro102ToRestoredG.ps1"

net session >nul 2>&1
if errorlevel 1 (
  echo Requesting Administrator - click Yes on UAC...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b 1
)

echo === Copy Alegro 10.2 VHDs to G: ===
echo Log: %LOG%
echo This may take 30-90 minutes. Do not unplug BIWIN or close this window.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
set RC=%ERRORLEVEL%

echo.
echo Exit code: %RC%
if exist "%LOG%" (
  echo.
  echo --- last log lines ---
  powershell -NoProfile -Command "Get-Content '%LOG%' -Tail 20"
)
echo.
pause
exit /b %RC%