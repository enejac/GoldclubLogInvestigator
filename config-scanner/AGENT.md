# Config Scanner — agent continuation

QA **config SHA1 snapshot + diff** feature, fully embedded in **Log Investigator** (GUI tab + `config_scanner/` Python package).

## Architecture

| Layer | Path |
|-------|------|
| GUI tab | `gui/config_scanner_tab.py`, `gui/config_scanner_worker.py` |
| Service API | `config_scanner/service.py` |
| Scan / diff / report | `config_scanner/scanner.py`, `xml_diff.py`, `report.py`, `build_version.py` |
| Bundled defaults | `config_scanner/assets/profiles.json`, `config.json`, `assets/templates/report.html` |
| Writable data | `config-scanner/` next to exe (USB) or repo root (dev) |

**PowerShell / `.bat` launchers were removed** — the GUI is the only entry point.

## USB deploy

```powershell
.\build_exe.ps1
.\deploy_usb.ps1              # default: H:\ConfigScanner\LogInvestigator.exe
.\deploy_usb.ps1 -Dest H:\tools
```

First run creates `config-scanner\` beside the exe (snapshots, reports, seeded config).

Existing snapshots on `H:\tools\config-scanner\` remain valid if present before upgrade.

## GUI workflow

1. **Scan now** — SHA1 all config files, save `{date}_build{N}_{time}/` under `snapshots/`.
2. **Compare latest two** / **Compare** / **Compare to baseline** — HTML report in `reports/`.
3. **Open snapshots folder** / **Open reports folder** — Explorer shortcuts for USB QA.

Settings: scan target + profile persisted in QSettings (`config_scanner/game_drive`, `config_scanner/profile_id`).

## Game profiles (`config_scanner/assets/profiles.json`)

| Profile | Scan target | Build tag | Scope |
|---------|-------------|-----------|--------|
| `roulette_usb` | `D:` | `ruleta/BuildVersion.txt` | `config\**` (~271 files) |
| `slot_lab_90` | `\\10.0.0.90\c$\Goldclub\slot` | SHA1 prefix of OneHand/game-start/Settings DLLs | slot root + hwdrivers + languages + themes/*.xml only (~41 files) |

Slot profile avoids scanning thousands of per-game theme assets under `themes\<Game>\`.

## Known limitations

- Content diff drill-down reads **live files on the game drive** (`D:\config\...`), not snapshot copies — same SHA1 summary either way, but HTML detail needs the USB mounted.
- Obfuscated `setup.xml` / `switches.xml` — path-level diff only, not menu labels (GoldClub.Settings hash map is future work).

## Tests

```powershell
python -m pytest tests/test_config_scanner.py
```

## History

Migrated 2026-07-13 from standalone USB PowerShell tool; GUI integration and removal of `.bat` entry points completed same session.
