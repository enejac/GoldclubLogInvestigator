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
2. **Compare** / **Quick compare** — HTML report in `reports/` (More menu: set baseline, delete, open report/folders).

Settings: scan target + profile persisted in QSettings (`config_scanner/game_drive`, `config_scanner/profile_id`).

## Game profiles (`config_scanner/assets/profiles.json`)

| Profile | Scan target | Build tag | Scope |
|---------|-------------|-----------|--------|
| `roulette_usb` | `\\10.0.0.90\c$\Goldclub` (or local D:/G:) | `ruleta/BuildVersion.txt` | `config\**` |
| `slot_lab_90` | `C:\Goldclub\slot` (or lab UNC) | SHA1 prefix of OneHand/game-start/Settings DLLs | slot root + hwdrivers + languages + themes/*.xml only |

Slot profile avoids scanning thousands of per-game theme assets under `themes\<Game>\`.

## Known limitations

- Content diff drill-down reads **live files on the scan target** (local or UNC such as `\\10.0.0.90\c$\Goldclub\config\...`), not snapshot copies — same SHA1 summary either way, but HTML detail needs that target reachable.
- Encrypted ruleta `setup.xml` (gcxml) is decrypted on **Scan now** via `Convert-GcxmlSetup.ps1` + `GoldClub.Settings.dll` (UNC libs are cached locally). Snapshot hash/archive/compare use plain `<node name="…">` settings XML. Write-back re-encrypts to live gcxml. Older encrypted-only snapshots still compare as opaque token churn until re-scanned.
- **Snapshot folder names include machine SN** when `ProductSerialNumber.json` / `MachineName` is readable (e.g. `2026-07-28_GRT330106_Ruleta_Alegro_Wing_v10.2_b40097_083000`). Same for baselines (`GRT330106_…_baseline`).
- **Cabinet profile / 10.1↔10.2 transfer:** on GRT330106 the archived 10.1 and 10.2 configs are almost identical (10 `setup.xml` leaves). They are still not interchangeable because signed DeviceManager (not in snapshots) keeps 10.2 paytable names. Plan: [ruleta-cabinet-profile-plan.md](ruleta-cabinet-profile-plan.md). Use write scope **Ruleta software only (keep cabinet profile)** (`binaries_only`) to push a matching pack without writing donor setup/SAS/licence. `Config + Ruleta software` still writes snapshot config first.
- **Machine identity:** `ProductSerialNumber` and `services/aurum/**` are never written. Live licence XML is never overwritten (10.1 ↔ 10.2 config restore must not refresh the dongle file). A different WIBU `LicenseeId` is never written. Missing licence may restore only when the snapshot serial matches. Per-field Write blocks `SerialNumber`, `EgmId`, `CabinetSerialNumber`, URIs, etc. Other XML restores merge live identity leaves before write (`config_scanner/machine_identity.py`). After restore onto Ruleta 10.1, 10.2-only paytable names are remapped (combo.dat / processorStatus / live AurumSetup `paytableId`); those JSON files are not written. **More → Clear ERROR 30 leftovers** deletes only USB/wrong-WIBU XML (`6051106…` / `12-12327444`). **Revert last restore** uses the recorded write scope (including Config + Ruleta software). On a cabinet, Kill-All always runs before restore/revert. **Auto-start stack** (default on) then runs Run-FullStack; uncheck it to leave the stack down. A four-part snapshot version (`10.2.0.827`) must match that pack — it will not silently push `10.2.0.684` (ERROR 30 trial).

## Tests

```powershell
python -m pytest tests/test_config_scanner.py
```

## History

Migrated 2026-07-13 from standalone USB PowerShell tool; GUI integration and removal of `.bat` entry points completed same session.
