@echo off
setlocal
echo Closing LogInvestigator (releases ConfigScanner\LogInvestigator.exe lock)...
taskkill /F /IM LogInvestigator.exe 2>nul
if errorlevel 1 (
  echo LogInvestigator was not running.
) else (
  echo LogInvestigator stopped.
)
timeout /t 2 /nobreak >nul
echo You can copy or deploy the exe now.
pause
