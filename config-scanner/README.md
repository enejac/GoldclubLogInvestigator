# Config Scanner data folder

Runtime storage for the **Config SHA1 Scanner** built into Log Investigator (`config_scanner/` Python package + **Config Scanner** GUI tab).

This folder is **not** a standalone tool — there are no `.bat` or PowerShell scripts here.

## Layout

```
config-scanner/          (next to LogInvestigator.exe on USB, or repo root in dev)
  config.json            seeded on first run from bundled assets
  templates/report.html  seeded on first run
  snapshots/             SHA1 manifests per scan session
  reports/               HTML diff reports
  baseline.json          optional baseline snapshot name (set from GUI)
```

## Usage

1. Point Scan target at the game root (auto-detect prefers local, then `\\10.0.0.90\c$\Goldclub` for roulette).
2. Run **`LogInvestigator.exe`** (or `python gui_app.py` in dev).
3. Open the **Config Scanner** tab → **Scan now**, **Compare latest two**, **Open report**.

On USB, copy only `dist\LogInvestigator.exe` to e.g. `H:\tools\`. The `config-scanner\` folder is created automatically beside the exe on first use.

Deploy helper: `.\deploy_usb.ps1` from the repo root.

## What gets scanned

From the game drive:

- `config\**\*.xml`, `*.ini`, `*.conf`, `*.json`, `*.dat`

Runtime folders (`var\`, logs) are excluded by scan roots in `config.json`.

## Agent docs

See [AGENT.md](AGENT.md) and the Python implementation in [`config_scanner/`](../config_scanner/).
