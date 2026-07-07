@echo off
setlocal EnableDelayedExpansion

:: -------------------------------------------------
:: CONFIG
:: -------------------------------------------------
set "SRC=%~dp0"
for %%I in ("%~dp0..") do set "USB_ROOT=%%~fI"
set "TVEXE=%USB_ROOT%\TeamViewerPortable\TeamViewer.exe"
set "DST=%LOCALAPPDATA%\TeamViewer"

echo.
echo === TeamViewer Portable Login Restore ===
echo USB     : %USB_ROOT%
echo Source  : %SRC%
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
if exist "%SRC%TeamViewer_HKCU.reg" (
    copy "%SRC%TeamViewer_HKCU.reg" "%DST%\TeamViewer_HKCU.reg" /Y >nul
    echo [OK] Registry file copied.
) else (
    echo [ERROR] TeamViewer_HKCU.reg not found in backup root!
    pause
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
        pause
        exit /b 1
    ) else (
        echo [OK] TeamViewer data copied.
    )
) else (
    echo [ERROR] TeamViewer subfolder not found!
    pause
    exit /b 1
)

:: -------------------------------------------------
:: 5. Import registry
:: -------------------------------------------------
echo [4/6] Importing registry...
reg import "%DST%\TeamViewer_HKCU.reg" >nul
if errorlevel 1 (
    echo [ERROR] Registry import failed!
    pause
    exit /b 1
) else (
    echo [OK] Registry imported.
)

:: -------------------------------------------------
:: 6. Launch TeamViewer
:: -------------------------------------------------
echo [5/6] Starting portable TeamViewer...
if exist "%TVEXE%" (
    start "" "%TVEXE%"
    echo [6/6] Launched. You should be logged in!
) else (
    echo [ERROR] TeamViewer.exe not found: %TVEXE%
    echo [HINT] Keep TeamViewer_LoginBackup and TeamViewerPortable on the same USB root.
)

echo.
echo === RESTORE COMPLETE ===
echo.
