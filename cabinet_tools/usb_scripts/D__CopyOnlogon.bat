@echo off
:: Ensure the destination directory exists
mkdir "c:\Goldclub\platform\user\init\" 2>nul

:: Copy onlogon.ps1 from D:\ to c:\Goldclub\platform\user\init\, overwriting if exists
copy /Y "D:\onlogon.ps1" "c:\Goldclub\platform\user\init\onlogon.ps1"

:: Check if the copy was successful
if %ERRORLEVEL%==0 (
    echo File copied successfully.
) else (
    echo Error: Failed to copy file.
)
exit /b %ERRORLEVEL%