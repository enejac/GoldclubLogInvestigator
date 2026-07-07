# GoldClub Cabinet — Factory Reset Flow & File Operations

Reference cabinet: `\\10.0.0.90\c$\Goldclub` (local `C:\goldclub`).

This document describes exactly what happens when a factory reset is triggered,
which scripts run in which order, and **which files are copied / restored / deleted**.

---

## 1. TL;DR

- `begin-FactoryReset.cmd` **does not copy anything itself.** It deletes two
  state markers, disables the write filter, and reboots.
- The actual reset work runs **after reboot**, during the on-logon maintenance
  sequence, gated by a missing `ProductSerialNumber.conf` marker.
- The reset **wipes** logs, licences and synced config, then **restores**
  configuration from the stored factory-default snapshot under
  `var\state\maintenance\factory-defaults\`.
- The file copy itself is done by `UpdateFactoryDefaults.1.ps1 -restore`
  (uses `robocopy /MIR` for folders, `Copy-Item` for single files).

---

## 2. Trigger

Two entry-point command files exist in `C:\goldclub\maintenance\`:

| File | Final action |
|------|--------------|
| `begin-FactoryReset.cmd` | `shutdown /r` (reboot) |
| `begin-FactoryResetShutdown.cmd` | `shutdown /s` (power off) |

Both are identical except for the final shutdown mode:

```bat
@echo off
DEL /F /Q \goldclub\var\state\maintenance\first-time-setup.conf
DEL /F /Q \goldclub\var\state\maintenance\ProductSerialNumber.conf
Powershell ... -File "c:\goldclub\maintenance\tasks\setupfilter.ps1"
shutdown /r /t 5 /c "Maintenance in progress (begin-factory-reset)..."
exit
```

What it does:

1. **Deletes the two markers** that gate the post-reboot tasks:
   - `var\state\maintenance\first-time-setup.conf`
   - `var\state\maintenance\ProductSerialNumber.conf`
2. Runs `maintenance\tasks\setupfilter.ps1`, which loads the
   `goldclub.filter.1` module and calls **`Disable-UWF`** — this schedules the
   Unified Write Filter to be **off after the next restart**, so disk writes
   made during the maintenance boot actually persist.
3. Reboots (or shuts down).

> Deleting `ProductSerialNumber.conf` is what arms the factory reset:
> the on-logon gate `05-01-CheckForFactoryReset.ps1` runs the reset **only when
> that marker is absent**.

---

## 3. Write-Filter (UWF) / reboot cycle

The cabinet normally runs write-protected (UWF enabled, disk writes volatile).
Maintenance needs writes to persist, so the cycle is:

1. **Protected session** → `begin-FactoryReset.cmd` calls `Disable-UWF`, reboots.
2. **Maintenance session** (UWF now disabled, writes persist) → on-logon runs the
   factory-reset tasks. Early in on-logon, `00-02-CheckUWF.ps1` detects the
   filter is disabled, calls **`Enable-UWF`** (re-arm for next boot) and triggers
   a final reboot.
3. **Protected session again** (UWF enabled) → marker now present, reset does not
   re-run; normal startup proceeds.

`00-02-CheckUWF.ps1`:

```powershell
$FilterStatus = Show-UWFState
if ($FilterStatus -eq "disabled") {
    Enable-UWF
    shutdown /r /t 1 /c "Maintenance in progress (close disk)..."
}
```

---

## 4. On-logon orchestration

Boot/logon runs `platform\user\init\onlogon.ps1`, which shows the
“Initializing Machine…” screen and executes every `*.ps1` in
`platform\user\init\onlogon\` **in sorted filename order** via
`bin\RunManteinanceTasks.1.ps1`. Relevant steps:

| Order | Script | Role |
|-------|--------|------|
| `00-02` | `CheckUWF.ps1` | Re-enable UWF + reboot if currently disabled |
| `04` | `UpdateFactoryDefaults.ps1` | **Captures** factory defaults (store, if not already stored) |
| `05-01` | `CheckForFactoryReset.ps1` | **Runs factory reset** if `ProductSerialNumber.conf` missing |
| `05-04` | `CheckForRamClear.ps1` | RAM-clear path (marker-gated) |
| `05-05` | `CheckForFirstTimeSetup.ps1` | First-time setup path (marker-gated) |
| `90` | `StartServices.ps1` | Start GoldClub services |

`RunManteinanceTasks.1.ps1` simply enumerates `*.ps1` in a folder (sorted),
dot-sources each in turn, and logs `[START]`/`[END]` per task:

```powershell
foreach ( $item in ([System.IO.Directory]::GetFiles($path, "*.ps1") | Sort)) {
    try { & { . $item } } catch { Write-Warning "Task stopped due error: $PSItem" }
}
```

### 4a. Factory-defaults capture (step 04)

`04-UpdateFactoryDefaults.ps1` → `UpdateFactoryDefaults.1` (no `-restore`) runs
in **Update** mode: for each config item it stores a snapshot **only if one is
not already stored** (`if (Test-Path $targetDir) { continue }`). So the default
snapshot is created once and reused; it is the source for the restore in step 5.

### 4b. Factory-reset gate (step 05-01)

```powershell
$marker = "C:\goldclub\var\state\maintenance\ProductSerialNumber.conf"
if ( Test-Path -Path $marker -PathType Leaf ) { return }
C:\goldclub\bin\RunManteinanceTasks.1 -path C:\goldclub\maintenance\tasks\factory-reset\ -nested
```

---

## 5. Factory-reset task sequence

Tasks in `maintenance\tasks\factory-reset\` run in sorted order:

| Order | Script | Action |
|-------|--------|--------|
| `00` | `00-InvokeTask-RamClear.ps1` | Runs the full **RAM-clear** task chain (backup + cleanup) — see §7 |
| `11` | `11-RemoveLogs.ps1` | Deletes `C:\goldclub\var\log` recursively |
| `11` | `11-RemoveSlotLicence.ps1` | Clears `licenses`, `Licences`, `slot\licenses` contents and deletes `slot\licence.dll` |
| `55` | `55-RestoreDefaults.ps1` | **Restores config from factory-default snapshot** — see §6 |
| `55` | `55-SlotRemoveConfigurationSync.ps1` | Deletes `var\state\SlotConfigurationSync` |
| `60` | `60_ProductSerialNumber.ps1` | Renames `var\state\maintenance\HostName.conf` → `ProductSerialNumber.conf` (re-creates the gate marker) |

> Note: two tasks share the `11-` and `55-` prefixes; PowerShell `Sort` orders
> them alphabetically by full name (`RemoveLogs` before `RemoveSlotLicence`,
> `RestoreDefaults` before `SlotRemoveConfigurationSync`).

`60_ProductSerialNumber.ps1` restoring the marker is what stops the reset from
looping on the next boot.

---

## 6. The COPY / RESTORE operation (the heart of the reset)

`55-RestoreDefaults.ps1` is a one-liner:

```powershell
UpdateFactoryDefaults.1 -restore
```

`bin\UpdateFactoryDefaults.1.ps1` reads `maintenance\config\factory-defaults.conf`:

```ini
DROP=C:\goldclub\var\state\maintenance\factory-defaults\
CONFIG_DIR=C:\goldclub\maintenance\config\factory-defaults\
```

For every `*.conf` in `CONFIG_DIR`, it restores the matching subtree from
`DROP\<name>\` back into `C:\goldclub\...`:

- **Folders** → `robocopy /MIR <stored> <live>` (mirror: live folder is made
  identical to the stored snapshot, extra files removed).
- **Single files** → `Copy-Item -Force <stored> <live>`.

### Restore map

| Config (`config\factory-defaults\*.conf`) | Type | Stored source (`var\state\maintenance\factory-defaults\…`) | Live destination (`C:\goldclub\…`) |
|---|---|---|---|
| `aurum.conf` → `FOLDERS=services\aurum\config\` | folder (MIR) | `aurum\services\aurum\config\` | `services\aurum\config\` |
| `configuration.conf` → `FOLDERS=bios/etc/` | folder (MIR) | `configuration\bios\etc\` | `bios\etc\` |
| `slot.conf` → `FOLDERS=Slot\Licenses\` | folder (MIR) | `slot\Slot\Licenses\` | `Slot\Licenses\` |
| `slot.conf` → `FOLDERS=Licenses\` | folder (MIR) | `slot\Licenses\` | `Licenses\` |
| `slot.conf` → `FILES=Slot\Themes\mgconfig.xml` | file (copy) | `slot\Slot\Themes\mgconfig.xml` | `Slot\Themes\mgconfig.xml` |
| `slot.conf` → `FILES=Slot\Themes\HardwareConfig.xml` | file (copy) | `slot\Slot\Themes\HardwareConfig.xml` | `Slot\Themes\HardwareConfig.xml` |

> `HardwareConfig.xml` is the file that holds the Dallas key permissions, so a
> factory reset reverts the Dallas keys to whatever is in the stored default
> snapshot (currently the snapshot’s default codes, not the live `01D68A721B000019`).

### What is actually present in the snapshot today

`slot\` snapshot:

```
slot\Licenses\GCLicence.xsd
slot\Slot\Themes\HardwareConfig.xml
slot\Slot\Themes\mgconfig.xml
```

(`slot\Slot\Licenses\` has no stored content, so that mirror restores an empty folder.)

`aurum\` snapshot:

```
aurum\services\aurum\config\SASControler1\crcfileslist.txt
```

`configuration\bios\etc\` snapshot (66 files total), including e.g.:

```
configuration\bios\etc\logger.xml
configuration\bios\etc\logger_settings.xml
configuration\bios\etc\skin.xml
configuration\bios\etc\application\CommCtrl\CommControler.ini
configuration\bios\etc\application\CommCtrlSAS\CommControler.ini
configuration\bios\etc\application\aurum\*\options.xml   (many)
configuration\bios\etc\application\modules\hw\endpoints\tcp\connection{0,1,2}.xml
configuration\bios\etc\application\modules\hw\Drivers\ChainLink\link{0,1,2}.xml
configuration\bios\etc\application\system\network.xml
configuration\bios\etc\application\licensing\xmlLicenceStorageSettings.xml
configuration\bios\etc\xml-configs\application\Jackpot*/configuration-cfg.xml
... (full bios\etc config tree)
```

---

## 7. RAM-clear sub-chain (invoked first, step 00)

`00-InvokeTask-RamClear.ps1` → `maintenance\tasks\ramclear.ps1` →
`RunManteinanceTasks.1 ... \ramclear\`. Tasks:

| Order | Script | Action |
|-------|--------|--------|
| `01` | `01-StopServices.ps1` | Stops all services whose name starts with `goldclub` |
| `10` | `10-Backup.ps1` | **Backs up** live data to a timestamped `.7z` archive |
| `50` | `50-Cleanup.ps1` | Deletes the contents of configured folders |
| `51` | `51-LogDaemon.ps1` | Runs `LogDaemonRamClear.exe` |

### 7a. RAM-clear backup copy (`10-Backup.ps1`)

Reads `maintenance\config\ramclear.conf`:

```ini
[Backup]
DROP=C:\goldclub\var\state\maintenance\ramclear\backup\
CONFIG_DIR=C:\goldclub\maintenance\config\ramclear\backup\
MAX_BACKUPS = 10
```

It reuses `UpdateFactoryDefaults.1 -drop <tmp>` to gather the configured folders
into a temp dir, then `7za.exe` compresses them into
`var\state\maintenance\ramclear\backup\<yyyy-MM-dd_HH-mm-ss>.7z`
(keeping the newest 10).

Folders collected for backup (`config\ramclear\backup\*.conf`):

| Conf | `FOLDERS` |
|------|-----------|
| `slot.conf` | `slot\var` |
| `config.conf` | `bios\etc\` |
| `aurum.conf` | `var\state\goldclub.aurum.services` |

### 7b. RAM-clear cleanup (`50-Cleanup.ps1`)

For each folder in `config\ramclear\cleanup\*.conf`, removes the **contents**
(files + subfolders) of `/goldclub/<folder>`.

---

## 8. Net effect of a factory reset

**Backed up (compressed):** `slot\var`, `bios\etc`, `var\state\goldclub.aurum.services`
→ `var\state\maintenance\ramclear\backup\*.7z`.

**Deleted / wiped:**
- `var\log` (all logs)
- `licenses`, `Licences`, `slot\licenses` contents + `slot\licence.dll`
- `var\state\SlotConfigurationSync`
- contents of the ramclear cleanup folders

**Restored from factory-default snapshot (overwrites live):**
- `services\aurum\config\` (mirror)
- `bios\etc\` (mirror)
- `Licenses\` and `Slot\Licenses\` (mirror)
- `Slot\Themes\mgconfig.xml` and `Slot\Themes\HardwareConfig.xml` (file copy)

**Re-armed marker:** `var\state\maintenance\ProductSerialNumber.conf`
(from `HostName.conf`), preventing a reset loop.

---

## 9. Key file reference

| Purpose | Path |
|---------|------|
| Reset entry (reboot) | `maintenance\begin-FactoryReset.cmd` |
| Reset entry (shutdown) | `maintenance\begin-FactoryResetShutdown.cmd` |
| Disable write filter | `maintenance\tasks\setupfilter.ps1` |
| UWF helpers | `bin\lib\powershell\goldclub.filter.1\goldclub.filter.1.psm1` |
| Task runner | `bin\RunManteinanceTasks.1.ps1` |
| On-logon driver | `platform\user\init\onlogon.ps1` |
| Reset gate | `platform\user\init\onlogon\05-01-CheckForFactoryReset.ps1` |
| Defaults capture | `platform\user\init\onlogon\04-UpdateFactoryDefaults.ps1` |
| Reset tasks | `maintenance\tasks\factory-reset\*.ps1` |
| Copy/restore engine | `bin\UpdateFactoryDefaults.1.ps1` |
| Restore config | `maintenance\config\factory-defaults.conf` + `config\factory-defaults\*.conf` |
| Default snapshot store | `var\state\maintenance\factory-defaults\` |
| RAM-clear config | `maintenance\config\ramclear.conf` + `config\ramclear\**` |
| Backup archive store | `var\state\maintenance\ramclear\backup\` |
