@echo off
rem Wrapper: Setup.exe superadmin then RunManteinanceTasks on a D__* task folder.
rem Usage: _RunElevated.bat D__JT25
rem        _RunElevated.bat D__JT25 -SkipSetup
setlocal EnableExtensions
cd /d "%~dp0"
title RunElevated
set "TASK=%~1"
if "%TASK%"=="" (
  echo Usage: %~nx0 D__JT25
  echo        %~nx0 D__JT25 -SkipSetup
  goto END
)
set "SKIP="
if /i "%~2"=="-SkipSetup" set "SKIP=-SkipSetup"
if not exist "%~dp0Run-MaintenanceTasks.ps1" (
  echo ERROR: Run-MaintenanceTasks.ps1 not found next to this bat.
  goto END
)
echo Running maintenance: %TASK% %SKIP%
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Run-MaintenanceTasks.ps1" -TaskFolder "%~dp0%TASK%" %SKIP%
set "RC=%ERRORLEVEL%"
echo.
echo Exit code: %RC%
:END
echo.
echo Press any key to close...
pause >nul
exit /b %RC%