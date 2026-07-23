# Proven AFT inject procedure - no physical IGT polls (.90 / GST20664)

Saved lab workflow for landing **$1,000 promo AFT** via WinDivert poll simulation +
in-stream `0x72` inject. **No IGT tester, no COM11 serial polls.**

| Field | Value |
|-------|--------|
| Cabinet | `10.0.0.90` (GST20664) |
| EGM | `GCC_ST_20664_01`, asset `777` |
| Method | `WdPollInject.exe pollaft` via `Invoke-WinDivertAft.ps1 -SasPollMode WinDivert` |
| Last full success | **2026-07-10** txn 84 - ingest + credit, no physical polls |
| Prior success | **2026-07-09** txn 83 - same path |

Layer model: [`no-physical-polls-layers.md`](no-physical-polls-layers.md).
Command reference: [`RUNBOOK.md`](RUNBOOK.md).
Flow diagrams: [diagrams/windivert-pollaft-inject-flow.md](diagrams/windivert-pollaft-inject-flow.md)


## One-shot (when bridge is healthy)

```powershell
.\Initialize-LabAccess.ps1 -Verify
.\lab\Invoke-WinDivertAft.ps1 -Send -IP 10.0.0.90 -SasPollMode WinDivert
```

## Full procedure (wake + inject + verify)

### Step 1 - Preflight

Check TCP 31150 Established; if pollaft shows s2c=0 c2s=0 the bridge is silent.

### Step 2 - Wake bridge

```powershell
.\Invoke-WakeSasBridge.ps1 -IP 10.0.0.90 -ClearPendingAft -WaitForWat
```

### Step 3 - Inject

```powershell
.\lab\Invoke-WinDivertAft.ps1 -Send -IP 10.0.0.90 -SasPollMode WinDivert -MaxRetries 2
```

### Step 4 - Success criteria

- INGESTED: sasmsgr qGMID1:0172 full hex match
- Post-AFT polls: 80/81 after 0172 (~3 s)
- CREDITED: GM2AU Withdraw 100000; SlotLog promo 100000

## Failure modes

| Symptom | Fix |
|---------|-----|
| s2c=0 c2s=0 | Invoke-WakeSasBridge.ps1 |
| Exception 69 | -ClearPendingAft |
| Ingest only | Wait WAT2AFT UP; post-AFT polls (PostAftPollMs=3000) |

## Proven run 2026-07-10 txn 84 (slot path)

See test-runs/20260710-073042-after/inject-summary.md

---

## Roulette path (.90 / GCC_RT_330106_01) — proven 2026-07-23

Roulette does **not** use CommCtrlSAS `:31150`. Use the WakeUpPort / MUX channel
(usually **30550**) via the roulette wrapper. Full notes:
[lab/roulette/README.md](../lab/roulette/README.md).

| Field | Value |
|-------|--------|
| Cabinet | `10.0.0.90` (GST20664) |
| EGM | `GCC_RT_330106_01`, asset `777`, SASAddress `1` |
| Bridge | CommCtrlSAS **WakeUpPort 30550** → Aurum (ClientsSet Port=30500) |
| Method | `lab\roulette\Invoke-WinDivertAftRoulette.ps1 -Send` (`pollaft` + AutoWake) |
| Last full success | **2026-07-23** txn 37 — ingest + NonRestricted 100000 credited |

### One-shot (roulette)

```powershell
.\Initialize-LabAccess.ps1 -Verify
.\lab\roulette\Invoke-WinDivertAftRoulette.ps1 -Send -IP 10.0.0.90 -nr 100000
```

### Success criteria (2026-07-23)

- BridgePort auto-selected: `30550` (ESTABLISHED)
- Exception **66** (cashout) ignored — not a host→EGM busy
- AutoWake (WinRM) restarted Gateway SAS + Aurum when WAT was stale; WAT2AFT UP
- sasmsgr: `qGMID1:0172...` INGESTED (addr 1, non-restricted amount 100000)
- Aurum: `FULL_TRANSFER_SUCCESSFUL` — NonRestricted Req/Com **100000**

Do **not** point slot `Invoke-WinDivertAft.ps1` at `:31150` on this roulette image
(`NO_ESTABLISHED`). If only `:40000` is listening, restore COM5/MUX USB and cold-boot.