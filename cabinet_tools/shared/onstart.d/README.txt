GoldClub onstart – share, WinRM, TeamViewer restore, Total Commander
====================================================================

91-EnableShareAndWinRM.ps1
  Deploy to either or both:
    C:\platform\system\init\onstart\91-EnableShareAndWinRM.ps1
    C:\goldclub\platform\system\init\onstart.d\91-EnableShareAndWinRM.ps1

  What it does:
    - SMB shares: slot (C:\goldclub), USB (D:\), ConfigScanner
    - local user test + File/Printer Sharing firewall
    - RustDesk LAN firewall: TCP 21118, UDP 21119, TCP 21114 (re-enabled every boot)
    - WinRM enable + listen check
    - One elevated PowerShell left open at goldclub logon
      (task GoldClub-ElevatedPowerShell, RunLevel Highest, -NoExit;
       WorkingDirectory D:\ConfigScanner; WindowStyle Minimized;
       helper C:\goldclub\var\state\onstart-share-winrm\Open-AdminShell.ps1)
    - TeamViewer via restore_tv_login_roulette.cmd (preferred) + Total Commander
      as the *auto-logon user* (goldclub)
      AtLogOn scheduled tasks + Startup\GoldClub-USB-DesktopTools.cmd
      All launches use start /min (or PowerShell -WindowStyle Minimized)
      Never Start-Process GUI as SYSTEM/session 0 (TV exits; TC vanishes at logon)
      Falls back to bare TeamViewerPortable\TeamViewer.exe only if restore cmd missing

  USB layout expected:
    D:\TeamViewer_LoginBackup\restore_tv_login_roulette.cmd
    D:\TeamViewer_LoginBackup\TeamViewer_HKCU_Roulette.reg
    D:\TeamViewer_LoginBackup\TeamViewer\   (portable profile data)
    D:\TeamViewerPortable\TeamViewer.exe
    D:\totalcmd\TOTALCMD64.EXE  (or TOTALCMD.EXE)

  restore_tv_login_roulette.cmd resolution (first valid hit wins):
    - next to this script / nearby TeamViewer_LoginBackup\
    - <USB>\TeamViewer_LoginBackup\restore_tv_login_roulette.cmd
    - D:\ConfigScanner\scripts\roulette\ (only if backup payload is beside it)
    - C:\goldclub\TeamViewer_LoginBackup\
    Candidate is valid only if TeamViewer_HKCU_Roulette.reg or TeamViewer\ sits beside the .cmd
    (the .cmd uses %~dp0 as its backup SRC).

  Repo copy of the restore cmd:
    cabinet_tools\shared\TeamViewer_LoginBackup\restore_tv_login_roulette.cmd

  After reboot check:
    C:\goldclub\var\log\onstart-share-winrm.log
    C:\goldclub\var\state\onstart-share-winrm\last-run.txt
    schtasks: GoldClub-TeamViewer-USB, GoldClub-TotalCommander-USB (Run as goldclub)
    C:\Users\goldclub\...\Startup\GoldClub-USB-DesktopTools.cmd
    Get-Process TeamViewer,TOTALCMD64 | select Name,SessionId  (SessionId should be 1)

  If last-run.txt is missing after reboot, EWF/UWF wiped the change (or script did not run).
