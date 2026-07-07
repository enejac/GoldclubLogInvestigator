# AFT / WAT Transfer Chain — Per-Hop Decouple Map

Lab cabinet: **GoldClub Aurum EGM @ 10.0.0.90, asset 777**. Per-hop architecture mapping for AFT/WAT robustness QA.

Verified chain (two successful $1,000 promo transfers on 2026-06-15: via the IGT tester UI, and via WinDivert in-stream injection **without tester UI action** — txn 23 @ 14:43:11 and txn 41 @ 15:00:30; SAS host must still be connected and polling):

```
SAS host COM11 ─serial SAS─┐
                           ├▶ CommCtrlSAS.exe ─TCP 31100/31150 (0x1B)─▶ Aurum WAT2AFT ─▶ AFT XML ─▶ OneHand ─▶ SlotLog ─▶ verification PASS
WinDivert inject (0x72 only) ┘    [Hop 2]                                  [Hop 3]         (commit)    [Hop 4]      [Hop 5]
   into live 31150→ephemeral
   [Hop 2, in-stream]
```

The WinDivert path injects directly into the live Hop 2 stream (see the decouple verdict below and [`../RUNBOOK.md`](../RUNBOOK.md)); the hop-mapping docs themselves are read-only analysis.

## Per-hop files

| Hop | File | What it carries |
|-----|------|-----------------|
| 1 | [hop1-igt-tester-to-commctrlsas.md](hop1-igt-tester-to-commctrlsas.md) | Serial SAS on COM11 @ 921600, addr 1, CRC-16/KERMIT; `0x80/0x81` polls, `0x72` transfer, `0x73` registration |
| 2 | [hop2-commctrlsas-bridge-to-aurum.md](hop2-commctrlsas-bridge-to-aurum.md) | Loopback SAS-over-TCP, `0x1B`+frame, `qGMID1:` log; exactly 2 sockets (31100/31150) |
| 3 | [hop3-aurum-wat2aft-to-aft-xml.md](hop3-aurum-wat2aft-to-aft-xml.md) | WAT2AFT trust/decision layer: requestTransfer→authorizeTransfer→commitTransfer; AFT state XML |
| 4 | [hop4-onehand-to-slotlog.md](hop4-onehand-to-slotlog.md) | `GM2AU_aurumExecute` applies credit → `Cashless In: $X` SlotLog line (output sink) |
| 5 | [hop5-verification-readback.md](hop5-verification-readback.md) | Read-only oracle: reads XML + SlotLog → PASS/FAIL |

## Decouple verdict (verified 2026-06-15 — supersedes the earlier guess)

The working decouple point is **Hop 2 (the CommCtrlSAS → Aurum bridge), via WinDivert IN-STREAM injection** — not Hop 3. This was proven on 2026-06-15 with **two independent $1,000 promo credits** with SAS host connected (see [`../RUNBOOK.md`](../RUNBOOK.md)). The earlier ranking below has been corrected accordingly.

| Hop | Decouple difficulty | Why |
|-----|--------------------|-----|
| 1 — IGT ↔ CommCtrlSAS (serial) | **HARD** | Needs a real serial SAS host emulator on COM11: real-time polling, link sync, CRC-16/KERMIT, stateful transfer/interrogate. Not a socket write. |
| 2 — Bridge ↔ Aurum (TCP) | **WORKING ✅ (in-stream WinDivert)** | A NEW TCP socket to `31150` still fails (accepts bytes but is **not merged** into the live stream — no `qGMID1:`, no transfer). The proven fix is to inject a TCP segment INTO the EXISTING `31150 → ephemeral` flow at `SEQ = origSeq + origPayloadLen` via WinDivert, so Aurum reads it as the next in-order SAS bytes and commits. Tooling: `../../Invoke-WinDivertAft.ps1` / `../../WdInject.cs` (wrapper `../../Send-TestAft1000.ps1 -Send`). |
| 3 — Aurum WAT2AFT | **FAILED for a new transfer** | The direct .NET-remoting `WAT.requestTransfer` to `http://GST20664:50011/SASControler1` reaches the engine but `RequestTransferPosted` throws `NullReferenceException` when minting a wholly new transaction id. Does not complete a fresh transfer. |
| 4 — OneHand → SlotLog | **N/A** | Output sink. Forging a log line creates no real credit and the AFT↔SlotLog reconciliation flags it. |
| 5 — Verification | **EASY (already done)** | Pure read-only oracle; "decoupling" just means automation, which exists. Safest layer to extend. |

### Recommendation

**Decouple at Hop 2 using WinDivert in-stream injection** (`../../Invoke-WinDivertAft.ps1` / `../../WdInject.cs`). The key nuance: a *new* socket to `31150` does not work — `CommCtrlSAS` never merges a separate connection into the live serial-backed SAS session. Injecting INTO the existing flow with the correct SEQ does, because Aurum sees the bytes as the next in-order data on its established session. Full method, the live-injection evidence, and the dead-ends are in [`../RUNBOOK.md`](../RUNBOOK.md).

What does **not** work (recorded so nobody retries):
- **Hop 3 direct-post (.NET remoting WAT `requestTransfer`)** — `RequestTransferPosted` `NullReferenceException` for a brand-new id.
- **Hop 3 generic remoting on `:50010` / `:50011`** — interfaces hidden behind `MarshalByRefObject`; GM2AU binds `169.254.243.18` not localhost (probe 2026-06-17, [`../investigations/hop3-spoof-probe-20260617.md`](../investigations/hop3-spoof-probe-20260617.md)).
- **Hop 3 AFT XML hand-edit** — files are WAT2AFT write-only outputs; OneHand does not re-read them for credit.
- **Raw TCP socket to `31150`** — write succeeds but is never merged into the live stream (no `qGMID1:`).
- **Legacy `setBonusAward` jackpot** — host-rejected: `TRANSACTIONID SEQUENCE NOT ALLOWED TO USE ON HOST SIDE`.

Note: this layer has **no effective registration gate** — an unregistered cabinet with an all-zero key still commits (finding #4). That is exactly why the `SUCCESS_WHILE_UNREGISTERED` monitor exists; keep it on.

For the test oracle, pair any stimulus with **Hop 5** (`../../Invoke-AftTransferTest.ps1` / `../../Convert-AftHistory.ps1`) for automatic PASS/FAIL.
