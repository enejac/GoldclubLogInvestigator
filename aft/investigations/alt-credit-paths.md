# Credit-path analysis — cabinet `10.0.0.171` (read-only)

Lab QA mapping of how promo/cashable credit reaches the game meter on the
reference cabinet, and which **documented** paths could apply on `.171` when the
SAS serial session is offline. **No credit was posted, no remote configs were
edited, no services were restarted.** Grounded in repo hop docs and prior `.90`
evidence.

**Context:** AFT `0x72` over the CommCtrlSAS→Aurum bridge **ingests** on `.171`
but does not **commit** (`CREDITED:NO`, WAT2AFT `NO OWNED DEVICE`). Root cause is
missing COM11 upstream poll stream ([COM11 mux diagnosis](5ed4f26a-b560-4739-9ae5-bf22f67dc5a8)).

---

## 1. Where credit actually posts (working `.90` chain)

| Hop | Component | What happens |
|-----|-----------|--------------|
| 2 | CommCtrlSAS → Aurum (`31150`) | SAS bytes (polls + `0x72`) reach sasmsgr |
| 3 | WAT2AFT (`GoldClub.Aurum.Services`) | `requestTransfer` → `authorizeTransfer` → `commitTransfer`; AFT state XML written |
| 4 | OneHand via GM2AU (`GM2AU_aurumExecute`) | Applies committed transfer; **meter bump** + SlotLog |
| 5 | Verification | `Cashless In: $X` + `Aurum promo credit state increased to <cents>` |

**Last hop that increments the player/promo meter:** OneHand (`OneHand.AurumEGM`),
driven by an **already-committed** WAT2AFT transfer from Hop 3. SlotLog
`Cashless In` is an output sink only — forging that line does not move credit
(`hop4-onehand-to-slotlog.md` §5).

Endpoints observed on working cabinets:
- HOST / WAT: `http://<GST>:50011/SASControler1` (.NET remoting)
- EGM / GM2AU: `http://<GST>:50010/GM2AU`

---

## 2. Candidate paths on `.171` (ranked)

### Rank 1 — Restore COM11 SAS host (physical or serial emulator)

| | |
|---|---|
| **Viability** | **Only path proven to complete a real commit** on lab cabinets |
| **Risk** | Physical check = low; serial emulator = invasive (stop CommCtrlSAS, own COM11) |
| **Effort** | Physical: cable/power/MUX upstream; software: `SasSerialEmulator.cs` scaffold exists |
| **Why** | WAT2AFT ownership and `commitTransfer` require sustained serial-side SAS
liveness. Loopback poll inject disproven. |

**Safe test plan (after upstream polls restored):** existing
`Invoke-WinDivertAft.ps1 -Send -IP 10.0.0.171`; oracle =
`Invoke-AftTransferTest.ps1` / SlotLog `Cashless In` + AFT XML `transferException=0`.

---

### Rank 2 — WinDivert AFT `0x72` on loopback (current transport win)

| | |
|---|---|
| **Viability** | **Proven ingest** on `.171`; **commit blocked** while WAT offline |
| **Risk** | Low (same class as `.90` success) |
| **Effort** | Done — needs Rank 1 prerequisite |
| **Status** | Do not burn transaction IDs until WAT owns device |

---

### Rank 3 — Direct WAT `requestTransfer` via `:50011` remoting

| | |
|---|---|
| **Viability** | **Dead-end for new transfer** (documented NRE) |
| **Evidence** | `hop3-aurum-wat2aft-to-aft-xml.md` §4: remoting reaches proxy, but
`RequestTransferPosted` throws `NullReferenceException` when minting a new id |
| **Re-eval with offline WAT** | NRE is in WAT manager expecting existing Aurum
transaction context — same class of failure as missing owned device; unlikely to
work while offline |

**Safe test plan (if re-tried under GO):** read-only probe only via existing
`Invoke-AftTransferTest.ps1` patterns; expect NRE until WAT online.

---

### Rank 4 — Legacy `setBonusAward` / jackpot (GM2AU bonus path)

| | |
|---|---|
| **Viability** | **Dead-end** — not the AFT/WAT promo path |
| **Evidence** | Host-rejected: `TRANSACTIONID SEQUENCE NOT ALLOWED TO USE ON HOST SIDE`
(`hop3` §4, `hops/README.md`) |
| **Note** | Separate from WAT promo; no AFT XML produced |

---

### Rank 5 — GM2AU / OneHand direct credit message

| | |
|---|---|
| **Viability** | **Not a control point** — observation sink downstream of commit |
| **Evidence** | `hop4-onehand-to-slotlog.md` §5: Hop 4 is output only; GM2AU applies
what WAT2AFT already committed |
| **WinDivert on GM2AU** | No analogue to Hop 2 proven for credit creation; splicing
`:50010` would at best forge messages without a committed Hop 3 state |

---

### Rank 6 — Hand-edit Aurum state `.dat` files

| | |
|---|---|
| **Viability** | **Not recommended / unverified** |
| **Evidence** | State under `var\state\goldclub.aurum.services\` (payments,
validations, features, bonuses `.dat`) — format undocumented in repo |
| **Risk** | High — corruption, no rollback story, no log oracle match |

**Assessment only:** credit staging via files was not investigated on live `.171`
(SMB blocked). Treat as last resort with full backup, not a bypass.

---

### Rank 7 — AFT XML state hand-edit (`.90` probe 2026-06-17)

| | |
|---|---|
| **Viability** | **Dead-end** |
| **Evidence** | [`hop3-spoof-probe-20260617.md`](hop3-spoof-probe-20260617.md) §4:
`aftMostRecentTransaction_v1.xml` is WAT2AFT **output**; spoof write did not
change `transactionId`; no Cashless In after edit |
| **Note** | PowerShell `[xml]` cannot manipulate `transactionId` char arrays;
real state likely in binary `.dat` or memory |

---

### Rank 8 — Generic .NET remoting invoke (EGM `:50010`, HOST `:50011`)

| | |
|---|---|
| **Viability** | **Dead-end** without typed Aurum assemblies |
| **Evidence** | [`hop3-spoof-probe-20260617.md`](hop3-spoof-probe-20260617.md) §2–§3:
both endpoints return `MarshalByRefObject` proxies only; 88+ method guesses
failed; GM2AU on `169.254.243.18:50010` not `localhost` |
| **Re-eval** | Typed client may reach NRE path (Rank 3) but not mint credits |

---

## 3. Recommendation

**No viable non-SAS-online credit vector is documented in this repo.** All real
commits on `.90` flow through WAT2AFT after a live SAS session. The practical
order for `.171`:

1. **Restore COM11 upstream polls** (physical SAS host on MUX, or approved serial
   emulator) — see `com11-emulator-plan.md`, `171-landing-plan.md`.
2. Confirm WAT2AFT stops logging `NO OWNED DEVICE`.
3. Re-run `Invoke-WinDivertAft.ps1 -Send -IP 10.0.0.171` (proven injector).
4. Verify with existing hop-5 oracle (`Cashless In`, `commitTransfer`, meter line).

**Parallel unblock:** add `GOLD-CLUB\test` credential for `10.0.0.171` to complete
live inventory (endpoints, state paths, process diff) — PsExec/SMB currently denied.

---

## 4. Dead-ends (summary)

| Vector | Why dead |
|--------|----------|
| Loopback `80`/`81` inject | Aurum decodes; EGM stays offline |
| `WdRespond` / table slave on loopback | No serial enumeration; online gated on COM11 |
| WAT remoting new `requestTransfer` | `RequestTransferPosted` NRE |
| Generic remoting invoke `:50010`/`:50011` | `MarshalByRefObject` only; interface hidden (2026-06-17) |
| AFT XML hand-edit | Write-only output; OneHand does not re-read (2026-06-17) |
| `setBonusAward` | Host rejects transaction id sequence |
| SlotLog / GM2AU splice | Output sink only; no meter without Hop 3 commit |
| State `.dat` hand-edit | Unverified, high risk |

---

*Authored locally 2026-06-16; updated 2026-06-17 with `.90` Hop 3 spoof probe
results (`hop3-spoof-probe-20260617.md`). Prior sources: `hops/hop3–hop4`,
`../RUNBOOK.md`, `com11-emulator-plan.md`, mux diagnostic
[5ed4f26a](5ed4f26a-b560-4739-9ae5-bf22f67dc5a8).*
