@echo off
rem Elevated repair only (no TV/TC). Right-click Run as administrator, or from GoldClub Admin Shell:
rem   powershell -ExecutionPolicy Bypass -File FIX-SHARE-NOW.ps1
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0FIX-SHARE-NOW.ps1"
echo.
pause
