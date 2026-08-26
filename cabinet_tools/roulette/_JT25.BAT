@echo off
setlocal
cd /d "%~dp0"
echo === _JT25 Setup.exe superadmin ===
echo Folder: %CD%
echo.
if not exist "%~dp0Setup.exe" (
  echo ERROR: Setup.exe not found in this folder.
  echo Place this bat next to Setup.exe in C:\goldclub\bin
  goto END
)
"%~dp0Setup.exe" --setup application.ruleta.setup --user superadmin --mangler type0 --keyword "keyword here"
set "RC=%ERRORLEVEL%"
echo.
echo Setup.exe exit %RC%
:END
echo.
echo Press any key to close...
pause >nul
exit /b %RC%