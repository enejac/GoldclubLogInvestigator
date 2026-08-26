@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Start game-start
echo.
echo === Start-GameStart ===
echo.

REM Guard: parallel game-start while shell.ps1/HIH owns the chain causes a second
REM Godot UI ~10-20s later. Prefer Run-FullStack.bat, or Kill-All then this.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$shells=@(Get-CimInstance Win32_Process -EA SilentlyContinue|?{ $_.Name -match '^(powershell|pwsh)\.exe$' -and $_.CommandLine -match '(?i)[\\/]platform[\\/]user[\\/]shell\.ps1' -and $_.CommandLine -notmatch '(?i)Open-AdminShell' });" ^
  "$g=@(Get-CimInstance Win32_Process -EA SilentlyContinue|?{ $_.Name -and ($_.Name -like 'godot*' -or $_.Name -ieq 'Godot_v4.exe') });" ^
  "$gs=@(Get-Process -Name 'game-start','Start-Game' -EA SilentlyContinue);" ^
  "$hih=@(Get-Process -Name 'HIH' -EA SilentlyContinue);" ^
  "if($shells.Count -gt 0){ Write-Host ('REFUSE: platform shell.ps1 running (pid={0}). Use Run-FullStack.bat' -f ($shells.ProcessId -join ',')) -ForegroundColor Yellow; exit 2 };" ^
  "if($g.Count -gt 0){ Write-Host ('REFUSE: Godot already running (pid={0}). Use Kill-All.bat -GameOnly first.' -f (($g.ProcessId) -join ',')) -ForegroundColor Yellow; exit 3 };" ^
  "if($gs.Count -gt 0 -or $hih.Count -gt 0){ Write-Host 'REFUSE: game-start/HIH already running. Use Kill-All.bat / Run-FullStack.bat' -ForegroundColor Yellow; exit 4 };" ^
  "exit 0"
if errorlevel 1 (
  echo.
  pause
  exit /b %ERRORLEVEL%
)

set "BIN="
if exist "C:\goldclub\bin\game-start.exe" set "BIN=C:\goldclub\bin"
if not defined BIN if exist "G:\goldclub\bin\game-start.exe" set "BIN=G:\goldclub\bin"
if not defined BIN if exist "D:\goldclub\bin\game-start.exe" set "BIN=D:\goldclub\bin"

if not defined BIN (
  echo ERROR: game-start.exe not found under C:\goldclub\bin
  echo.
  pause
  exit /b 1
)

echo Bin: %BIN%
echo Starting game-start.exe ...
start "" /D "%BIN%" "%BIN%\game-start.exe"
echo Done.
echo.
pause
exit /b 0
