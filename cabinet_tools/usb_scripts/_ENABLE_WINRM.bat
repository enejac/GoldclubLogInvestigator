@echo off
setlocal
echo [%date% %time%] Enable WinRM for fast remote commands...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Enable-WinRM.ps1" %*
set ERR=%ERRORLEVEL%
echo [%date% %time%] Enable-WinRM finished exit=%ERR%
echo Log: C:\goldclub\var\log\enable_winrm.log
if %ERR% NEQ 0 pause
exit /b %ERR%
