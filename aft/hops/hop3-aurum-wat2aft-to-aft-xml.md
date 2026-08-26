# Hop 3 — GoldClub.Aurum.Services (WAT2AFT) internal processing → AFT XML state

Defensive QA / robustness architecture mapping of **one hop** of the verified
AFT/WAT credit-transfer chain on the owned lab cabinet.

- Cabinet: `10.0.0.90` (`GST20664`), EGM `GCC_ST_20664_01`, asset `777`
- Hop under study: **`GoldClub.Aurum.Services` (the WAT2AFT service) — the layer
  that DECIDES and COMMITS the transfer (`requestTransfer` → `authorizeTransfer`
  → `commitTransfer`) and persists it as AFT state XML.**
- This document is read-only analysis. No transfer was posted to the cabinet while
  producing it.

Full chain for context (this file documents only hop 3):

```
IGT SAS tester (COM11)
  -> CommCtrlSAS.exe          (serial<->TCP bridge, loopback 31100/31150)
  -> GoldClub.Aurum.Services  (WAT2AFT: requestTransfer->authorizeTransfer->commitTransfer)   <-- HOP 3
  -> AFT XML state files
  -> OneHand (GM2AU_aurumExecute, "Cashless In")
  -> SlotLog -> verification
```

---

## 1. Role of Aurum WAT2AFT — the trust / decision point

`GoldClub.Aurum.Services` runs the **WAT2AFT** component. It is the process that
actually *decides* whether a credit transfer is allowed and *commits* it; the SAS
bytes upstream (hop 1/2) and the OneHand credit downstream (hop 4) are inputs and
outputs around this decision. The service announces itself in its own log:

```text
2026-06-01T08:42:17.786+01:00 INFO [:] *****  [GCC_ST_20664_01]  [13]  *****   STARTING WAT2AFT
2026-06-01T10:35:26.728+01:00 INFO [:] *****  [GCC_ST_20664_01]  [35]  WAT2AFT UP! v.1.0.35.31197
```

For each transfer WAT2AFT creates an `AurumTransaction` of type `WatTransaction`
and drives it through a clean three-phase handshake, then writes the result to
disk as XML:

1. `requestTransfer` — the host/SAS request is received and a WAT transaction is
   created (account, amount, credit type, `transactionId`, `hostRequestId`).
2. `authorizeTransfer` — the service authorizes the amount/account (this is the
   accept/deny decision point).
3. `commitTransfer` — the funds are committed with `transferException="0"` (no
   error) and a `transferDateTime`.

This is the verified credit boundary on this cabinet. `Convert-AftHistory.ps1`
(`.DESCRIPTION`) states the architectural fact plainly:

> The AFT credit result is persisted by `GoldClub.Aurum.Services` into the state
> XML this script parses … If you need live SAS wire visibility, that is a
> separate passive capture on the CommCtrlSAS loopback ports - not this report.
> — `Convert-AftHistory.ps1`

and `AFT_Robustness_Findings.md` #5 confirms the commit happens here, not as a
raw host SAS `0x72` on the bridge:

> On this cabinet the credit is committed through the Aurum/WAT2AFT service path,
> not a host-originated raw SAS `0x72` visible on that bridge.
> — `report/AFT_Robustness_Findings.md` #5

The persisted XML lives at:
`\\10.0.0.90\c$\Goldclub\var\state\goldclub.aurum.services\GCMessenger\SASControler1\`
with the three relevant file families:

- `*_aftMostRecentTransaction_v*.xml` — mirror of the latest transaction
- `*_aftTransactionHistory_i*_v*.xml` (under `History\`) — per-slot history
- `*_aftCurrentSettings_v*.xml` — registration status / key / transfer flags

---

## 2. Internal message sequence (ordered, with timestamps)

The representative transfer below is the **current most-recent transaction** on
the cabinet: WAT transaction id `80`, txn label `…est Transaction23`, a `$1,000.00`
promo (non-restricted) in-house transfer that completed `FULL_TRANSFER_SUCCESSFUL`
on `2026-06-15`. The ordered steps, fused from the WAT2AFT service log and the
AFT XML phase timestamps:

| # | Step | Source | Timestamp |
|---|---|---|---|
| 1 | `TRANSFER REQUEST FROM SERVER STARTED` (NonRestricted 100000, `…est Transaction23`) | Aurum service log | `14:43:11.723` |
| 2 | `AurumTransaction` (`WatTransaction`) created — `transferInitiatedTime` | AFT XML | `14:43:11.7365915` |
| 3 | `requestTransfer` (account `…_promo`, `watAmount=100000`, `transactionId=80`) | AFT XML | `14:43:11.7365915` |
| 4 | `authorizeTransfer` (same amount/account, accepted) | AFT XML | `14:43:11.7531113` |
| 5 | `ALL WAT TRANSACTIONS FINISHED` | Aurum service log | `14:43:11.959` |
| 6 | `commitTransfer` (`transferAmount=100000`, `transferException=0`) — `transferDateTime` | AFT XML | `14:43:11.8861445` |
| 7 | OneHand applies it: `GM2AU_aurumExecute … Cashless In: $1,000.00` | SlotLog | `14:43:11.874` |
| 8 | `TRANSFER REQUEST FROM SERVER FINISHED` (`FULL_TRANSFER_SUCCESSFUL`, NonRestricted Com 100000) | Aurum service log | `14:43:27.796` |

This is the same three-phase shape recorded for the earlier canonical transfer in
`AFT_Robustness_Findings.md` Section B (request → authorize → commit, with
`transferException=0`); the table above is the live re-verification of it.

---

## 3. Real dump

### 3a. `aftMostRecentTransaction` XML (trimmed, annotated)

Read read-only from
`\\10.0.0.90\c$\Goldclub\var\state\goldclub.aurum.services\GCMessenger\SASControler1\GCC_ST_20664_01_aftMostRecentTransaction_v1.xml`
(`<version>37</version>`). Trimmed to the fields that matter; annotations marked
`<!-- … -->`.

```xml
<AftTransactionSerializer>
  <aftTransaction>
    <instanceId>2357b470-3da7-40c1-a284-a587f8a23781</instanceId>          <!-- unique Aurum transaction instance -->
    <transferStatus>FULL_TRANSFER_SUCCESSFUL</transferStatus>              <!-- the commit decision: success -->
    <transferType>TRANSFER_INHOUSE_AMOUNT_FROM_HOST_TO_GAMING_MACHINE</transferType>
    <requestedNonRestrictedAmount>100000</requestedNonRestrictedAmount>    <!-- promo $1,000.00 requested (cents) -->
    <obtainedNonRestrictedAmount>100000</obtainedNonRestrictedAmount>      <!-- $1,000.00 actually credited -->
    <assetNumber>CQMAAA==</assetNumber>                                    <!-- base64 -> 09 03 00 00 LE = 777 -->
    <transactionIdLength>18</transactionIdLength>
    <transactionId>                                                        <!-- char codes -> ".est Transaction23" (tester label) -->
      <char>0</char><char>101</char><char>115</char><char>116</char><char>32</char>
      <char>84</char><char>114</char><char>97</char><char>110</char><char>115</char>
      <char>97</char><char>99</char><char>116</char><char>105</char><char>111</char>
      <char>110</char><char>50</char><char>51</char>
    </transactionId>
    <transactionCompletedDateTime>ICYGFRRDEQ==</transactionCompletedDateTime> <!-- 7 BCD bytes -> 2026-06-15 14:43:11 -->
    <requestTextMessage>$;AFT_BONUS;type=0</requestTextMessage>            <!-- promo-bonus marker text -->
    <aftTransactionReported>true</aftTransactionReported>
    <aftTransactionFinished>true</aftTransactionFinished>
    <aurumTransactions>
      <AurumTransaction xsi:type="WatTransaction">                         <!-- WAT2AFT transaction object -->
        <transferInitiated>true</transferInitiated>
        <transferFinished>true</transferFinished>
        <transferInitiatedTime>2026-06-15T14:43:11.7365915+01:00</transferInitiatedTime>

        <!-- PHASE 1: requestTransfer -->
        <requestTransfer d5p1:idNumber="GCC_ST_20664_01" d5p1:accountId="GCC_ST_20664_01_promo"
            d5p1:watAmount="100000" d5p1:creditType="promo" d5p1:poolId="0"
            d5p1:transferAction="withdraw" d5p1:transactionId="80"
            d5p1:hostRequestId="7d29c82c-b8cb-4be9-a9f9-4da219d1341b"
            d5p1:egmDateTime="2026-06-15T14:43:11.7365915+01:00" />

        <!-- PHASE 2: authorizeTransfer (accept decision) -->
        <authorizeTransfer d5p1:accountId="GCC_ST_20664_01_promo" d5p1:watAmount="100000"
            d5p1:creditType="promo" d5p1:transferAction="withdraw" d5p1:transactionId="80"
            d5p1:egmDateTime2="2026-06-15T14:43:11.7531113+01:00" />

        <!-- PHASE 3: commitTransfer (funds committed, no error) -->
        <commitTransfer d5p1:accountId="GCC_ST_20664_01_promo" d5p1:transferAction="withdraw"
            d5p1:transferAmount="100000" d5p1:transferException="0"
            d5p1:transferDateTime="2026-06-15T14:43:11.8861445+01:00" d5p1:transactionId="80" />

        <watTransactionDetails>
          <WatTransactionDetails>
            <creditType>promo</creditType>
            <amountToTransfer>100000</amountToTransfer>
            <account d7p1:accountId="GCC_ST_20664_01_promo" d7p1:withdrawOk="true"
                d7p1:depositOk="true" d7p1:creditType="promo">
              <AccountOwner>GCC_ST_20664_01</AccountOwner>
              <Balance>0</Balance>                                         <!-- lab "account" is unbacked (balance 0) -->
            </account>
          </WatTransactionDetails>
        </watTransactionDetails>
      </AurumTransaction>
    </aurumTransactions>
  </aftTransaction>
</AftTransactionSerializer>
```

Field decoding (`assetNumber`, BCD time, `transactionId` chars) follows
`Convert-AftHistory.ps1` / `Parse-AftHistory.ps1`:
`CQMAAA==` → bytes `09 03 00 00` little-endian = **777**;
`ICYGFRRDEQ==` → BCD `2026 06 15 14 43 11` = **2026-06-15 14:43:11**;
char list `0,101,115,116,32,…` = `".est Transaction23"`.

The companion `aftCurrentSettings_v2.xml` (`<version>40</version>`) shows the
trust-boundary state at the time of the commit:

```xml
<currentAftSettings>
  <registrationStatus>GAMING_MACHINE_NOT_REGISTERED</registrationStatus>   <!-- NOT registered -->
  <registrationKey>AAAAAAAAAAAAAAAAAAAAAAAAAAA=</registrationKey>          <!-- all-zero 20-byte key -->
  <transferFlags>
    <transferAllowedOnlyIfLocked>false</transferAllowedOnlyIfLocked>       <!-- no lock gate -->
  </transferFlags>
</currentAftSettings>
```

### 3b. Corresponding Aurum WAT2AFT service log lines

Read read-only from
`\\10.0.0.90\c$\Goldclub\var\log\GoldClub.Aurum.Services\2026-06-15.log`:

```text
2026-06-15T14:43:11.723+01:00 INFO [:] *****  [GCC_ST_20664_01]  [12]  TRANSFER REQUEST FROM SERVER STARTED: TransferType(TRANSFER_INHOUSE_AMOUNT_FROM_HOST_TO_GAMING_MACHINE), TransferCode(TRANSFER_REQUEST_FULL_TRANSFER_ONLY), Cashable(0), Restricted(0), NonRestricted(100000), ID( est Transaction23).
2026-06-15T14:43:11.959+01:00 INFO [:] *****  [GCC_ST_20664_01]  [73]  ALL WAT TRANSACTIONS FINISHED.
2026-06-15T14:43:27.796+01:00 INFO [:] *****  [GCC_ST_20664_01]  [15]  TRANSFER REQUEST FROM SERVER FINISHED: TransferType(TRANSFER_INHOUSE_AMOUNT_FROM_HOST_TO_GAMING_MACHINE), TransferStatus(FULL_TRANSFER_SUCCESSFUL), Cashable Req(0), Cashable Com(0), Restricted Req(0), Restricted Com(0), NonRestricted Req(100000), NonRestricted Com(100000), ID( est Transaction23).
```

And the downstream OneHand credit (SlotLog `\\10.0.0.90\c$\Goldclub\var\log\SlotLog\2026-06-15.log`),
proving the commit landed as real credit:

```text
2026-06-15T14:43:11.874+01:00 INFO  [GM2AU_aurumExecute] OneHand.AurumEGM - Cashless In: $1,000.00
```

(For cross-reference, the upstream raw SAS bytes of this transfer family are
logged by the SAS messenger as `qGMID1:0172…` lines — see
`../protocol-raw-traffic.md` §1; hop 3 is where those decoded bytes become the
committed `WatTransaction` above.)

---

## 4. Direct-post path at THIS layer — attempted and FAILED for a new transfer

A direct-post tool (now removed) targeted the WAT2AFT service over secure .NET
remoting (`http://GST20664:50011/SASControler1`, the same `POST /SASControler1
HTTP/1.1` on `:50011` seen in the WinDivert capture, `../protocol-raw-traffic.md`
§4), skipping the IGT tester and the CommCtrlSAS bridge. The intent was to obtain
a transparent proxy, build `host.GetNewCommand(deviceClassBase.WAT) as WAT`, fill
a `requestTransfer` (`creditType = promo`, `transferAction = withdraw`,
`accountId`, `watAmount`, `transactionId`, `hostRequestId`, `idNumber = egmId`) —
the same `requestTransfer` attributes seen in the §3a XML — and `PostCommand` it.

### Outcome: does NOT complete a fresh transfer

- **Remoting reachability: proven.** The probe path connects through the secure
  channel, gets a transparent proxy, and reads `GCMessengerId` / `MessengerType`
  and a non-null `WAT` command template. The endpoint is reachable and accepts
  command objects over the network.
- **Direct HOST `requestTransfer` for a brand-new id: FAILS.** Posting
  `WAT.requestTransfer` directly to `SASControler1` calls
  `GoldClub.Aurum.WATmanager.RequestTransferPosted`, which expects an
  already-existing Aurum transaction and throws a `NullReferenceException` for a
  new transaction id. The remoting call is accepted, but the engine assumes an
  existing Aurum transaction context, so minting a wholly new transfer from
  nothing via the HOST proxy hits an NRE.
- **Legacy `setBonusAward` (GM2AU bonus): also not usable.** This jackpot/bonus
  path was host-rejected with `TRANSACTIONID SEQUENCE NOT ALLOWED TO USE ON HOST
  SIDE`; it is not the AFT/WAT promo path and produces no AFT XML.

Because the Hop 3 direct-post could not mint a new transfer, that approach was
abandoned and its tooling removed. **The working decouple point is Hop 2 (the
CommCtrlSAS → Aurum bridge), via WinDivert IN-STREAM injection** — proven
2026-06-15 with two $1,000 promo credits, SAS host connected. See
`hop2-commctrlsas-bridge-to-aurum.md` §4–§5 and `../RUNBOOK.md`.

---

## 5. Decoupling difficulty

**Verdict: FAILED for a brand-new transfer (the direct-post NRE blocks it).**

Hop 3 is a pure software/service layer, but the specific operation we need — minting
a wholly new WAT transfer from the HOST proxy — does not complete:

| Factor | Hop 3 (this layer) | Effect |
|---|---|---|
| Interface | .NET remoting over HTTP (`:50011` HOST, `:50010` EGM) | Network-reachable; no RS-232 / no polled SAS timing |
| Reachability | Proxy + command template obtainable | Endpoint accepts command objects |
| New-transfer post | `RequestTransferPosted` `NullReferenceException` | **Blocks** a brand-new id; no AFT XML written |
| Legacy bonus | `setBonusAward` host-rejected | `TRANSACTIONID SEQUENCE NOT ALLOWED TO USE ON HOST SIDE` |
| Auth gate at this layer | None effective (see below) | Unregistered cabinet + all-zero key still commits |

Contrast with the other hops: hop 1 needs the physical IGT tester on `COM11`;
**hop 2 is the WORKING decouple point** — WinDivert injects a segment into the
EXISTING `31150 → ephemeral` SAS flow at the correct sequence number, which Aurum
accepts as the next in-order SAS bytes and commits
(`hop2-commctrlsas-bridge-to-aurum.md` §4–§5). Hop 3 remoting, by contrast, cannot
conjure a new transfer because of the `RequestTransferPosted` NRE.

### Trust-boundary implication (finding #4)

Because WAT2AFT is the commit point and there is **no effective registration gate
in front of it**, anything that can post at this layer commits real credit:

> The gaming machine is NOT registered, yet transfers commit … `registrationStatus
> = GAMING_MACHINE_NOT_REGISTERED`, `registrationKey = AAAA…=` (all-zero),
> `transferAllowedOnlyIfLocked = false`. Despite the unregistered state and
> all-zero key, the $1,000 transfers still completed successfully.
> — `report/AFT_Robustness_Findings.md` #4

The §3a `aftCurrentSettings` dump re-confirms this exact state at commit time. So
the SAS AFT registration/key (the nominal hardware-level trust boundary) is **not**
the control protecting this hop; WAT authorization inside WAT2AFT is. That makes
hop 3 both the easiest to reach *and* the most consequential to monitor —
`Convert-AftHistory.ps1`'s `SUCCESS_WHILE_UNREGISTERED` check exists precisely to
flag commits that happen while the cabinet reports `NOT_REGISTERED`.

---

## 6. Extended spoof probe (2026-06-17) — all alternative vectors dead

A systematic WinRM probe on `10.0.0.90` retried five vectors that bypass Hop 2
WinDivert. **None produced `Cashless In` credits.** Full report:
`../investigations/hop3-spoof-probe-20260617.md`.

| Vector | Result |
|--------|--------|
| EGM remoting `:50010/GM2AU` (reflection + invoke) | Proxy is `MarshalByRefObject` only; binds `169.254.243.18`, not localhost; 88+ guessed methods not found |
| HOST remoting `:50011/SASControler1` (pre-create txn, direct authorize/commit) | Untyped proxy exposes 7 base methods only; WAT/`AurumTransaction` unreachable without Aurum assemblies |
| AFT XML hand-edit + OneHand refresh | XML is write-only WAT2AFT output; OneHand does not re-read files; no Cashless In |
| `.dat` / trigger file | Not viable without deserializing binary state |

**Implication:** The 2026-06-15 direct-post NRE (§4) remains the only *typed*
Hop 3 path documented; the 2026-06-17 probe confirms generic remoting and XML
spoofing cannot substitute for the Hop 2 in-stream inject. Interface discovery
requires decompiling `OneHand.exe` or capturing binary remoting on
`169.254.243.18:50010`.

---

## Source map

- WAT2AFT role / three-phase handshake / commit-not-raw-SAS:
  `report/AFT_Robustness_Findings.md` #3, #5, Section B; banner from live
  `\\10.0.0.90\c$\Goldclub\var\log\GoldClub.Aurum.Services\*.log`
- AFT XML field names / decoders (`instanceId`, `transferStatus`, `transferType`,
  `obtainedCashableAmount`, `assetNumber`, `aurumTransactions/AurumTransaction`,
  `requestTransfer`/`authorizeTransfer`/`commitTransfer`, `hostRequestId`,
  `transactionId`, `transferException`): `Convert-AftHistory.ps1`,
  `Parse-AftHistory.ps1`
- Direct-post attempt (secure .NET remoting to `:50011`, `requestTransfer`,
  `PostCommand`) and why it FAILED for a new id (`RequestTransferPosted` NRE):
  recorded in `../RUNBOOK.md` §7 (tooling since removed)
- Extended 2026-06-17 spoof probe (EGM/HOST remoting, XML manipulation):
  `../investigations/hop3-spoof-probe-20260617.md` §1–§6
- Working method (Hop 2 in-stream WinDivert injection):
  `hop2-commctrlsas-bridge-to-aurum.md` §4–§5, `../RUNBOOK.md`,
  `../../lab/Invoke-WinDivertAft.ps1`, `../../probes/WdInject.cs`
- Remoting endpoint `:50011` / `POST /SASControler1` and `qGMID1:` byte logging
  cross-reference: `../protocol-raw-traffic.md` §1, §4
- Unregistered-but-commits trust finding + monitoring check:
  `report/AFT_Robustness_Findings.md` #4, Section D
- Live evidence (read-only):
  - `…\GCMessenger\SASControler1\GCC_ST_20664_01_aftMostRecentTransaction_v1.xml`
  - `…\GCMessenger\SASControler1\GCC_ST_20664_01_aftCurrentSettings_v2.xml`
  - `\\10.0.0.90\c$\Goldclub\var\log\GoldClub.Aurum.Services\2026-06-15.log`
  - `\\10.0.0.90\c$\Goldclub\var\log\SlotLog\2026-06-15.log`
