# WinDivert pollaft AFT inject — flow diagrams

Reference: [`PROVEN-INJECT-PROCEDURE.md`](../PROVEN-INJECT-PROCEDURE.md), [`no-physical-polls-layers.md`](../no-physical-polls-layers.md).

Cabinet **10.0.0.90** (GST20664), path **`-SasPollMode WinDivert`**, no physical IGT polls.

---

## 1. Orchestrator flow (`Invoke-WinDivertAft.ps1 -Send`)

```mermaid
flowchart TD
    START([Operator runs inject]) --> DRY{Pass -Send?}
    DRY -->|No| DRYRUN[DryRun: print packet only]
    DRY -->|Yes| SMB[SMB / WinRM to cabinet]

    SMB --> WATIDLE{Wait-AurumAftReady<br/>120s max}
    WATIDLE -->|Exception 69 / in progress| ABORT1([Exit 5: WAT busy])
    WATIDLE -->|Idle| WARM{Wait WAT2AFT UP<br/>AurumWarmupSec}

    WARM -->|Not seen| WARN[WARN: may ingest without credit]
    WARM -->|UP| POLLCHECK{Recent sasmsgr 80/81?}

    POLLCHECK -->|No| POLLAFT[Use WdPollInject pollaft<br/>sim 1B81/1B80 + AFT]
    POLLCHECK -->|Yes| WDI[Use WdInject.exe only]

    POLLAFT --> BRIDGE{31150 ESTABLISHED<br/>and TCP traffic?}
    WDI --> BRIDGE

    BRIDGE -->|Silent s2c=0| WAKE[Invoke-WakeSasBridge.ps1<br/>restart services]
    WAKE --> BRIDGE
    BRIDGE -->|No connection| FAIL1([NO_ESTABLISHED_31150])

    BRIDGE -->|Traffic OK| RUN[Remote WdPollInject pollaft<br/>WinRM on cabinet]

    RUN --> ING{sasmsgr 0172<br/>full hex match?}
    ING -->|No| RETRY{Retries left?}
    RETRY -->|Yes| RUN
    RETRY -->|No| FAIL2([INGESTED: NO])

    ING -->|Yes| CRED{GM2AU / SlotLog<br/>100000 credit?}
    CRED -->|Yes| OK([SUCCESS: ingest + credit])
    CRED -->|No| PART([PARTIAL: ingest only])
```

---

## 2. Layer stack (what must be alive)

```mermaid
flowchart BT
    subgraph L0["L0 — CommCtrlSAS infrastructure"]
        COM[COM11 / MUX optional]
        LISTEN[Listen 31100 + 31150]
        SVC[Service: GoldClub Serial Communication Gateway SAS]
    end

    subgraph L1["L1 — TCP bridge session"]
        TCP[CommCtrlSAS:31150 ↔ Aurum:ephemeral<br/>ESTABLISHED + bytes flowing]
    end

    subgraph L2["L2 — Poll stream (~200 ms)"]
        SIM[WdPollInject: 1B81 / 1B80<br/>SIMULATED — no IGT]
    end

    subgraph L3["L3 — AFT credit commit"]
        AFT[Inject 1B + SAS 0x72]
        WAT[WAT2AFT authorize + commit]
        GM[OneHand GM2AU / SlotLog credit]
    end

    L0 --> L1 --> L2 --> L3

    style L2 fill:#e8f5e9
    style SIM fill:#c8e6c9
```

**WinDivert replaces L2 organic polls only.** L0+L1 must be real (service restart if wedged).

---

## 3. pollaft sequence (on cabinet, one inject attempt)

```mermaid
sequenceDiagram
    participant OP as Operator PC
    participant WR as WinRM
    participant WD as WdPollInject<br/>(pollaft)
    participant CC as CommCtrlSAS<br/>:31150
    participant AU as Aurum<br/>sasmsgr client
    participant WAT as WAT2AFT
    participant GM as OneHand GM2AU

    OP->>WR: Invoke-WinDivertAft -Send
    WR->>WD: pollaft ephem, 14s window
    WD->>WD: WinDivertOpen loopback 31150

    Note over WD,AU: T+0..2s warmup
    loop Every 200 ms
        WD->>CC: inject 1B81 or 1B80 (S2C)
        CC->>AU: relay bytes
        AU->>AU: log qGMID1:80/81
    end

    Note over WD,AU: T+~2s AFT
    WD->>CC: inject 1B + 0x72 AFT frame
    CC->>AU: relay 75 bytes
    AU->>AU: log qGMID1:0172...

    Note over WD,AU: T+2..5s post-AFT polls (required)
    loop ~3 s more @ 200 ms
        WD->>CC: inject 1B80/1B81
        CC->>AU: relay
        AU->>AU: log qGMID1:80/81 after 0172
    end

    AU->>WAT: decode 0x72 transfer
    WAT->>GM: requestTransfer / commit
    GM->>GM: Withdraw successful 100000

    Note over WD: DRAIN swallow delta, CLOSE
    WD-->>WR: aftInjected=True, EXITCODE=0
    WR-->>OP: verify sasmsgr + credit logs
```

---

## 4. pollaft internal timeline

```mermaid
gantt
    title WdPollInject pollaft window (defaults)
    dateFormat X
    axisFormat %Ss

    section Polls
    C2S template learn     :a1, 0, 200ms
    Warmup 1B81/1B80       :a2, 200ms, 2000ms
    Post-AFT 1B80/1B81     :a3, 2000ms, 5000ms

    section AFT
    AFT_INJECTED 0x72      :milestone, 2000ms, 0ms

    section Drain
    DRAIN begin            :milestone, 5000ms, 0ms
    Swallow delta / close  :a4, 5000ms, 9000ms
```

---

## 5. Bridge wake decision

```mermaid
flowchart LR
    A[Get-NetTCPConnection<br/>31150] --> B{State?}
    B -->|Listen + Established| C{pollaft sees<br/>C2S/S2C packets?}
    B -->|Missing| D[Restart Gateway SAS service]
    C -->|Yes| E[Proceed inject]
    C -->|No silent bridge| F[Restart Gateway SAS<br/>+ Aurum.Services]
    F --> G{Exception 69?}
    G -->|Yes| H[ClearPendingAft<br/>move aftPendingTransaction XML]
    G -->|No| I[Wait WAT2AFT UP ~30s]
    H --> I
    I --> E
    D --> I
```

---

## 6. Success log chain

```mermaid
flowchart LR
    S1[sasmsgr<br/>qGMID1:0172] --> S2[sasmsgr<br/>80/81 after 0172]
    S2 --> S3[Aurum<br/>TRANSFER REQUEST]
    S3 --> S4[SlotLog<br/>promo 100000]
    S4 --> S5[GM2AU<br/>Withdraw successful]
```

All five within ~500 ms of ingest when credit lands.