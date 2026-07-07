@echo off
taskkill /IM OneHand.exe /F
net stop "GoldClub.Aurum.Services"
net stop "GoldClub Serial Communication Gateway SAS"
rd /s /q "C:\Goldclub\slot\var"
rd /s /q "C:\Goldclub\var\state\GoldClub.Aurum.Services"
xcopy "D:\goldclub.aurum.services" "C:\Goldclub\var\state\GoldClub.Aurum.Services" /E /Y /I
xcopy "D:\var" "C:\Goldclub\slot\var" /E /Y /I
cd /d C:\Goldclub\slot
start OneHand.exe
timeout /t 25
net start "GoldClub.Aurum.Services"
net start "GoldClub Serial Communication Gateway SAS"
exit