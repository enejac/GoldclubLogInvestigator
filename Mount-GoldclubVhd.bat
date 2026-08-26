@echo off
setlocal
set "ROOT=%~dp0"
set "SCRIPT=%ROOT%Mount-GoldclubVhd.ps1"
set "LOG=%ROOT%mount-goldclub-vhd.log"

net session >nul 2>&1
if errorlevel 1 (
  echo Requesting Administrator - click Yes on UAC...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs -ArgumentList '%*'"
  exit /b 1
)

echo === Mount goldclub.vhd (diskpart, readonly by default) ===
echo Finds P:\goldclub.vhd or any drive with goldclub.vhd
echo Log: %LOG%
echo.
echo Tips:
echo   Mount-GoldclubVhd.bat
echo   Mount-GoldclubVhd.bat -MountLetter G
echo   Mount-GoldclubVhd.bat -Dismount
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
set RC=%ERRORLEVEL%

echo.
if %RC% equ 0 (echo SUCCESS) else (echo FAILED exit %RC%)
if exist "%LOG%" (
  echo.
  echo --- last log lines ---
  powershell -NoProfile -Command "Get-Content '%LOG%' -Tail 25"
)
echo.
pause
exit /b %RC%
