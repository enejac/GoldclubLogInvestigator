Shared cabinet tools - single USB location
==========================================

On stick: H:\ConfigScanner\scripts\shared\

  Unlock-WriteFilter.*    EWF/UWF unlock (elevated)
  Enable-WinRM / _ENABLE_WINRM.bat
  _share.bat              slot + USB_Remote (+ USB) + ConfigScanner; pause only on fail
  Scan-CabinetSession.*
  onstart.d\91-EnableShareAndWinRM.ps1   boot share+WinRM (system init)

Roulette: ..\roulette\
Slot:     ..\slot\

See H:\ConfigScanner\SCRIPTS.txt