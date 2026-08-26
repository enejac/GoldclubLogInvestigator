Cabinet USB scripts (canonical)

  roulette\   Kill-All (services+game), Run-FullStack, Alegro / Godot / ruleta tools
  slot\       OneHand / slot cabinet tools
  shared\     Any cabinet (share, scan, onlogon, USB mount, WinRM, RustDesk)
  usb_root\   Thin .bat shortcuts for the USB stick root only

On the stick:
  <usb>\Kill-All.bat                         -> forwarder
  <usb>\ConfigScanner\scripts\roulette\...   -> real scripts + .ps1 companions
  <usb>\usb_scripts\roulette|slot|shared\    -> mirror (onlogon / legacy)

Kill-All.bat is the single kill entry (stops GoldClub services by default).
Old names Kill-ActiveGame / Kill-GoldClubProcesses forward into Kill-All.

Deploy Log Investigator + scripts:
  .\deploy_usb.ps1 -Dest H:\ConfigScanner

Push scripts + root shortcuts to live cabinet USB share:
  .\cabinet_tools\Deploy-UsbDriveScripts.ps1

Do not put one-off debug scripts here. Lab AFT research stays under lab\.
