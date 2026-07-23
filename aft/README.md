# AFT promo-credit lab docs

Lab-only tooling for owned GoldClub cabinets. Goal: land a **$1,000 promo AFT
(cashless) credit** by injecting SAS `0x72` into the live CommCtrlSAS -> Aurum
loopback stream. With the full-sim path you do not need the IGT tester UI.

This folder holds documentation and evidence. Inject scripts live under `lab/`
and `probes/`.

## Status (short)

| Cabinet | Result |
|---------|--------|
| **10.0.0.90** (GST20664) slot path | **Works** — WinDivert poll-sim + inject on `:31150` (txn 83/84, Jul 2026) |
| **10.0.0.90** roulette path | **Works** — same pollaft method on WakeUpPort **`:30550`** (txn 37, **2026-07-23**, NonRestricted 100000) |
| **10.0.0.171** (GST19737) | **Blocked** without an organic SAS poll source - see investigations |

Default **slot** path: simulate polls with `WdPollInject` (`-SasPollMode WinDivert`),
inject `0x72` in-stream, keep post-AFT polls for a few seconds. No physical COM11
IGT session required on `.90` when the CommCtrlSAS <-> Aurum bridge is awake.

**Roulette** on the same cabinet uses ClientsSet WakeUpPort (usually 30550), not
31150 — use `lab\roulette\Invoke-WinDivertAftRoulette.ps1` (see
[lab/roulette/README.md](../lab/roulette/README.md)).

## Quick start (.90)

From the repo root:

```powershell
.\Initialize-LabAccess.ps1 -Verify
.\lab\Send-TestAft1000.ps1 -Send
# or:
.\lab\Invoke-WinDivertAft.ps1 -Send -IP 10.0.0.90
```

If the bridge is silent (`s2c=0 c2s=0`):

```powershell
.\lab\Invoke-WakeSasBridge.ps1 -IP 10.0.0.90 -ClearPendingAft -WaitForWat
```

Full checklist: [PROVEN-INJECT-PROCEDURE.md](PROVEN-INJECT-PROCEDURE.md)
Ops detail: [RUNBOOK.md](RUNBOOK.md)

## How the path works

```
SAS polls (organic IGT/MUX  --OR--  WdPollInject 1B81/1B80 @ 200ms)
        |
        v
CommCtrlSAS :31150  ----TCP (0x1B framed)---->  Aurum WAT2AFT
        ^                                              |
        |         WinDivert in-stream 0x72 inject      v
        +----------------------------------------  AFT XML -> OneHand -> SlotLog
```

- Inject into the **existing** TCP session (correct SEQ). A new socket to 31150 does not work.
- Aurum must reach `WAT2AFT UP`. Clear stale `aftPendingTransaction_*.xml` if exception 69.
- Success = sasmsgr shows `0172` **and** GM2AU / SlotLog show the credit (e.g. 100000 cents).

Per-hop map: [hops/README.md](hops/README.md)

## Doc map

| Path | What it is |
|------|------------|
| [RUNBOOK.md](RUNBOOK.md) | Day-to-day inject, verify, troubleshoot |
| [PROVEN-INJECT-PROCEDURE.md](PROVEN-INJECT-PROCEDURE.md) | Saved .90 procedure that worked |
| [hops/](hops/) | Hop-by-hop chain (tester/bridge/WAT/OneHand/verify) |
| [diagrams/](diagrams/) | Flow diagrams for pollaft inject |
| [investigations/](investigations/) | .90 vs .171, poll source, config parity, dead ends |
| [report/](report/) | Read-only AFT/WAT audit output (generated) |
| [test-runs/](test-runs/) | Historical before/after inject snapshots |
| [protocol-raw-traffic.md](protocol-raw-traffic.md) | Raw SAS framing notes |

## .171 blocker (why credit fails there)

`.171` can ingest an injected `0x72` and still not credit if there is **no steady
upstream SAS poll stream**. Stack restart alone does not create polls. Details:

- [investigations/poll-source.md](investigations/poll-source.md)
- [investigations/90-vs-171-sas-state.md](investigations/90-vs-171-sas-state.md)
- [investigations/alt-credit-paths.md](investigations/alt-credit-paths.md) - no non-SAS bypass found

Do not treat `.171` inject failures as a WinDivert bug until polls are live.

## Tooling (repo)

| Item | Role |
|------|------|
| `lab/Send-TestAft1000.ps1` | Thin wrapper - default $1k promo inject (slot `:31150`) |
| `lab/Invoke-WinDivertAft.ps1` | Main inject driver (`-SasPollMode WinDivert`) |
| `lab/roulette/Invoke-WinDivertAftRoulette.ps1` | Roulette wrapper — WakeUpPort `:30550`, proven 2026-07-23 |
| `lab/Invoke-WakeSasBridge.ps1` | Wake CommCtrlSAS + Aurum / clear pending AFT |
| `probes/WdPollInject.*` | Poll simulation + `pollaft` inject |
| `probes/WdInject.*` | Legacy one-shot inject when polls already exist |
| `lab/Convert-AftHistory.ps1` | Read-only history -> `aft/report/` |
| `lab/Invoke-AftTransferTest.ps1` | Wait/verify helper (does not send `0x72`) |

Roulette procedure + 2026-07-23 evidence:
[lab/roulette/README.md](../lab/roulette/README.md) ·
[PROVEN-INJECT-PROCEDURE.md](PROVEN-INJECT-PROCEDURE.md#roulette-path-90--gcc_rt_330106_01--proven-2026-07-23)

## Lab access

```powershell
.\Initialize-LabAccess.ps1 -Verify
```

Uses lab account `GOLD-CLUB\test` (see `.cursor/rules/lab-cabinet-access.mdc`).
