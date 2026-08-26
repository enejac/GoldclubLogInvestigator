# IGT / host → ruleta.exe — layered cashflow (roulette)

Cabinet: **10.0.0.90** (`GCC_RT_330106_01` / GST20664).  
Slot equivalent: [`igt-onehand-layered-flow.clean.svg`](igt-onehand-layered-flow.clean.svg).

This file diagrams **all cash paths** on the roulette image. Each path uses the same
six-layer swimlane model as the slot AFT diagram.

---

## Master overview — parallel buses

```mermaid
flowchart TB
    subgraph L0["L0 — Physical"]
        BILL[Bill validator COM3]
        TITO[Ticket printer COM4]
        SASMUX[SAS MUX COM5 @921600]
        SW[Security switch COM6]
        LED[Light tower COM7]
        IGT[IGT SAS tester optional]
    end

    subgraph L1["L1 — CommCtrl gateway"]
        CC[CommCtrl.exe]
        CCS[CommCtrlSAS.exe]
    end

    subgraph L2["L2 — TCP loopback"]
        P30300[":30300 KeyCtrl"]
        P30400[":30400 TITO"]
        P30550[":30550 SAS WakeUp"]
        P30600[":30600 switch"]
        P30700[":30700 lights"]
        P8090[":8090 HTTP middleware"]
    end

    subgraph L3["L3 — Services"]
        AURUM[GoldClub.Aurum.Services]
        HW[HWSubsys.exe]
    end

    subgraph L4["L4 — Game"]
        RULETA[ruleta.exe]
        GODOT[Godot RouletteGUI2]
    end

    subgraph L5["L5 — State"]
        XML[AFT XML disk]
        GM2AU[gm2au noteAcceptor files]
        DEVM[DeviceManagerData.xml]
    end

    subgraph L6["L6 — Logs"]
        LR[ruleta YYYY-MM-DD.log]
        LSAS[sasmsgr qGMID1]
        LHW[HWSubsys log]
    end

    BILL --> CC
    TITO --> CC
    SW --> CC
    LED --> CC
    IGT --> SASMUX --> CCS
    CC --> P30300 & P30400 & P30600 & P30700
    CCS --> P30550
    P30300 --> RULETA
    P30400 --> HW --> RULETA
    P30550 --> AURUM --> XML --> RULETA
    P30600 --> HW
    P30700 --> HW
    GODOT --> P8090 --> RULETA
    RULETA --> LR & GM2AU
    AURUM --> LSAS & DEVM
    HW --> LHW
```

---

## Path 1 — Bill in (physical cash)

**Proven on .90** (2026-07-27): two physical inserts + one synthetic inject.

| Run | Type | Signal after `999` | RCM delta |
|-----|------|-------------------|-----------|
| 12:07:45 | Physical | `821 27` → `BL 1`/`CN 1` | `c=` +200k (1.3M→1.5M) |
| 12:33:23 | Physical | `821 18` `821 19` `822 98` → `BL 10` | `c=` +200k (0→200k) |
| 14:01:43 | **Inject `captured`** | same burst as 12:33 (spliced) | `c=` +200k (0→200k) |

Session diagram: [`igt-ruleta-bill-flow-report.html`](igt-ruleta-bill-flow-report.html)

```mermaid
flowchart TB
    subgraph Physical
        BV[Bill validator stacker]
    end
    subgraph Gateway
        CC[CommCtrl COM3→30300]
    end
    subgraph Protocol["ASCII line protocol (\\r\\n)"]
        HB[899 / AL heartbeat]
        BZ[BZ 0/1 ↔ 888/999 zone poll]
        EV[821 XX bill event]
        BL[BL N / CN N credit notify]
        CH[807 803 701 666 choreography]
    end
    subgraph Game
        R[ruleta KeyCtrl consumer]
    end
    subgraph SAS
        M[SAS meter 000B + LP $31–$37]
    end
    subgraph Verify
        LOG[ruleta RCM s= c= line]
    end

    BV --> CC --> HB & BZ & EV & BL & CH --> R --> M
    R --> LOG
```

| Hop | Layer | Bytes / signal | Tool |
|-----|-------|----------------|------|
| 1 | Physical | Note accepted, escrow | — |
| 2 | COM3→30300 | `821 68`, `821 27`, … | WdSniff / Dallas capture |
| 3 | C2S ack | `BL 1\r\nCN 1\r\n` | capture log |
| 4 | ruleta | RCM credit increase | ruleta log |
| 5 | Aurum | Bill meter SAS poll response | sasmsgr / SAS verify |

Detail: [`../lab/roulette/BILL-ACCEPTOR-PROTOCOL.md`](../lab/roulette/BILL-ACCEPTOR-PROTOCOL.md)

Inject (proven):

```powershell
.\lab\roulette\Invoke-BillInjectRouletteRemote.ps1 -PayloadProfile captured -Credits 200000 -Send
```

---

## Path 2 — AFT cashless in (host transfer)

Same WAT2AFT engine as slot; **different TCP port** and **ruleta** as sink.

```mermaid
sequenceDiagram
    participant Host as IGT SAS / WdPollInject
    participant MUX as COM5 MUX
    participant CCS as CommCtrlSAS
    participant TCP as :30550 / :30500
    participant Aurum as GoldClub.Aurum.Services
    participant XML as AFT state XML
    participant R as ruleta.exe
    participant Log as sasmsgr + ruleta log + tester $72

    Host->>MUX: 80/81 polls + 0x72 AFT
    MUX->>CCS: serial SAS
    CCS->>TCP: 1B80 / 1B81 / 1B017245...
    TCP->>Aurum: established session
    Aurum->>Aurum: requestTransfer → authorize → commit
    Host->>MUX: 69 AFT transfer complete
    Aurum->>XML: FULL_TRANSFER_SUCCESSFUL
    Aurum->>R: apply credit
    R->>Log: RCM c= increased
    Host->>Host: 72 poll — Status 00, amounts + meters
```

| Hop | Slot | Roulette |
|-----|------|----------|
| Bridge port | :31150 | **:30550** (WakeUpPort from ClientsSet.xml) |
| Config | COM11 | **COM5** MUX @921600 |
| Game sink | OneHand GM2AU | **ruleta** (no OneHand on this image) |
| Inject | `Invoke-WinDivertAft.ps1` | **`Invoke-WinDivertAftRoulette.ps1 -Send`** |

### Proven runs on `.90` roulette

| When | Method | Amount | ID / evidence |
|------|--------|--------|---------------|
| 2026-07-23 | WinDivert pollaft (no tester) | NonRestricted **100000** | Aurum txn **37**; sasmsgr `0172` |

---

## Path 3 — TITO voucher in / ticket out

Separate from bill bus. CommCtrl must listen on **30400 before HWSubsys** starts
(`Run-FullStack.ps1` ordering).

```mermaid
flowchart LR
    COM4[COM4 physical] --> CC[CommCtrl :30400]
    CC --> HW[HWSubsys FutureLogic tito driver]
    HW --> R[ruleta.exe]
    R -->|Collect wire 249| CC
    CC --> PRN[Ticket print / stacker]
```

| Direction | Trigger | Port | Notes |
|-----------|---------|------|-------|
| Voucher **in** | Barcode scan / insert | :30400 | driverssetup `tito` index 0 |
| Ticket **out** | Godot `Collect` | :30400 + HTTP | wire code **249** = Collect TITO |
| Cashout alt | Collect variants | HTTP → ruleta | 241 hopper, 254 SAS handpay, 402 WAT |

Sniff: `Invoke-HwSniffRemote.ps1 -Ports 30400`

---

## Path 4 — Collect / cash out (software trigger)

No new physical device — Godot sends HTTP; ruleta picks payout channel.

```mermaid
flowchart TB
    UI[Godot Collect button] -->|PUT /api/action Collect| MW[:8090 EmbedIO]
    MW --> R[ruleta.exe payout router]
    R --> TITO[:30400 ticket print]
    R --> HP[Handpay / attendant]
    R --> HOP[Hopper / dispenser]
    R --> WAT[AFT cashout WAT]
```

| Collect wire code | Channel |
|-------------------|---------|
| 241 | Hopper |
| 249 | TITO |
| 252 | Bonus |
| 254 | SAS handpay |
| 282 | Remote |
| 285 | Dispenser |
| 402 | WAT |

Source: [`docs/action-endpoint-commands.md`](../../docs/action-endpoint-commands.md)

Sniff: `Invoke-RuletaGuiSniff.ps1` during Collect press.

---

## Path 5 — Dallas / KeyCtrl (admin + handpay clear)

Shares **:30300** with bill acceptor. Not a credit path by itself — unlocks handpay
and service menu.

```
Physical iButton / keyboard
  → COM3 → CommCtrl :30300
  → ruleta KeyCtrl
  → inject: 700\r\n 701\r\n <ROM>\r\n
  → log: KEY=admin DALLAS=<ROM> EVENT=IN|OUT
```

Tool: `Invoke-DallasSpliceRouletteRemote.ps1`  
Runbook: [`lab/DALLAS-INJECTION-RUNBOOK.md`](../../lab/DALLAS-INJECTION-RUNBOOK.md)

---

## Path 6 — Hardware auxiliary (non-cash but gates cash)

| Port | Device | Role in cashflow |
|------|--------|------------------|
| :30600 | Security switch | Door open → handpay lock |
| :30700 | Light tower | Attendant call / status |
| :25071 | HWSubsys hub | Aggregates HW sessions |

Sniff: `Invoke-HwSniffRemote.ps1 -Ports 30600,30700,25071`

---

## Path 7 — SAS meter readback (accounting oracle)

Read-only verification — no credit injection.

```
Aurum sasmsgr poll responses
  → SAS LP $31–$45 (bill in/out by denom)
  → meter 000B (total bill in)
  → DeviceManagerData notesInStackerAmt
  → GUI SAS verify / meter_comparator.py
```

---

## Slot vs roulette — one-page comparison

| Layer | Slot OneHand | Roulette ruleta |
|-------|--------------|-----------------|
| SAS serial | COM11 | COM5 MUX |
| SAS TCP | :31150 / :31100 | **:30550** / :30500 |
| Game EXE | OneHand.exe | ruleta.exe |
| GUI | Embedded in OneHand | Godot → :8090 |
| Dallas | :30800 | **:30300** (shared w/ bill) |
| Bill | (not diagrammed) | **:30300** |
| TITO | COM4 :30400 | COM4 :30400 |
| AFT inject script | `Send-TestAft1000.ps1` | `Invoke-WinDivertAftRoulette.ps1` |
| Primary log | SlotLog | `ruleta\` + sasmsgr |

---

## Related files

| File | Purpose |
|------|---------|
| [`igt-ruleta-bill-flow-report.html`](igt-ruleta-bill-flow-report.html) | **Bill flow HTML report** (2026-07-27) |
| [`igt-ruleta-visio-shapes.txt`](igt-ruleta-visio-shapes.txt) | Copy-paste Visio boxes (all paths) |
| [`lab/roulette/CASHFLOW-LAYERS.md`](../lab/roulette/CASHFLOW-LAYERS.md) | Operator hub + sniff cheat sheet |
| [`aft/hops/README.md`](../hops/README.md) | AFT hop decouple map (applies to path 2) |
| [`windivert-pollaft-inject-flow.md`](windivert-pollaft-inject-flow.md) | L0–L3 inject layers |
