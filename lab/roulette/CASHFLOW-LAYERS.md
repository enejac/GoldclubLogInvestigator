# Roulette cashflow — all communication layers

Lab reference cabinet: **10.0.0.90** (`GST20664` / `GCC_RT_330106_01`, asset **777**).

This document maps **every cash-related path** on the roulette image the same way the
slot stack is diagrammed under `aft/diagrams/igt-onehand-layered-flow.*`.

**Latest session (2026-07-27):** bill physical + inject proven — see
[`aft/diagrams/igt-ruleta-bill-flow-report.html`](../../aft/diagrams/igt-ruleta-bill-flow-report.html).

| Slot (OneHand) | Roulette (ruleta) |
|----------------|-------------------|
| `aft/diagrams/igt-onehand-layered-flow.svg` | [`aft/diagrams/igt-ruleta-layered-flow.md`](../../aft/diagrams/igt-ruleta-layered-flow.md) |
| `aft/diagrams/igt-onehand-visio-shapes.txt` | [`aft/diagrams/igt-ruleta-visio-shapes.txt`](../../aft/diagrams/igt-ruleta-visio-shapes.txt) |
| AFT bridge **:31150** | AFT bridge **:30550** (WakeUpPort) |
| Consumer **OneHand.exe** | Consumer **ruleta.exe** |
| Dallas **:30800** | Dallas / KeyCtrl / bill **:30300** |
| Logs: SlotLog | Logs: `ruleta\`, sasmsgr, HWSubsys, GM2AU |

---

## Port map (CommCtrl gateway)

Physical UART **`COM#` → loopback TCP `30000 + COM#`** (`CommCtrl.exe` listens).

| COM | TCP | Hardware / role | Primary consumer |
|-----|-----|-----------------|------------------|
| COM3 | **30300** | Bill acceptor + KeyCtrl keyboard + Dallas | **ruleta.exe** |
| COM4 | **30400** | TITO / ticket printer (FutureLogic) | **HWSubsys** → ruleta |
| COM5 | **30500** | SAS MUX serial (CommCtrlSAS) | **CommCtrlSAS.exe** |
| COM5 MUX | **30550** | SAS WakeUpPort (loopback leg) | **GoldClub.Aurum.Services** |
| COM6 | **30600** | Security switch / doors | **HWSubsys** SecuritySwitch |
| COM7 | **30700** | LED / light tower | **HWSubsys** Light |
| — | **25071** | HWSubsys internal aggregator | **HWSubsys** clients |
| — | **8090** | Ruleta EmbedIO middleware (HTTP) | **Godot GUI** via nginx |

**Do not edit** `serialport/layout.json` or `locations.json` on live cabinets.

---

## Cash paths at a glance

```
                         ┌─────────────────────────────────────────────────────────┐
                         │                    ruleta.exe (core)                     │
                         │  credits · bets · handpay · collect · SAS meter mirror  │
                         └────────▲───────────────▲───────────────▲────────────────┘
                                  │               │               │
          ┌───────────────────────┘               │               └──────────────────┐
          │ Bill / KeyCtrl / Dallas               │ AFT cashless                      │ TITO / ticket
          │ ASCII on :30300                       │ 0x1B SAS on :30550                │ :30400
┌─────────┴─────────┐                 ┌───────────┴──────────┐            ┌──────────┴─────────┐
│ COM3 bill validator│                 │ COM5 @921600 MUX      │            │ COM4 ticket printer │
│ + iButton keyboard │                 │ CommCtrlSAS bridge    │            │ FutureLogic driver  │
└───────────────────┘                 └───────────┬──────────┘            └────────────────────┘
                                                    │
                                         IGT SAS tester (optional)
                                         or WdPollInject pollaft
```

| # | Path | Money in/out | Inject / sniff tool |
|---|------|--------------|---------------------|
| 1 | **Bill in** | Physical note → credit | **Proven 2026-07-27** · inject: **`Invoke-BillInjectRouletteRemote.ps1 -Credits 200000 -Send`** · report: [`igt-ruleta-bill-flow-report.html`](../../aft/diagrams/igt-ruleta-bill-flow-report.html) |
| 2 | **AFT cashless in** | Host → EGM credit | **Proven 2026-07-27** organic tester ($2k non-restricted, `$72` Status 00) + **2026-07-23** pollaft; `Invoke-WinDivertAftRoulette.ps1 -Send` |
| 3 | **TITO voucher in** | Ticket barcode → credit | HW path `:30400`; no inject tool yet |
| 4 | **Collect / cash out** | Credit → ticket / handpay / hopper | Godot `PUT /api/action Collect` (wire codes 241–402) |
| 5 | **Dallas admin key** | Handpay clear / service menu | `Invoke-DallasSpliceRouletteRemote.ps1` |
| 6 | **SAS meter readback** | Accounting only | SAS verify GUI / `sas_serial_meters.py` |

Parallel **non-cash** bus: Godot ↔ `:8090` HTTP (bets, spin, UI) — see
[`docs/action-endpoint-commands.md`](../../docs/action-endpoint-commands.md).

---

## Layer stack (matches slot L0–L6 model)

| Layer | Slot (OneHand) | Roulette (ruleta) |
|-------|------------------|-------------------|
| **L0 Physical** | COM11 SAS @921600 | COM3 bill, COM4 ticket, COM5 SAS MUX, COM6 switch, COM7 lights |
| **L1 Bridge framing** | CommCtrlSAS `0x1B` + SAS | CommCtrlSAS `0x1B` + SAS **or** CommCtrl ASCII `\r\n` lines |
| **L2 TCP loopback** | :31100 / :31150 | :30300 :30400 :30550 :30600 :30700 :8090 |
| **L3 Services** | Aurum WAT2AFT | Aurum WAT2AFT + **HWSubsys** drivers |
| **L4 Game core** | OneHand GM2AU | **ruleta.exe** + Godot frontend |
| **L5 Persistence** | AFT XML on disk | AFT XML + `gm2au\` note files + DeviceManagerData |
| **L6 Logs / verify** | SlotLog, sasmsgr | `ruleta\`, sasmsgr, HWSubsys, SlotLog (shared Aurum) |

WinDivert inject layers (AFT only): [`aft/no-physical-polls-layers.md`](../../aft/no-physical-polls-layers.md).

---

## Per-path documentation

| Path | Detail doc |
|------|------------|
| AFT cashless | [`aft/hops/README.md`](../../aft/hops/README.md) + [`lab/roulette/README.md`](README.md) § AFT |
| Bill acceptor | [`BILL-ACCEPTOR-PROTOCOL.md`](BILL-ACCEPTOR-PROTOCOL.md) |
| Dallas / KeyCtrl | [`lab/DALLAS-INJECTION-RUNBOOK.md`](../../lab/DALLAS-INJECTION-RUNBOOK.md) |
| Godot middleware | [`docs/action-endpoint-commands.md`](../../docs/action-endpoint-commands.md) |
| Full layered diagrams | [`aft/diagrams/igt-ruleta-layered-flow.md`](../../aft/diagrams/igt-ruleta-layered-flow.md) |
| Visio shape text | [`aft/diagrams/igt-ruleta-visio-shapes.txt`](../../aft/diagrams/igt-ruleta-visio-shapes.txt) |

---

## Sniff cheat sheet (.90)

```powershell
# Bill + KeyCtrl + Dallas (:30300)
.\lab\roulette\Invoke-DallasSpliceRouletteRemote.ps1 -Mode capture -RunSeconds 120

# AFT SAS bridge (:30550 + optional :31100 style legs)
.\lab\Invoke-Stage0SasSniff.ps1 -ComputerName 10.0.0.90 -Ports 30550,30500 -Seconds 60 `
  -WinDivertDir 'C:\Tools\WinDivert\x64'

# TITO + switch + lights + HW aggregator
.\lab\Invoke-HwSniffRemote.ps1 -ComputerName 10.0.0.90 -Ports 30400,30600,30700,25071 -Seconds 60 `
  -WinDivertDir 'C:\Tools\WinDivert\x64'

# Godot ↔ ruleta HTTP
.\lab\roulette\Invoke-RuletaGuiSniff.ps1 -IP 10.0.0.90 -Seconds 60
```

Log tails: `\\10.0.0.90\c$\Goldclub\var\log\ruleta\`, `HWSubsys\`, `GoldClub.Aurum.Services sasmsgr of SASControler1\`.
