@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title RunPs1

if "%~1"=="" (
  echo Usage: %~nx0 ScriptName.ps1 [args...]
  echo Example: %~nx0 Scan-CabinetSession.ps1
  echo.
  echo Or double-click Scan-CabinetSession.bat
  goto END
)

set "SCRIPT=%~1"
if not exist "%SCRIPT%" if exist "%~dp0%~nx1" set "SCRIPT=%~dp0%~nx1"
if not exist "%SCRIPT%" (
  echo ERROR: not found: %~1
  goto END
)

echo Running: "%SCRIPT%"
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %2 %3 %4 %5 %6 %7 %8 %9
set "RC=%ERRORLEVEL%"
echo.
echo Exit code: %RC%

:END
echo.
echo Press any key to close...
pause >nul
exit /b %RC%