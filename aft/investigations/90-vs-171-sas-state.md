# SAS state comparison: 10.0.0.90 (reference) vs 10.0.0.171 (target)

**Captured:** 2026-07-10, BEFORE user clicks Apply on "SAS channel functionality" dialog on `.171`.

Reference cabinet: **10.0.0.90** (`GST20664`, `GCC_ST_20664_01`) — known-good poll + wake path.  
Subject cabinet: **10.0.0.171** (`GST19737`, `GCC_ST_19737_01`) — same disk image family; SAS channel controllers not yet enabled in UI.

---

## HEADLINE (before Apply)

| Signal | .90 | .171 |
|--------|-----|------|
| Key SAS services RUNNING | YES (all 5) | YES (all 5) |
| sasmsgr log folder | **YES** | **NO** (folder missing entirely) |
| qGMID1:80/81 polls today | **YES** (continuous) | **NO** |
| CommCtrlSAS 31100/31150 bridge | **YES** (Established) | **NO** (port 40000 listen only) |
| SlotLog communications | game idle / offline msgs | **machine locked (communications offline)** |
| SASsetupData.xml | identical SHA | identical SHA |
| CommControler.ini (COM11 921600) | identical SHA | identical SHA |
| AurumSetup ConfigurationId | 51 | 49 |
| WatAccounts on SASControler1 | YES | NO |
| Channel device OwnerHostId | mostly **1** (configured) | mostly **0** (unassigned) |
| LastConfigurationChange on channels | **2** on .90 | **0** on .171 |

**Services are up on both cabinets.** The gap is runtime SAS channel activation and poll bridge — not missing Windows services.

---

## Services (sc.exe remote — no PsExec)

Both cabinets identical:

- GoldClub.Aurum.Services — RUNNING
- GoldClub Serial Communication Gateway — RUNNING
- GoldClub Serial Communication Gateway SAS — RUNNING
- GoldClub Hardware Subsystem — RUNNING
- GoldClub.Logging.LogDaemon — RUNNING

---

## Config file hashes

| Path | .90 | .171 | Same? |
|------|-----|------|-------|
| SASsetupData.xml | FE840740… | FE840740… | **YES** |
| CommControler.ini (SAS) | 28EF12FC… | 28EF12FC… | **YES** |
| CommControler.ini (CommCtrl) | 54897D4E… | 54897D4E… | **YES** |
| taskhost CommCtrlSAS.xml | 6206FEE9… | 6206FEE9… | **YES** |
| AurumSetup.xml | EFDD68C2… (23398 B) | 6F0E3EA2… (22027 B) | **NO** (identity + channel state) |
| mgconfig.xml | 015B6F48… | AD36B60E… | **NO** (game content) |
| ClientsSet.xml | 63BBEF25… | 7D2B7625… | **NO** (EGM id only) |

---

## AurumSetup SAS channel devices (BEFORE)

These map to the UI checkboxes (validation, cashless, handpay, bonusing, note acceptor):

| DeviceClass | .90 OwnerHostId | .90 LCC | .171 OwnerHostId | .171 LCC |
|-------------|-----------------|---------|------------------|----------|
| noteAcceptor | 1 | 2 | 0 | 0 |
| handpay | 1 | 2 | 0 | 0 |
| bonus (0) | 1 | 2 | 0 | 0 |
| bonus (1) | 1 | 2 | 0 | 0 |
| voucher | 1 | 2 | 0 | 0 |
| WAT | 1 | 2 | 0 | 0 |
| communications | 1 | 0 | 1 | 0 |

All show `Enabled=false` in XML on both (runtime flag; host assignment differs).  
`.171` lacks `WatAccounts` block under SASControler1; `.90` has full WAT account list.

---

## Log evidence (today 2026-07-10)

**.90**

- sasmsgr: `qGMID1:80` / `qGMID1:81` every ~200 ms
- CommCtrlSAS: `Connection … established on port 31100` and `31150`
- WinRM: CommCtrlSAS process running; TCP 31150 Established

**.171**

- **No** `GoldClub.Aurum.Services sasmsgr of SASControler1` log directory
- SASControler1 log: device registration at 12:26 (`optionConfig`, `communications`, `bonus`)
- CommCtrlSAS: only `Listening on port 40000` (2 lines all day)
- SlotLog: communications offline / machine locked earlier

---

## COM4 PermissionError vs SMB admin share

These are **different machines and different resources**:

| | SMB `\\10.0.0.171\c$` | COM4 on HOST PC |
|--|----------------------|-----------------|
| What | Cabinet disk over network | Local serial port on workstation |
| Used for | Read logs/config remotely | AFT inject / IGT SASTest / physical SAS cable |
| `com_port_blocked` | **Not involved** | **This is the error** |
| Fix | cmdkey + GOLD-CLUB\test (working) | Close app holding COM4, pick correct port, replug USB-serial |

Admin share access proves you can read the cabinet. It does **not** grant access to a COM port on your PC.

---

## AFTER user clicks Apply — what to check

Run:

```powershell
.\Capture-CabinetSasState.ps1 -IP 10.0.0.171 -Label after
.\Capture-CabinetSasState.ps1 -Compare `
  aft\investigations\171-sas-state-baseline.json `
  aft\investigations\171-sas-state-after-<stamp>.json
```

Expect changes if enable succeeded:

1. **AurumSetup.xml** SHA changes; `OwnerHostId=1` and `LastConfigurationChange` bump on channel device classes
2. **New sasmsgr log folder** under `var\log\GoldClub.Aurum.Services sasmsgr of SASControler1\`
3. **qGMID1:80/81** lines appearing in sasmsgr (may take wake / external poller)
4. **CommCtrlSAS log** shows 31100/31150 connections (not just 40000)
5. **SlotLog** stops "communications offline" / machine locked messages
6. Optional: `WatAccounts` section appears in AurumSetup SASControler1 block
7. **ConfigurationId** may increment (49 -> 50+)

If services restart: re-check sc.exe states; no manual reboot expected from Apply alone.

**HOST inject:** even after cabinet SAS channels enable, fix COM4 separately on the workstation before retrying AFT inject.

---

## Files

- `aft/investigations/171-sas-state-baseline.json` — `.171` BEFORE snapshot
- `aft/investigations/90-sas-state-reference.json` — `.90` reference snapshot
- `Capture-CabinetSasState.ps1` — reusable capture + compare

**Ready for user to click Apply on `.171`.**
