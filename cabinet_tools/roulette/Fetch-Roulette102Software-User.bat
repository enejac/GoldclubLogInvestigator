@echo off
setlocal EnableExtensions
cd /d "%~dp0"
REM Same as Fetch-Roulette102Software.bat - no admin, auto-detect mounted drives.
if not exist "%~dp0Fetch-Roulette102Software.bat" (
  echo ERROR: Fetch-Roulette102Software.bat not found
  echo Press any key to close...
  pause >nul
  exit /b 2
)
call "%~dp0Fetch-Roulette102Software.bat" %*
exit /b %ERRORLEVEL%