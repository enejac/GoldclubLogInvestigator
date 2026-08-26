@echo off
setlocal EnableExtensions
rem Soft USB / slot shares for lab cabinets.
rem Success -> auto-close. Failure -> keep console open, then exit 0 (never fail onlogon).
rem Flags: -pause = always wait; -nopause = never wait.

set "PAUSE_MODE=auto"
if /i "%~1"=="-pause" set "PAUSE_MODE=always"
if /i "%~1"=="-nopause" set "PAUSE_MODE=never"

set "SHARE_OK=1"
set "USB_LETTER="
set "SLOT_PATH="

echo [_share] start %DATE% %TIME%

rem --- Slot / GoldClub volume (unquoted drive roots — "G:\" breaks cmd quoting) ---
if exist "G:\" (
  set "SLOT_PATH=G:\"
) else if exist "C:\Goldclub\" (
  set "SLOT_PATH=C:\Goldclub"
) else if exist "C:\goldclub\" (
  set "SLOT_PATH=C:\goldclub"
)

if defined SLOT_PATH (
  echo [_share] slot -^> %SLOT_PATH%
  net share slot /delete /y >nul 2>&1
  rem Drive-root paths must not use quotes ending in backslash.
  if /i "%SLOT_PATH%"=="G:\" (
    net share slot=G:\ /grant:everyone,FULL
  ) else (
    net share slot="%SLOT_PATH%" /grant:everyone,FULL
  )
  if errorlevel 1 (
    echo [FAIL] net share slot failed
    set "SHARE_OK=0"
  ) else (
    echo [OK] share slot
  )
) else (
  echo [WARN] no GoldClub root for slot share
)

rem --- USB stick letter ---
if exist "D:\TeamViewer_LoginBackup\restore_tv_login.cmd" set "USB_LETTER=D"
if not defined USB_LETTER if exist "D:\ConfigScanner\" set "USB_LETTER=D"
if not defined USB_LETTER if exist "D:\" set "USB_LETTER=D"
if not defined USB_LETTER if exist "E:\TeamViewer_LoginBackup\restore_tv_login.cmd" set "USB_LETTER=E"
if not defined USB_LETTER if exist "F:\TeamViewer_LoginBackup\restore_tv_login.cmd" set "USB_LETTER=F"

if defined USB_LETTER (
  echo [_share] USB -^> %USB_LETTER%:\  as USB_Remote
  net share USB_Remote /delete /y >nul 2>&1
  net share USB_Remote=%USB_LETTER%:\ /grant:everyone,FULL
  if errorlevel 1 (
    echo [FAIL] net share USB_Remote failed
    set "SHARE_OK=0"
  ) else (
    echo [OK] share USB_Remote
  )
  net share USB /delete /y >nul 2>&1
  net share USB=%USB_LETTER%:\ /grant:everyone,FULL >nul 2>&1
  if exist "%USB_LETTER%:\ConfigScanner\" (
    echo [_share] ConfigScanner -^> %USB_LETTER%:\ConfigScanner
    net share ConfigScanner /delete /y >nul 2>&1
    net share ConfigScanner="%USB_LETTER%:\ConfigScanner" /grant:everyone,FULL
    if errorlevel 1 (
      echo [WARN] net share ConfigScanner failed
    ) else (
      echo [OK] share ConfigScanner
    )
  )
) else (
  echo [FAIL] no USB stick found for USB_Remote
  set "SHARE_OK=0"
)

net user test >nul 2>&1
if errorlevel 1 (
  net user test test /add
  if errorlevel 1 (echo [WARN] could not create user test) else (echo [OK] created user test)
) else (
  echo [OK] user test already exists
)
net localgroup administrators test /add >nul 2>&1

echo [_share] done SHARE_OK=%SHARE_OK%

set "DO_PAUSE=0"
if /i "%PAUSE_MODE%"=="always" set "DO_PAUSE=1"
if /i "%PAUSE_MODE%"=="auto" if "%SHARE_OK%"=="0" set "DO_PAUSE=1"

if "%DO_PAUSE%"=="1" (
  echo.
  if "%SHARE_OK%"=="0" (
    echo [!] Share setup had failures - window kept open for diagnosis.
    echo     From workstation after fix: Open-CabinetUSB.ps1
  ) else (
    echo Press any key to close...
  )
  pause >nul
)

exit /b 0