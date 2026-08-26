@echo off
mkdir "c:\Goldclub\platform\user\init\" 2>nul
mkdir "c:\Goldclub\platform\user\init\onlogon\" 2>nul
copy /Y "%~dp0onlogon.ps1" "c:\Goldclub\platform\user\init\onlogon.ps1"
if %ERRORLEVEL%==0 (
    echo Copied onlogon.ps1 to c:\Goldclub\platform\user\init\
    echo Log after reboot: c:\Goldclub\var\log\onlogon.log and USB\onlogon-run.log
) else (
    echo Error: Failed to copy onlogon.ps1
)
exit /b %ERRORLEVEL%