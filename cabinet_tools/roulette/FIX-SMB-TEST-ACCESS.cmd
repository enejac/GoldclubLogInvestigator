@echo off
:: Run ON the cabinet from GoldClub Admin Shell (elevated).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0usb_scripts\roulette\FIX-SMB-TEST-ACCESS.ps1"
if errorlevel 1 powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0FIX-SMB-TEST-ACCESS.ps1"
pause
