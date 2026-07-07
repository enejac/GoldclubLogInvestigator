@echo off
SETLOCAL EnableDelayedExpansion

:: --- CONFIGURATION ---
SET "SRC_DIR=d:\HWDrivers_ST3"
SET "LOCAL_DST=c:\Goldclub\slot\hwdrivers"
SET "REMOTE_DST=\\10.0.0.90\c$\Goldclub\slot\hwdrivers"
SET "FILES=Lights.xml Keyboard.xml"

echo [1/3] Checking Source Files...
if not exist "%SRC_DIR%\Lights.xml" (echo Missing Lights.xml in source & pause & exit)
if not exist "%SRC_DIR%\Keyboard.xml" (echo Missing Keyboard.xml in source & pause & exit)

:: --- LOCAL ATTEMPT ---
echo [2/3] Checking Local Destination: %LOCAL_DST%
if exist "%LOCAL_DST%\" (
    echo Local directory found. Replacing files...
    for %%F in (%FILES%) do (
        xcopy "%SRC_DIR%\%%F" "%LOCAL_DST%\" /Y /R
    )
    echo Local Update Complete.
    goto FINISH
)

:: --- REMOTE RETRY ATTEMPT ---
echo Local path not found. Checking Remote Destination: %REMOTE_DST%
if exist "%REMOTE_DST%\" (
    echo Remote path active. Starting Robocopy with Retries...
    :: /R:5 = 5 retries, /W:5 = 5 seconds wait between retries
    robocopy "%SRC_DIR%" "%REMOTE_DST%" %FILES% /R:5 /W:5 /NP /IS /IT
    
    if !ERRORLEVEL! LEQ 3 (
        echo Remote Update Successful.
    ) else (
        echo Robocopy failed with error code !ERRORLEVEL!.
    )
) else (
    echo ERROR: Could not find Local or Remote destination. Check network/permissions.
)

:FINISH
echo ------------------------------------------
echo Process Finished.
pause