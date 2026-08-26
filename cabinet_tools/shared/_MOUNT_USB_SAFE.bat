@echo off
setlocal EnableExtensions
cd /d "%~dp0"

net session >nul 2>&1
if errorlevel 1 (
  echo Requesting Administrator for SAFE BIWIN mount (EGM boot preserved)...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b 0
)

echo.
echo ============================================================
echo   BIWIN USB - SAFE mount (default before EGM boot)
echo   - Online disk + assign G: only
echo   - NO PnP reset, NO EFI mount changes, NO ghost E: hacks
echo   - Verifies bootx64.efi + BCD + goldclub.vhd
echo ============================================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Mount-UsbDiskNow.ps1"
set ERR=%ERRORLEVEL%

echo.
if %ERR% EQU 0 (
  echo BOOT READY - safe to unplug and boot EGM from this disk.
) else if %ERR% EQU 3 (
  echo BOOT CHECK FAILED - see log; do NOT boot EGM until fixed.
) else (
  echo Mount failed exit=%ERR%
)
echo Log: c:\Users\Ezbogar\GoldclubLogInvestigator\logs\mount-usb-disk-now.log
pause
exit /b %ERR%
