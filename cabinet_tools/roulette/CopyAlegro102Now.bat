@echo off
setlocal EnableDelayedExpansion
set "LOG=C:\Users\Ezbogar\GoldclubLogInvestigator\copy-alegro102-to-g.log"
set "IMG=C:\WIN_SYSTEMS\Images\Alegro_10.2_128GB_BIWIN.mrimg"
set "REF=C:\Program Files\Macrium\Reflect\Reflect.exe"

net session >nul 2>&1
if errorlevel 1 (
  echo Click YES on UAC to mount P:, assign G:, and copy 10.2 VHDs...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b 1
)

> "%LOG%" echo === Copy 10.2 to G: started %DATE% %TIME% ===

echo [%TIME%] Rescanning disks...
(
  echo rescan
  echo list disk
  echo exit
) | diskpart >> "%LOG%" 2>&1

echo [%TIME%] Finding BIWIN disk number...
for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command "(Get-Disk | ? FriendlyName -match 'BIWIN' | Select-Object -First 1).Number"`) do set BID=%%D
if not defined BID (
  echo FAIL: BIWIN disk not found >> "%LOG%"
  echo ERROR: BIWIN not found in Get-Disk
  pause
  exit /b 1
)
echo BIWIN is disk !BID! >> "%LOG%"

echo [%TIME%] Assigning G: to BIWIN partition 2...
(
  echo select disk !BID!
  echo list partition
  echo select partition 2
  echo assign letter=G
  echo exit
) | diskpart >> "%LOG%" 2>&1

if not exist G:\ (
  echo FAIL: G: not found >> "%LOG%"
  echo ERROR: G: drive not assigned. Check BIWIN is disk 1.
  pause
  exit /b 1
)
dir G:\ >> "%LOG%" 2>&1

if exist P:\goldclub.vhd (
  echo [%TIME%] P: already mounted with 10.2 payload >> "%LOG%"
) else (
  echo [%TIME%] Mounting 10.2 browse on P:...
  "%REF%" "%IMG%" -b -auto -drives *,P >> "%LOG%" 2>&1
  timeout /t 5 /nobreak >nul
)

if not exist P:\goldclub.vhd (
  echo FAIL: P:\goldclub.vhd missing >> "%LOG%"
  echo ERROR: 10.2 mount failed on P:
  pause
  exit /b 2
)

echo [%TIME%] Copying goldclub.vhd system.vhd system.efi P: -^> G: ...
echo This takes 30-90 minutes. Do not unplug BIWIN.
robocopy P:\ G:\ goldclub.vhd system.vhd system.efi /R:2 /W:5 /NP /LOG+:"%LOG%"
set RC=!ERRORLEVEL!

echo robocopy exit !RC! >> "%LOG%"
if !RC! GEQ 8 (
  echo FAIL robocopy >> "%LOG%"
  echo ERROR: robocopy failed with exit !RC!
  pause
  exit /b !RC!
)

echo [%TIME%] Verifying sizes...
powershell -NoProfile -Command "$f=@('goldclub.vhd','system.vhd','system.efi'); foreach($n in $f){$s=(Get-Item ('P:\'+$n)).Length; $d=(Get-Item ('G:\'+$n)).Length; Add-Content '%LOG%' ($n+': src='+$s+' dst='+$d); if($s-ne$d){exit 9}}; exit 0"
if errorlevel 1 (
  echo FAIL size mismatch >> "%LOG%"
  echo ERROR: file size mismatch after copy
  pause
  exit /b 9
)

echo === DONE %DATE% %TIME% === >> "%LOG%"
echo.
echo SUCCESS: 10.2 VHDs copied to G:
echo Detach P: in Macrium when ready (Existing Backups - Detach).
dir G:\goldclub.vhd G:\system.vhd G:\system.efi
pause
exit /b 0
