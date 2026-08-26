@echo off
:: AUTO_ONLOGON: no pause - safe for usb_scripts onlogon
:: Roulette variant - uses TeamViewer_HKCU_Roulette.reg

setlocal EnableDelayedExpansion

:: -------------------------------------------------
:: CONFIG
:: -------------------------------------------------
set "SRC=%~dp0"
:: Walk up until TeamViewerPortable is found (USB root), or fall back to parent of SRC
set "USB_ROOT="
set "WALK=%~dp0"
set "DEPTH=0"
:find_usb_root
if exist "%WALK%TeamViewerPortable\TeamViewer.exe" (
  set "USB_ROOT=%WALK%"
  goto :usb_root_done
)
set /a DEPTH+=1
if !DEPTH! GEQ 8 goto :usb_root_fallback
for %%I in ("%WALK%..") do set "NEXT=%%~fI\"
if /I "!NEXT!"=="!WALK!" goto :usb_root_fallback
set "WALK=!NEXT!"
goto :find_usb_root
:usb_root_fallback
for %%I in ("%~dp0..") do set "USB_ROOT=%%~fI\"
:usb_root_done
set "TVEXE=%USB_ROOT%TeamViewerPortable\TeamViewer.exe"
set "DST=%LOCALAPPDATA%\TeamViewer"
set "REGFILE=TeamViewer_HKCU_Roulette.reg"

echo.
echo === TeamViewer Portable Login Restore (ROULETTE) ===
echo USB     : %USB_ROOT%
echo Source  : %SRC%
echo Reg     : %REGFILE%
echo Target  : %DST%
echo Portable: %TVEXE%
echo.

:: -------------------------------------------------
:: 1. Stop TeamViewer
:: -------------------------------------------------
echo [1/6] Stopping TeamViewer...
taskkill /F /IM TeamViewer.exe >nul 2>&1
taskkill /F /IM TeamViewer_Service.exe >nul 2>&1
timeout /t 2 >nul

:: -------------------------------------------------
:: 2. Create destination
:: -------------------------------------------------
if not exist "%DST%" md "%DST%"

:: -------------------------------------------------
:: 3. Copy REG file from backup root
:: -------------------------------------------------
echo [2/6] Copying registry file...
if exist "%SRC%%REGFILE%" (
    copy "%SRC%%REGFILE%" "%DST%\TeamViewer_HKCU.reg" /Y >nul
    echo [OK] %REGFILE% copied as TeamViewer_HKCU.reg
) else (
    echo [ERROR] %REGFILE% not found in backup root!
    exit /b 1
)

:: -------------------------------------------------
:: 4. Copy the ENTIRE TeamViewer folder content
:: -------------------------------------------------
echo [3/6] Copying TeamViewer data (from TeamViewer subfolder)...
if exist "%SRC%TeamViewer\" (
    xcopy "%SRC%TeamViewer\*" "%DST%\" /E /I /H /R /Y /K
    if errorlevel 1 (
        echo [ERROR] Failed to copy TeamViewer data
        exit /b 1
    ) else (
        echo [OK] TeamViewer data copied.
    )
) else (
    echo [ERROR] TeamViewer subfolder not found!
    exit /b 1
)

:: -------------------------------------------------
:: 5. Import registry
:: -------------------------------------------------
echo [4/6] Importing registry...
reg import "%DST%\TeamViewer_HKCU.reg" >nul
if errorlevel 1 (
    echo [ERROR] Registry import failed!
    exit /b 1
) else (
    echo [OK] Registry imported.
)

:: -------------------------------------------------
:: 6. Launch TeamViewer
:: -------------------------------------------------
echo [5/6] Starting portable TeamViewer...
if exist "%TVEXE%" (
    start /min "" "%TVEXE%"
    echo [6/6] Launched. You should be logged in!
) else (
    echo [ERROR] TeamViewer.exe not found: %TVEXE%
    echo [HINT] Keep TeamViewer_LoginBackup and TeamViewerPortable on the same USB root.
)

echo.
echo === RESTORE COMPLETE (ROULETTE) ===
echo.
:: Always exit so the logon console window closes (Startup / scheduled task).
exit /b 0
