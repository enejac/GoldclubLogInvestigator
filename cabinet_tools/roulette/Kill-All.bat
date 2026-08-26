@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Kill-All
echo.
echo === Kill-All ===
echo Folder: %CD%
echo.
echo Default: suspend shell + STOP GoldClub services + kill game + godot + nginx.
echo Game only:  Kill-All.bat -GameOnly   (keep services/nginx; UI only)
echo.

if not exist "%~dp0Kill-All.ps1" (
  echo ERROR: Kill-All.ps1 not found next to this bat.
  goto END
)

echo Starting PowerShell Kill-All (elevates via SYSTEM task if UAC RunAs is disabled)...
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Kill-All.ps1" %*
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