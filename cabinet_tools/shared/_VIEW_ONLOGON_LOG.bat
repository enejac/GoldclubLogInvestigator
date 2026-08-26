@echo off
echo === onlogon logs ===
echo.
if exist C:\goldclub\var\log\onlogon.log (
  echo --- C:\goldclub\var\log\onlogon.log ---
  type C:\goldclub\var\log\onlogon.log
  echo.
)
if exist "%~dp0onlogon-run.log" (
  echo --- %~dp0onlogon-run.log ---
  type "%~dp0onlogon-run.log"
  echo.
)
for %%D in (C D E F G H) do (
  if exist "%%D:\onlogon-run.log" (
    echo --- %%D:\onlogon-run.log ---
    type "%%D:\onlogon-run.log"
    echo.
  )
)
pause