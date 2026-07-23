# Goldclub Log Investigator

Modular Python tool to recursively scan Goldclub log directories (local or UNC), detect errors and warnings using configurable regex rules, associate them with an active **game/theme**, estimate a **first-cause** line, map **severity**, and write **Markdown** / **CSV** reports. The desktop app also covers **roulette QA**: config snapshots, software swap, bug sessions, and lab cabinet ops.

## Layout

| Module | Role |
|--------|------|
| `config.py` | Regex patterns, severity rules, scan defaults, probable-cause hints |
| `scanner.py` / `parser.py` / `reporter.py` | Discover, parse, report |
| `logic_validator.py` | Roulette bonus math check vs `data/RouletteBonusMath.json` |
| `aft_transfer.py` | AFT transfer lexicon and cross-layer correlator |
| `config_scanner/` | Roulette/slot **Config SHA1 Scanner** (scan, compare, write-back) |
| `ai_helper/` | Offline helper + **gcxml** decrypt for encrypted ruleta `setup.xml` |
| `network/` | Fleet, health, RAM clear, bug detector, software version swap, lab access |
| `gui/` | PySide6 desktop UI |
| `cabinet_tools/roulette/` | On-cabinet Kill-All / Run-FullStack / serial fix / version swap scripts |
| `lab/roulette/` | Roulette Dallas splice + WinDivert AFT lab scripts |
| `gui_app.py` | Launches the Qt GUI |

## Requirements

- Python 3.10+
- **GUI + DB:** `pip install -r requirements.txt` (PySide6, SQLAlchemy)
- Optional AI Helper: `pip install -r requirements-ai-helper.txt` + model fetch scripts

## Usage (CLI)

```bash
python log_analyzer.py
python log_analyzer.py "C:\Goldclub\var\log" -o report.csv --format csv --workers 12 -v
```

## Desktop GUI

```bash
pip install -r requirements.txt
python gui_app.py
```

Or run `dist\LogInvestigator.exe` (build with `.\build_exe.ps1`).

### Core analysis

- **Scan** on a `QThread` (local or UNC such as `\\10.0.0.90\c$\Goldclub\var\log`)
- **Incident table** (`QAbstractTableModel`), debounced filter, Root Cause inspector
- **Live Watch** — tail newest logs; **Case snapshot** zip for filtered incidents
- **Logic validator** — roulette bonus math CRITICAL rows when `RouletteBonusMath.json` defines `math`
- **History / SQLite** — local `goldclub_investigator.db`
- **Fleet Overview** — ping + SMB 445; lab creds via `Initialize-LabAccess.ps1` / `GOLD-CLUB\test`

### Roulette features (GUI)

| Feature | Where | What it does |
|---------|--------|----------------|
| **Config Scanner** | Connection bar → **Config Scanner** (Ctrl+Shift+C) | SHA1 snapshot of roulette `config` / `bios\etc` / `data`; compare; per-setting **Write** back to the live cabinet |
| **Software Version** | Lazy tab | Surgical Frontend / Middleware / Backend swap on roulette (Kill-All → copy package → Run-FullStack) |
| **Bug Detector** | Lazy tab | Session-oriented bug discovery from roulette / Godot / Aurum logs |
| **RAM Clear** | Connection bar | Slot or roulette RAM-clear chain; **skips `ClearWibuKey`** so licences are not wiped |
| **AI Helper** | Connection bar (Ctrl+Shift+A) | Offline search over config; decrypts encrypted ruleta `setup.xml` (gcxml) for readable settings |
| **Verify SAS Accounting** | Connection bar | Meter verify; roulette path expects **Godot** frontend (not only `Ruleta.exe`) |

Lab cabinets: register SMB/WinRM with `.\Initialize-LabAccess.ps1 -Verify` (known IPs include `10.0.0.90` roulette reference).

### Config Scanner (roulette QA)

1. Point **Scan target** at the game root (e.g. `\\10.0.0.90\c$\Goldclub` or local `D:`).
2. **Scan now** → snapshot under `config-scanner\snapshots\`.
3. Change BiOS options and **save to disk**, then scan again → **Compare** / **Quick compare**.
4. Review field diffs (e.g. `payoutAutoConfirm`); **Write** applies one side to the live machine.

Important roulette details:

- Encrypted live `config\etc\application\ruleta\setup.xml` is **decrypted** for hash/archive/compare (`Convert-GcxmlSetup` + `GoldClub.Settings.dll`). Write-back **re-encrypts**.
- On many cabinets `bios` is a **junction to `config`** — the same `setup.xml` must not be reported twice. The scanner keeps the highest path (`config\etc\...`).
- Status / progress / compare summary go to the **bottom log** only.
- Deploy: `.\build_exe.ps1` then `.\deploy_egm.ps1` or copy `dist\LogInvestigator.exe` to `\\10.0.0.90\usb\ConfigScanner\`.

Docs: [`config-scanner/AGENT.md`](config-scanner/AGENT.md), [`config-scanner/README.md`](config-scanner/README.md).

### Software Version (roulette)

- Source package layout: `godot\`, `lib\`, `Ruleta.exe`, etc. under `software_versions\<Ruleta_v…>`.
- Apply uses cabinet scripts in [`cabinet_tools/roulette/`](cabinet_tools/roulette/) (`Kill-All`, `Run-FullStack`, `Invoke-SoftwareVersionSwap.ps1`).

### Cabinet / USB roulette tools

See [`cabinet_tools/roulette/README.txt`](cabinet_tools/roulette/README.txt):

- `Kill-All.bat` / `Run-FullStack.bat` — stop/start game + Godot stack
- `Fix-SerialPortLocations.bat` — Leds vs MUX `locations.json` drift
- `Fetch-Roulette102Software*.bat` — pull 10.2 software without admin (see `COPY102-NO-ADMIN.txt`)

### Lab roulette inject (owned cabinets only)

- Dallas splice (keyboard / admin key): [`lab/roulette/README.md`](lab/roulette/README.md)
- WinDivert AFT on roulette WakeUpPort: same folder / `Invoke-WinDivertAftRoulette.ps1`
- Slot AFT docs remain under [`aft/`](aft/)

## Configuration

Edit **`config.py`** for severity rules, first-cause patterns, UNC templates, and live-watch knobs. Config Scanner profiles live in `config_scanner/assets/profiles.json` (`roulette_usb`, `slot_lab_90`).

## Testing

```bash
pip install -r requirements-dev.txt
python -m pytest
python -m pytest tests/test_config_scanner.py
```

If a third-party pytest plugin fails to import:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
python -m pytest
```

## Notes

- **File in use / UNC latency**: parser retries opens and several encodings.
- **First cause**: oldest matching anomaly in the last *N* lines (`FIRST_CAUSE_LOOKBACK_LINES`).
- **Licences**: LI RAM Clear intentionally does **not** run official `ClearWibuKey` / `ClearWibu.exe`.
