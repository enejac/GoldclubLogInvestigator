# Config Scanner data folder

Runtime storage for the **Config SHA1 Scanner** built into Log Investigator (`config_scanner/` Python package + **Config Scanner** window).

This folder is **not** a standalone tool — there are no `.bat` or PowerShell scripts here.

## Layout

```
config-scanner/          (next to LogInvestigator.exe on USB, or repo root in dev)
  config.json            seeded on first run from bundled assets
  templates/report.html  seeded on first run
  snapshots/             SHA1 manifests + archived files per scan
  reports/               HTML diff reports
  baseline.json          optional baseline snapshot name (set from GUI)
```

## Usage

1. Point **Scan target** at the game root (auto-detect prefers local, then `\\10.0.0.90\c$\Goldclub` for roulette).
2. Run **`LogInvestigator.exe`** (or `python gui_app.py` in dev).
3. Open **Config Scanner** from the connection bar (Ctrl+Shift+C) → **Scan now**, **Compare** / **Quick compare**, optional per-setting **Write**.

On USB, copy `dist\LogInvestigator.exe` beside a `config-scanner\` folder (created automatically on first use).

Deploy helpers: `.\deploy_usb.ps1`, `.\deploy_egm.ps1` (lab EGM, e.g. `.90`).

## Roulette notes

- Encrypted `config\etc\application\ruleta\setup.xml` is decrypted for meaningful setting diffs; write-back re-encrypts.
- `bios\etc\...` often junctions to `config\...` — duplicates are collapsed to the highest path (`config\etc\...`).
- After changing BiOS options, **save to disk** before scanning or the snapshot will not see the change.

## What gets scanned (roulette profile)

From the game root:

- `config\**`, `bios\etc\**`, `data\**` — patterns `*.{xml,ini,conf,json,dat}`

Runtime log folders under `var\` are outside the default scan roots.

## Agent docs

See [AGENT.md](AGENT.md) and the Python implementation in [`config_scanner/`](../config_scanner/). Root overview: [`README.md`](../README.md).
