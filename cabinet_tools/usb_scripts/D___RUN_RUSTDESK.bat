@echo off
cd D:\RustDesk
set "source=D:\RustDesk\config"
set "dest=%APPDATA%\RustDesk\config"
set "rustdesk_exe=D:\RustDesk\rustdesk.exe"

:: Create destination folder if it doesn't exist
if not exist "%dest%" (
    mkdir "%dest%"
)

:: Copy all files from source to destination, overwriting existing files
xcopy "%source%\*.*" "%dest%" /Y /Q

:: Launch RustDesk in minimized window
start /min "" "%rustdesk_exe%"