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
echo *****Terminating non-Windows Embedded tasks*****
echo ******************************************
::taskkill /F /IM BiOS.exe > nul 2>&1
taskkill /F /IM bootstrap.exe > nul 2>&1
taskkill /F /IM CommCtrl.exe > nul 2>&1
taskkill /F /IM CommCtrlSAS.exe > nul 2>&1
taskkill /F /IM GoldClub.Aurum.Services.e > nul 2>&1
taskkill /F /IM GoldClub.Logging.LogDaemo > nul 2>&1
taskkill /F /IM hwsubsys.exe > nul 2>&1
taskkill /F /IM MachineRemoteTools.exe > nul 2>&1
taskkill /F /IM OneHand.exe > nul 2>&1
taskkill /F /IM SlotConfigurationSync.exe > nul 2>&1
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
ping 0.0.0.0 -n 5 > nul
taskkill /f /im cmd.exe