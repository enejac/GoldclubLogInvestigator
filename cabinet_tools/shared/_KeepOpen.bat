@echo off
rem Keep a console open after any bat/ps1 finishes.
rem Usage: _KeepOpen.bat SomeScript.bat
rem        _KeepOpen.bat SomeScript.ps1
setlocal EnableExtensions
cd /d "%~dp0"
title KeepOpen
if "%~1"=="" (
  echo Usage: %~nx0 Script.bat [args...]
  echo        %~nx0 Script.ps1 [args...]
  goto END
)
set "TARGET=%~1"
if not exist "%TARGET%" if exist "%~dp0%~nx1" set "TARGET=%~dp0%~nx1"
if not exist "%TARGET%" (
  echo ERROR: not found: %~1
  goto END
)
echo Running: "%TARGET%"
echo.
if /i "%~x1"==".ps1" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%TARGET%" %2 %3 %4 %5 %6 %7 %8 %9
) else (
  call "%TARGET%" %2 %3 %4 %5 %6 %7 %8 %9
)
set "RC=%ERRORLEVEL%"
echo.
echo Exit code: %RC%
:END
echo.
echo Press any key to close...
pause >nul
exit /b %RC%