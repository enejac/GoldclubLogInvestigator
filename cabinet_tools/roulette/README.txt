Roulette cabinet tools

  Kill-All.bat                 Master kill (suspend shell + game + helpers + godot)
                               Kill-All.bat -GameOnly  = game/relaunchers/godot only
  Run-FullStack.bat            Resume shell.ps1 + services + nginx + BiOS + game
  Invoke-SoftwareVersionSwap.ps1  Surgical 7-file up/downgrade (GUI prefers hybrid SMB)
  Start-GameStart.bat          Launch game-start.exe only
  Start-GoldClubProcesses.bat  Services subset + HIH.exe
  Fix-SerialPortLocations.bat  locations.json: io:238-23F COM5 -> COM7 (Leds vs MUX UI drift)

Notes
  Kill-All freezes C:\goldclub\platform\user\shell.ps1 so game stays down.
  It never kills Open-AdminShell / elevated GoldClub Admin Shell.
  Kill order: suspend shell -> HIH/game-start/ruleta FIRST -> helpers -> godot LAST
  (+ re-sweep). Does NOT kill service hosts (CommCtrl/Aurum/LogDaemon/nginx).
  Software Version (LogInvestigator tab): Kill-All -> copy Frontend/Middleware/Backend
  binaries -> Run-FullStack. Source folder must contain ruleta-relative paths
  (godot\RouletteGui.pck, lib\*.dll, Ruleta.exe, ...).
  Run-FullStack: ordered services + nginx + ruleta + godot.exe direct
  (no HIH / leave shell suspended). From WinRM/Session 0, ruleta/Godot launch via
  Register-ScheduledTask LogonType Interactive as console user (goldclub) so UI
  is not splash-frozen in Session 0. Health FAILS if Godot/ruleta Session != console.
  Backup: Run-FullStack.ps1.bak-shell-hih
  Start-GoldClubProcesses refuses if shell.ps1 is live (use Run-FullStack or -Force).
  Fix-SerialPortLocations: Serial Port Config labeled Leds as COM5 because stale
  locations.json mapped io:238-23F to COM5; layout/ruleta keep Leds=7 and MUX=USB5.