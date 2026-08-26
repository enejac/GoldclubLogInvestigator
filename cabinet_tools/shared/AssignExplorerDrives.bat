@echo off
setlocal
set "LOG=C:\Users\Ezbogar\GoldclubLogInvestigator\assign-letters.log"
set "IMG=C:\WIN_SYSTEMS\Images\Alegro_10.2_128GB_BIWIN.mrimg"
set "REF=C:\Program Files\Macrium\Reflect\Reflect.exe"

net session >nul 2>&1
if errorlevel 1 (
  echo Click YES on UAC to assign E: G: P: ...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b 1
)

> "%LOG%" echo === Assign E G P %DATE% %TIME% ===

echo Finding BIWIN disk...
for /f "tokens=2 delims=:" %%a in ('powershell -NoProfile -Command "(Get-Disk | ? FriendlyName -match 'BIWIN' | Select-Object -First 1).Number"') do set BDISK=%%a
if not defined BDISK set BDISK=1
echo BIWIN disk=%BDISK%>> "%LOG%"

(
  echo rescan
  echo select disk %BDISK%
  echo list partition
  echo select partition 1
  echo assign letter=E
  echo select partition 2
  echo assign letter=G
  echo list volume
  echo exit
) | diskpart >> "%LOG%" 2>&1

echo.>> "%LOG%"
echo --- E: --- >> "%LOG%"
if exist E:\ dir E:\ >> "%LOG%" 2>&1 else echo E: MISSING>> "%LOG%"

echo.>> "%LOG%"
echo --- G: --- >> "%LOG%"
if exist G:\ dir G:\ >> "%LOG%" 2>&1 else echo G: MISSING>> "%LOG%"

echo Mount 10.2 browse P:>> "%LOG%"
"%REF%" "%IMG%" -b -auto -drives *,P >> "%LOG%" 2>&1
timeout /t 4 /nobreak >nul

echo.>> "%LOG%"
echo --- P: --- >> "%LOG%"
if exist P:\ dir P:\ >> "%LOG%" 2>&1 else echo P: MISSING>> "%LOG%"

powershell -NoProfile -Command "$r=@(); foreach($l in 'E','G','P'){ if(Test-Path ($l+':\')){ $r += ($l+': OK '+((Get-Volume -DriveLetter $l -EA SilentlyContinue).FileSystemLabel)) } else { $r += ($l+': MISSING') } }; $r -join ' | '" >> "%LOG%"

echo === RESULT ===
type "%LOG%"
echo.
echo Explorer should show E: GCEFISYS, G: GCDATA0, P: 10.2 browse
pause
exit /b 0
