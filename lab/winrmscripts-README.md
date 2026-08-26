# WinRM-based replacements for C:\Tools\PSTools scripts that used PsExec.

Lab credential: GOLD-CLUB\test / test (see LabWinRm.ps1).

WinRM is auto-enabled when needed: Ensure-LabWinRm / Invoke-LabWinRm push
Enable-WinRM.ps1 (or run D:\_ENABLE_WINRM.bat) via PsExec as SYSTEM, then wait
for port 5985. Manual D:\_ENABLE_WINRM.bat is only a last resort.

Scripts:
  LabWinRm.ps1              Shared helper (dot-sourced by others)
  Open_CMD_Remote.ps1       Console cmd.exe on Session 1
  Open_TTCMD_Remote.ps1     Total Commander on Session 1
  Open-CabinetUSB.ps1       Share USB as \\cabinet\USB_Remote
  Push-Credits.ps1          Kill onehand + push XML via SMB
  remote_ram_clear.ps1      Soft RAM clear: upload+run newest START_ONEHAND.ps1
                            (late LogDaemonRamClear / SAS 0x7A). -UseUsbBat for
                            D:\.START_ONEHAND.bat. Sidecar: scripts\slot\
  Surgical-StateUpdate.ps1  Stop service, inject state, start service
  2Surgical-StateUpdate.ps1 Emergency stop + inject + restart
  Dallas\*.ps1              Dallas/WinDivert tools (exe still in ..\Dallas\)

Double-click the matching .cmd next to each .ps1 (same basename).
Example: Open_CMD_Remote.cmd  ->  Open_CMD_Remote.ps1

Canonical copies in the Log Investigator repo: lab\LabWinRm.ps1,
lab\remote_ram_clear.ps1, cabinet_tools\slot\START_ONEHAND.ps1
(re-copy to this folder after edits).