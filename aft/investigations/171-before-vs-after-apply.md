# 171 (GST19737) before vs after SAS channel Apply

Generated (UTC): 2026-07-10T12:55:00Z  
Baseline: `171-sas-state-baseline.json` (pre-Apply)  
After: `171-egm-after-state.json` (captured 2026-07-10T12:53:18Z)

## Apply readiness score

| Phase | Score | State |
|-------|-------|-------|
| Pre-Apply (baseline) | **0/11** | channels unassigned (OwnerHostId=0, LCC=0); no WatAccounts |
| Post-Apply (after) | **6/11** | **partial-Apply** — channels assigned, runtime not live |

Scoring: OwnerHostId=1 (≥5) +2, LCC=2 (≥4) +2, WatAccounts +2, sasmsgr dir +1, qGMID polls +2, 31100/31150 bridge +2.

## What Apply changed

### 1. AurumSetup.xml
- **SHA256 changed** (22027 → 23398 bytes; last write 2026-07-10 ~12:56 local on cabinet).
- **SASControler1 ConfigurationId**: 49 → **51** (matches .90 reference).
- **WatAccounts**: absent/false → **present (True)**.
- **Channel devices** (noteAcceptor, handpay, voucher, WAT, both bonus): OwnerHostId **0→1**, LastConfigurationChange **0→2**, RestartStatus **HOST_ENABLED** (parsed from AurumSetup).
- **communications** id=1: OwnerHostId stayed **1**; LastConfigurationChange still **0** (same as .90 reference pattern).

### 2. sasmsgr / polls
- **sasmsgr log folder**: still **missing** (no `GoldClub.Aurum.Services sasmsgr of SASControler1` under var\log).
- **qGMID1:80/81 today**: **none** (no host-side SAS polls reaching sasmsgr).

### 3. CommCtrlSAS
- Still **Listening on port 40000** only (latest 13:57:15).
- **No** “Connection … established on port **31100** / **31150**” (reference .90 shows both bridge ports up).

### 4. Services
- All five key SAS stack services remain **RUNNING** (unchanged).

### 5. SlotLog comm state
- **Before**: repeated “machine locked” / communications offline through ~12:25.
- **After**: tail shows normal hardware init (e.g. ticket printer); **no new** communications-offline lines after Apply window in today’s log (last offline ~12:25, capture tail ~13:58).

## Unchanged vs baseline (runtime gaps)

- SasmsgrLogDirExists: False  
- HasQGMIDPollsToday: False  
- CommCtrlSasBridge311: False  

## Inject readiness

**Not transfer-ready like .90.** Config-side Apply succeeded for SAS channels + WatAccounts; **physical/host SAS path still dead** (no sasmsgr, no qGMID polls, no 311 bridge).

**MUX/COM4 on host still required** for WinDivert poll inject until sasmsgr folder and qGMID1:80/81 appear without inject.

**Ready for inject (lab workaround): Y** — use host COM4/MUX inject path; cabinet is **not** ready for native host polls alone (**N** for inject-free transfer parity with .90).
