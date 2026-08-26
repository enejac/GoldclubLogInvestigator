@echo off
setlocal EnableExtensions
title Fix Ruleta crash loop (keep licence)
echo.
echo Stops HIH/Ruleta, sets active paytable to paytable_double_zero, relaunches.
echo Does NOT overwrite licences.
echo Run this from GoldClub Admin Shell (already elevated).
echo.
set "PS1=D:\ConfigScanner\scripts\roulette\Fix-Error30Loop.ps1"
if not exist "%PS1%" (echo ERROR missing %PS1% & pause & exit /b 2)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
echo exit %ERRORLEVEL%
pause
exit /b %ERRORLEVEL%