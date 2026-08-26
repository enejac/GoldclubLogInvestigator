@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

net session >nul 2>&1
if errorlevel 1 (
  echo Requesting Administrator for USB disk bring-to-life suite...
  set "ELEV_ARGS=%*"
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs -ArgumentList '!ELEV_ARGS!'"
  exit /b 0
)

echo.
echo ============================================================
echo   BIWIN USB Disk - Bring To Life Suite
echo   DEFAULT: SAFE mode (EGM boot preserved)
echo   Add -Recovery for aggressive USB reset (workstation only)
echo ============================================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Mount-UsbDiskNow.ps1" %*
set ERR=%ERRORLEVEL%

echo.
echo Log: c:\Users\Ezbogar\GoldclubLogInvestigator\logs\mount-usb-disk-now.log
if exist c:\Users\Ezbogar\GoldclubLogInvestigator\logs\mount-usb-disk-now.log (
  powershell -NoProfile -Command "Get-Content 'c:\Users\Ezbogar\GoldclubLogInvestigator\logs\mount-usb-disk-now.log' -Tail 40"
)
echo.
if %ERR% EQU 0 (
  echo SUCCESS. G: = data/Macrium. Run _CHECK_BIWIN_BOOT.bat before EGM boot.
) else if %ERR% EQU 3 (
  echo BOOT NOT READY - do not boot EGM until fixed. See log.
) else if %ERR% EQU 2 (
  echo HARDWARE FAILURE - unplug 30s, try rear USB 2.0, rerun with -ForceUsbMassStorage
) else (
  echo FAILED exit=%ERR% - see log above
)
echo.
pause
exit /b %ERR%