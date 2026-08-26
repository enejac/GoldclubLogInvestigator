Slot / OneHand cabinet tools

  START_ONEHAND.bat / START_ONEHAND.ps1
      Soft RAM-clear without reboot, then start OneHand.
      Stops goldclub* services, wipes slot/Aurum/OneHand/logdaemon/aurum var,
      starts services + OneHand, waits for Aurum cabinet ONLINE, then
      unclean-bounces LogDaemon and runs bin\LogDaemonRamClear.exe so SAS
      ConsumeEvent(RAMCLEAR) -> CBE029 / 0x7A (and OS_START -> 0x18/0x17).
      Optional -SeedCopy for empty-meter templates beside the script.

  slot1onlogon.ps1    Slot1 onlogon UI (deploy to USB as slot1onlogon.ps1)
