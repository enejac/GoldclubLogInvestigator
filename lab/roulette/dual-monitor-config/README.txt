# Dual-monitor (2 player stations) — proven on 10.0.0.90 2026-07-25

## Key setting
application.ruleta.setup → "number of player stations" = 2
(decrypt/encrypt via cabinet_tools/roulette/Convert-GcxmlSetup.ps1)

## Windows layout (already correct on .90)
DISPLAY1 1920x1080 @ 0,0     → player 1
DISPLAY2 1920x1080 @ 1920,0  → player 2
DISPLAY3 1920x1080 @ 3840,0  → unused

## godot.xml (unchanged; already had window2)
window1 position 0,0     playerIndexes 1
window2 position 1920,0  playerIndexes 2

## Other files applied
- configuredisplays/configuration.xml — bind player1@0,0 + player2@1920,0
- monitor/layout.json mode ADVANCED (Player01/Player02 PCI map)
- Restart ruleta in console session → 2 godot.exe:
  --players=1 --windowposition=0,0
  --players=2 --windowposition=1920,0

## Re-apply
1. Set stations=2 in setup (gcxml), write configuration.xml + ADVANCED layout
2. Kill ruleta/godot, start ruleta.exe in session 1

## Colombia currency (COP)
Lab `.90` uses the plain reference at `C:\Goldclub\tmp\setup.plain.xml` on the cabinet:
  COP bill slots, 2 player stations, `view format=euro`, `currency format=128`.
(Do **not** set `view format=cop` / `currency format=23` — that breaks the Godot label.)

```powershell
.\lab\roulette\Set-RouletteCurrencyCOP.ps1 -ComputerName 10.0.0.90 -Apply -RestartStack
```