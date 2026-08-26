@echo off
setlocal
echo [%date% %time%] SetSerial GST20664 starting...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0SetSerial_GST20664.ps1" %*
set ERR=%ERRORLEVEL%
echo [%date% %time%] SetSerial finished exit=%ERR%
echo Log: C:\goldclub\var\log\set_serial_GST20664.log
pause
exit /b %ERR%
