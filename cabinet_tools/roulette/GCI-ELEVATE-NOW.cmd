@echo off
rem One-time on GRT330106: register SYSTEM Kill-All elevation for remote restore.
setlocal
set "PS1=%~dp0Bootstrap-Elevate-111.ps1"
if not exist "%PS1%" set "PS1=D:\usb_scripts\roulette\Bootstrap-Elevate-111.ps1"
if not exist "%PS1%" set "PS1=%~dp0usb_scripts\roulette\Bootstrap-Elevate-111.ps1"
if not exist "%PS1%" (
    echo ERROR: Bootstrap-Elevate-111.ps1 not found.
    echo Put it next to this cmd or under D:\usb_scripts\roulette\
    echo.
    pause
    exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
if errorlevel 1 (
    echo.
    echo Bootstrap failed — see errors above.
)
pause
