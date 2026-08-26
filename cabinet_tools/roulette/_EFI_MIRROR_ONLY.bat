@echo off
net session >nul 2>&1 || (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b 0
)
echo WORKSTATION ONLY: mounts EFI at G:\GCEFISYS for Explorer browsing.
echo Do NOT run this before booting the EGM from this disk.
pause
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Mount-UsbDiskNow.ps1" -EfiOnly -EfiBrowseMount
pause