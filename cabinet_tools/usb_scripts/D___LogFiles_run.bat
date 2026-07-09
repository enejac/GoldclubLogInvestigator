@echo off
:: zelen text
color 0A
:: rdeč text 
rem color 0C
@echo off 
TITLE Winsystems QA LAB Sezana_COPY FILES_Batch file_Created by: DejanJ !!!
cls
echo ****************************************************** 
echo ******************************************************
echo *****PLEASE WAIT FILE COPYING IS IN PROGRESS**********  
echo ******************************************************
echo ****************************************************** 
ping 0.0.0.0 -n 2 > nul

:: Get current date in format DD_MM_YYYY
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set "dt=%%a"
set "DD=%dt:~6,2%"
set "MM=%dt:~4,2%"
set "YYYY=%dt:~0,4%"
set "LogFolder=log_%DD%_%MM%_%YYYY%"

echo ******************************************
echo *****Terminating log writers (keep SAS/COM stack running)*****
echo ******************************************
:: Do NOT kill CommCtrl.exe / CommCtrlSAS.exe — that breaks live SAS over COM.
::taskkill /F /IM BiOS.exe > nul 2>&1
taskkill /F /IM bootstrap.exe > nul 2>&1
taskkill /F /IM OneHand.exe > nul 2>&1
echo ******************************************
ping 0.0.0.0 -n 2 > nul

echo *****Please wait... START Copying files******
echo ******************************************
echo *****Deleting previous log folders******************
set "LOG_ROOT=%~dp0"
for /d %%i in ("%LOG_ROOT%log*") do rd /s /q "%%i"
echo ******************************************
ping 0.0.0.0 -n 5 > nul
Xcopy /E /I /S  C:\Goldclub\var\log  "%LOG_ROOT%%LogFolder%"
echo ******************************************
ping 0.0.0.0 -n 2 > nul
echo *****Copying meter state (GCMessenger full stack)******
Xcopy /E /I /S  C:\Goldclub\var\state\GoldClub.Aurum.Services\GCMessenger  "%LOG_ROOT%%LogFolder%\state\GoldClub.Aurum.Services\GCMessenger"
echo ******************************************
ping 0.0.0.0 -n 2 > nul
Xcopy /E /I /S  C:\Goldclub\slot\HWDrivers  "%LOG_ROOT%%LogFolder%\HWDrivers"
echo ******************************************
ping 0.0.0.0 -n 2 > nul
Xcopy           C:\Goldclub\slot\Themes\HardwareConfig.xml  "%LOG_ROOT%%LogFolder%"
echo ******************************************
ping 0.0.0.0 -n 2 > nul
Xcopy C:\Goldclub\slot\Themes\mgconfig.xml  "%LOG_ROOT%%LogFolder%"
ping 0.0.0.0 -n 2  > nul
echo *****Delete Logfiles*************************
del /S /Q C:\Goldclub\var\log
echo ******************************************
ping 0.0.0.0 -n 2  > nul
echo *********************************************
echo **********Copy files COMPLETED*************
echo *********************************************
echo *****Restarting game + SAS services (COM link)******
net start "GoldClub Serial Communication Gateway SAS" > nul 2>&1
net start "GoldClub.Aurum.Services" > nul 2>&1
if exist "C:\Goldclub\slot\OneHand.exe" start "" /D "C:\Goldclub\slot" OneHand.exe
ping 0.0.0.0 -n 5 > nul
taskkill /f /im cmd.exe