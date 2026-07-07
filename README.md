# Goldclub Log Investigator

Modular Python tool to recursively scan Goldclub log directories (local or UNC), detect errors and warnings using configurable regex rules, associate them with an active **game/theme** from `Themes\...` paths, estimate a **first-cause** line in a rolling window, map **severity** (CRITICAL / MEDIUM / LOW), and write **Markdown** or **CSV** reports.

## Layout

| Module | Role |
|--------|------|
| `config.py` | Regex patterns, severity rules, scan defaults, probable-cause hints |
| `scanner.py` | Recursive discovery of `.log` / `.txt` files |
| `parser.py` | Line parsing, theme context, severity + first-cause |
| `logic_validator.py` | Roulette bonus math check (spins / credit wins vs `data/RouletteBonusMath.json`) |
| `aft_transfer.py` | AFT transfer lexicon, log parsers, cross-layer correlator |
| `parser_rules.py` | Known-issue auto-triage (`data/known_issues.json`) + legacy crash signatures |
| `reporter.py` | Markdown / CSV output |
| `log_analyzer.py` | CLI and orchestration (`ThreadPoolExecutor`) |
| `database/` | SQLite persistence (SQLAlchemy): incidents, state nodes, machines |
| `network/` | Fleet discovery: ping sweep + TCP 445 (SMB) probes |
| `gui/` | PySide6 desktop UI (MVVM: ViewModel + virtual table model) |
| `gui_app.py` | Launches the Qt GUI |

## Requirements

- Python 3.10+ (uses `dataclass(slots=True)` and modern typing)
- **GUI + DB:** `pip install -r requirements.txt` (PySide6, SQLAlchemy)

## Usage

From this folder:

```bash
python log_analyzer.py
```

Uses default roots from `config.DEFAULT_SCAN_ROOTS` and writes `goldclub_log_report.md`.

```bash
python log_analyzer.py "C:\Goldclub\var\log" -o report.csv --format csv --workers 12 -v
```

## Configuration

Edit **`config.py`** to:

- Add or reorder **`SEVERITY_RULES`** (first match wins; keep CRITICAL rules first).
- Extend **`FIRST_CAUSE_ANOMALY_PATTERNS`** for better root-cause windows.
- Add **`PROBABLE_CAUSE_RULES`** for new exception or warning substrings.
- Change **`BASE_UNC_PATH`** (template `\\{ip}\c$\Goldclub\var\log`), **`DEFAULT_LOCAL_LOG_ROOT`**, **`DEFAULT_REMOTE_IP`**, or **`DEFAULT_SCAN_ROOTS`** for CLI defaults.
- Tune **`FIRST_CAUSE_LOOKBACK_LINES`** or **`OPEN_RETRIES`** for flaky shares.

Use **`resolve_scan_path(..., remote_mode=...)`** to build local vs administrative-share UNC paths (GUI runs this off the UI thread before scanning).

ISO timestamps such as `2026-03-13T21:11:55.144+00:00` are detected via `ISO_TIMESTAMP_PATTERN`.

## Desktop GUI (PySide6 / MVVM)

```bash
pip install -r requirements.txt
python gui_app.py
```

- **Scan engine** runs on a dedicated **`QThread`** (`gui/scan_worker.py`): directory walk + `parse_log_file_safe` per file, so the UI stays responsive on slow UNC paths (`\\10.0.0.90\c$`, …).
- **Incident table** uses **`QAbstractTableModel`** (`gui/incident_table_model.py`) — no per-row widgets; suitable for very large incident lists.
- **Filtering** debounces typing; above **20,000** incidents, index rebuild runs on **`QThreadPool`** (`gui/filter_runnable.py`).
- **Root Cause inspector** loads numbered log context via **`read_log_context`** in `parser.py` on a thread pool (`gui/stack_loader.py`).
- Pick a root with **Browse…** (same paths as CLI: local or `\\server\...`).
- **Live Watch** (`gui/live_watch_thread.py`): background **QThread** polls the **N** newest `.log`/`.txt` files (see `LIVE_WATCH_*` in `config.py`), reads only **new bytes** since last offset (`live_tail_io.read_new_bytes` with copy-to-temp fallback), and feeds **`parser.feed_live_byte_chunk`** so new rows append without a full rescan. **REC** pulses while live; **Auto-scroll** follows the latest row; **CRITICAL** rows flash red via the table delegate and a **tray balloon** appears if the window is minimized.
- **Case snapshot** (`case_packer.py`): **Case snapshot** button zips the **currently filtered** incidents, **`snapshot_report.md`** + **`snapshot_report.csv`**, **`metadata.json`** (scan path, target IP when remote, dominant game, UTC time, app version, technician notes, cluster map, **`validation_summary`** for roulette math pass/fail), and deduplicated **`logs/*.txt`** excerpts (50 lines before + a few after, via **`read_log_context`**). Packing runs on **`QThreadPool`** (`gui/case_pack_worker.py`) so UNC reads do not freeze the UI.
- **Logic validator** (`logic_validator.py`): During each full-file parse, if **`data/RouletteBonusMath.json`** defines a **`math`** block (`startingSpins`, optional **`creditWins`**), every log segment that enters a **bonus** state (see **`bonus_state_patterns`**) is checked: spin-like lines vs `startingSpins`, and credit-win lines vs `creditWins` (counts and amounts). Mismatches append a **CRITICAL** row with **`CRITICAL MATH DISCREPANCY`** and fill the **Validation** table column. Optional **`logValidation.spinLineSubstrings`** / **`creditWinLineSubstrings`** tune detection. Remove the **`math`** object to disable validation.
- **History / SQLite** (`database/`): A local **`goldclub_investigator.db`** (next to the app) stores **incidents** (full `Incident` fields + dedup hash), **state timeline segments**, and **machines** (IP, name, last scan time). The **History** tab queries the DB without rescanning logs; **Sync scan to database** runs the normal scan then bulk-writes new rows (SQLite `INSERT OR IGNORE` on `machine_id` + dedup hash). All DB I/O uses **`QThreadPool`** runnables (`gui/db_worker.py`). Schema versioning lives in **`database/migrate.py`** — bump **`CURRENT_SCHEMA_VERSION`** and add migration steps when models change.
- **Fleet Overview** (`network/fleet_scanner.py`, `gui/fleet_tab.py`, `gui/fleet_worker.py`): **ICMP** reachability via OS **ping** (subprocess, Windows/Linux) and **TCP 445** checks for SMB. **Scan range** accepts CIDR or `start-end`; concurrency is capped (**~12** workers by default, **≤1024** hosts). Results upsert into **`machines`** (`last_reachable`, `last_smb_open`, `last_net_scan_at`, optional **`asset_id`**). Cards show **green / yellow / red / grey** from reachability plus **24h** incident rollups (CRITICAL → red; math / MEDIUM / LOW → yellow). **Case snapshot** `metadata.json` includes **`machine_registry`** (name + asset id) when the host is in the DB. On startup the app **reloads the grid from SQLite** then **re-pings known IPv4** hosts in the background.

### Automation / other front ends

Import **`run_analysis(roots, output, output_format, workers)`** from `log_analyzer.py` for batch jobs, or drive **`IncidentViewModel`** + **`run_app()`** from `gui/main_window.py` for embedded use.

## Testing (pytest)

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Logic validator coverage lives in **`tests/test_logic_validator.py`** with fixture log **`tests/fixtures/test_roulette_sessions.log`**.

If a globally installed pytest plugin fails to import (for example `anchorpy` requiring `pytest_asyncio`), disable third-party plugin autoload:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
python -m pytest
```

## Notes

- **File in use / UNC latency**: the parser retries opens and uses several encodings (`FILE_ENCODINGS`).
- **First cause**: the oldest line in the last *N* lines (`FIRST_CAUSE_LOOKBACK_LINES`) that matches an anomaly pattern; tune if stacks are deeper than *N*.

## AFT promo-credit injection (lab)

Adjunct PowerShell tooling for owned lab cabinets, separate from the Python analyzer above. WinDivert injects a SAS `0x72` into the live `CommCtrlSAS → Aurum` loopback stream (replacing only the AFT frame; a SAS host must still be polling on COM11 for credit to commit).

**All documentation, evidence, and audit output:** [`aft/README.md`](aft/README.md) — runbook, hop map, `.171` investigations, captures.

**Quick start:** `.\Send-TestAft1000.ps1 -Send -IP 10.0.0.90` (requires `-Send`; see `aft/RUNBOOK.md`).
