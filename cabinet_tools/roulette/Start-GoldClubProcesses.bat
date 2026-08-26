@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Start GoldClub Processes
echo.
echo === Start-GoldClubProcesses ===
echo Folder: %CD%
echo.

if not exist "%~dp0Start-GoldClubProcesses.ps1" (
  echo ERROR: Start-GoldClubProcesses.ps1 not found next to this bat.
  echo Place both files in the same folder.
  goto END
)

echo Starting PowerShell start script...
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-GoldClubProcesses.ps1" %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
  echo PowerShell exit code: %RC%
) else (
  echo PowerShell finished OK.
)

:END
echo.
echo Press any key to close...
pause >nul
exit /b %RC%