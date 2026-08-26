@echo off
:: Start portable TeamViewer. Do NOT run restore_tv_login_roulette.cmd
:: (that script taskkill /F TeamViewer.exe first).
::
:: TV always StartService(Spooler). START_TYPE 4 pops 1058
:: "The service cannot be started, either because it is disabled..."
:: Set demand-start only (not Automatic, do not net start). Hide XPS/VPN infs.
setlocal
set "TVEXE=%~dp0TeamViewerPortable\TeamViewer.exe"
if not exist "%TVEXE%" set "TVEXE=D:\TeamViewerPortable\TeamViewer.exe"
if not exist "%TVEXE%" (
  echo TeamViewer.exe not found. Expected TeamViewerPortable\TeamViewer.exe next to this cmd or on D:\
  exit /b 1
)
for %%I in ("%TVEXE%") do set "TVDIR=%%~dpI"
if exist "%TVDIR%Printer\TeamViewer_XPSDriverFilter.inf" (
  if exist "%TVDIR%Printer.disabled" rd /s /q "%TVDIR%Printer.disabled"
  move "%TVDIR%Printer" "%TVDIR%Printer.disabled"
)
if exist "%TVDIR%x64\TeamViewerVPN.inf" (
  if exist "%TVDIR%x64\TeamViewerVPN.inf.disabled" del /f /q "%TVDIR%x64\TeamViewerVPN.inf.disabled"
  move "%TVDIR%x64\TeamViewerVPN.inf" "%TVDIR%x64\TeamViewerVPN.inf.disabled"
)
sc.exe query spooler | find /I "DISABLED" >nul
if not errorlevel 1 sc.exe config spooler start= demand >nul 2>&1
tasklist /FI "IMAGENAME eq tv_w32.exe" | find /I "tv_w32.exe" >nul
if not errorlevel 1 (
  echo TeamViewer already running with helpers
  exit /b 0
)
taskkill /F /IM TeamViewer.exe >nul 2>&1
timeout /t 1 /nobreak >nul
echo Starting %TVEXE%
start "" "%TVEXE%"
exit /b 0
