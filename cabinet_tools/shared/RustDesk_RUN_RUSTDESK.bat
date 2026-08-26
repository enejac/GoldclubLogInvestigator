@echo off
set "source=%~dp0config"
set "dest=%APPDATA%\RustDesk\config"
set "rustdesk_exe=%~dp0rustdesk.exe"

:: Create destination folder if it doesn't exist
if not exist "%dest%" (
    mkdir "%dest%"
)

:: Copy all files from source to destination, overwriting existing files
xcopy "%source%\*.*" "%dest%" /Y /Q

:: Launch RustDesk
start "" "%rustdesk_exe%"
