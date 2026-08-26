@echo off
setlocal
set "ROOT=%~dp0"
set "SCRIPT=%ROOT%Unmount-GJP.ps1"
net session >nul 2>&1
if errorlevel 1 (
  echo Requesting Administrator - click Yes on UAC...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b 0
)
echo Unmounting G: J: P: ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
set RC=%ERRORLEVEL%
if exist "%ROOT%_tmp_logs\unmount-gjp.log" (
  echo.
  powershell -NoProfile -Command "Get-Content '%ROOT%_tmp_logs\unmount-gjp.log' -Tail 40"
)
echo.
if %RC% equ 0 (echo SUCCESS) else (echo DONE with code %RC% - see note in log if drives remain)
pause
exit /b %RC%