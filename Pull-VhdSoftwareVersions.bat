@echo off
setlocal
set "ROOT=%~dp0"
set "SCRIPT=%ROOT%Pull-VhdSoftwareVersions.ps1"
set "LOG=%ROOT%pull-vhd-software-versions.log"

net session >nul 2>&1
if errorlevel 1 (
  echo.
  echo Administrator required to mount goldclub.vhd via diskpart.
  echo Click Yes on the UAC prompt...
  echo.
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs -ArgumentList '%*'"
  exit /b 1
)

echo === Pull ruleta software from VHD into D:\ConfigScanner\software_versions ===
echo Source: E:\goldclub.vhd (GCDATA0) or auto-find
echo Log: %LOG%
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
set RC=%ERRORLEVEL%

echo.
if %RC% equ 0 (echo SUCCESS) else (echo FAILED exit %RC%)
if exist "%LOG%" (
  echo.
  echo --- log tail ---
  powershell -NoProfile -Command "Get-Content '%LOG%' -Tail 20"
)
echo.
pause
exit /b %RC%
