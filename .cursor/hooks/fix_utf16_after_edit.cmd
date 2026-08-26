@echo off
setlocal EnableExtensions
set "SCRIPT=%~dp0fix_utf16_after_edit.py"
py -3 "%SCRIPT%"
if %ERRORLEVEL%==0 exit /b 0
python "%SCRIPT%"
exit /b %ERRORLEVEL%