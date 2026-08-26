@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title CabinetSuperadminAndShare

if /i "%~1"=="-ShareOnly" goto SHARE

if not exist "C:\goldclub\bin\Setup.exe" (
  echo ERROR: C:\goldclub\bin\Setup.exe not found
  echo Copy _JT25.BAT to C:\goldclub\bin and run it there for superadmin.
  goto END
)

if exist "C:\goldclub\bin\_JT25.BAT" (
  call "C:\goldclub\bin\_JT25.BAT"
) else (
  echo Running Setup.exe directly...
  pushd C:\goldclub\bin
  Setup.exe --setup application.ruleta.setup --user superadmin --mangler type0 --keyword "keyword here"
  popd
)
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto END

:SHARE
if exist "%~dp0_share.bat" (
  call "%~dp0_share.bat" -nopause
) else (
  echo ERROR: _share.bat not found
)
set "RC=%ERRORLEVEL%"
:END
echo.
echo Press any key to close...
pause >nul
exit /b %RC%