@echo off
:: Run on THIS lab PC. Drops stale SMB sessions to 10.0.0.111, forgets GOLD-CLUB\test
:: (this cabinet is GRT330106 workgroup - that domain account does not exist there),
:: then maps USB_Remote + slot as the current Windows user (Everyone shares).
:: Do not open C$. Do not enter GOLD-CLUB\test.
set "IP=10.0.0.111"
echo Disconnecting stale sessions to %IP% ...
net use \\%IP%\c$ /delete /y >nul 2>&1
net use \\%IP%\slot /delete /y >nul 2>&1
net use \\%IP%\ipc$ /delete /y >nul 2>&1
net use \\%IP%\USB_Remote /delete /y >nul 2>&1
net use \\%IP%\USB /delete /y >nul 2>&1
cmdkey /delete:%IP% >nul 2>&1
echo Mapping USB_Remote and slot (no GOLD-CLUB\test) ...
net use \\%IP%\USB_Remote
if errorlevel 1 (
  echo FAILED USB_Remote
  pause
  exit /b 1
)
net use \\%IP%\slot
echo OK - opening slot (roulette) and USB_Remote. Do NOT open \\%IP%\c$
echo Do NOT type GOLD-CLUB\test - this cabinet has no such account.
start explorer \\%IP%\slot
start explorer \\%IP%\USB_Remote
exit /b 0
