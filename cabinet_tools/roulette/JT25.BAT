@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title JT25
set "BIN=C:\goldclub\bin"
set "SETUP=%BIN%\Setup.exe"
set "USB=%~dp0"
set "LOG=%USB%JT25.log"

echo === JT25: superadmin + maintenance/share ===
echo Folder: %CD%
echo Log: %LOG%
echo.
>> "%LOG%" echo [%date% %time%] JT25 start

if not exist "%SETUP%" (
  echo ERROR: %SETUP% not found
  >> "%LOG%" echo ERROR Setup.exe missing
  goto END
)

cd /d "%BIN%"
>> "%LOG%" echo Running Setup.exe superadmin
"%SETUP%" --setup application.ruleta.setup --user superadmin --mangler type0 --keyword "keyword here"
set "RC=%ERRORLEVEL%"
>> "%LOG%" echo Setup.exe exit %RC%
if not "%RC%"=="0" (
  echo Setup.exe failed exit %RC%
  goto END
)

if exist "%BIN%\RunManteinanceTasks.1.ps1" (
  >> "%LOG%" echo Running RunManteinanceTasks D__JT25
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%BIN%\RunManteinanceTasks.1.ps1" -path "%USB%D__JT25" >> "%LOG%" 2>&1
) else (
  >> "%LOG%" echo WARN RunManteinanceTasks.1.ps1 missing - running _share.bat only
  if exist "%USB%_share.bat" (
    call "%USB%_share.bat" -nopause >> "%LOG%" 2>&1
  ) else (
    echo WARN: _share.bat missing
  )
)

>> "%LOG%" echo JT25 done
echo Done. Log: %LOG%
set "RC=0"
:END
echo.
echo Press any key to close...
pause >nul
exit /b %RC%