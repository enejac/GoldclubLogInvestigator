# Dallas iButton Key Injection — Runbook

Simulate a physical Dallas (iButton) key tap on the Goldclub EGM **remotely**, with **no reboot and no service restart**, by injecting the key's ROM frame into a live `CommCtrl → consumer` TCP connection using a WinDivert packet splicer, then draining so the connection stays in sync.

Two validated paths exist on the same lab cabinet. **Do not mix tools or ports.**

| | **Slot** | **Roulette** |
|---|----------|--------------|
| Consumer | `OneHand` / `BiOS` | `ruleta` (keyboard / KeyCtrl / ReadDallas) |
| CommCtrl port | `:30800` | `:30300` |
| Orchestrator | `lab\Invoke-DallasSpliceRemote.ps1` | `lab\roulette\Invoke-DallasSpliceRouletteRemote.ps1` |
| Splicer | `probes\DallasSplice.exe` | `probes\DallasSpliceRoulette.exe` |
| Default mode | `passthru` (must pass `-Mode inject`) | `inject` |
| Transport | PsExec (SYSTEM) | Fast WinRM (~3 s remote after inject; drain can finish in background) |
| Success signal | BIOS/service login during gameplay | ruleta log: `KEY = admin, CODE = 01, DALLAS = …, EVENT = IN` |

- **Lab cabinet:** `10.0.0.90`
- **Key ROM (both):** `01D68A721B000019` (ASCII + `\r\n` = 18 bytes on the wire)
- **Status:** both paths working & repeatable
- **Do not** stop Serial Communication Gateway (breaks HW / Dallas on roulette)

Short roulette pointer: `lab\roulette\README.md`.

---

## Slot vs Roulette — quick start

### Roulette (preferred one-liner)

From repo root (`GoldclubLogInvestigator`):

```powershell
.\Invoke-DallasSpliceRouletteRemote.ps1 -ComputerName 10.0.0.90
# same script: .\lab\roulette\Invoke-DallasSpliceRouletteRemote.ps1 -ComputerName 10.0.0.90
```

Defaults (robust): `Mode=inject`, `Action=roundtrip`, `ServerPort=30300`,
`Rom=700|||701|||01D68A721B000019`. Ejects then inserts, waits for drain +
`KEY=admin` IN. Active guest handpay is cleared by that same admin insert
(no manual cancel). Hard-gates only on keyboard disconnect. Use `-Fast` only
for the old insert-only background-drain path.

**Success:** `UiReady=True` (not merely `Injected=True`). ruleta log shows
`KEY = admin, CODE = 01, DALLAS = 01D68A721B000019, EVENT = IN` and handpay
unlocked (`HANDPAY CANCEL` / `unlock of type lmt_handpay_processing`).

### Slot

From `lab\` (or with full path):

```powershell
.\Invoke-DallasSpliceRemote.ps1 -ComputerName 10.0.0.90 -Mode inject -RunSeconds 25 -InjectAfterMs 4000 -DrainReserveSec 8
```

> **Most common slot mistake:** omitting `-Mode inject` — default is `passthru` (`injected=False`). Roulette defaults to `inject`, so the one-liner above is enough.

---

## 1. Slot quick start (the command that works)

From the workstation, in `C:\Tools\PSTools` (or anywhere, using the full path):

```powershell
.\Invoke-DallasSpliceRemote.ps1 -ComputerName 10.0.0.90 -Mode inject -RunSeconds 25 -InjectAfterMs 4000 -DrainReserveSec 8
```

The key lands ~4 seconds in. **Success looks like this in the log:**

```
INJECTED ROM: clientSeq=... bytes=18 delta=18
DRAIN phase begin (delta=18)
DRAIN complete -> connection back in sync (drained=18)
DONE. ... injected=True drained=18 finalDelta=0
Connection left in sync (delta=0).
```

> **Most common mistake:** running the script with **no `-Mode`** argument. It defaults to `passthru`, which validates the path but **never injects** (`injected=False`). You must pass `-Mode inject`.

---

## 2. How it works (the concepts)

Shared splice/drain mechanics apply to both slot and roulette. The difference is **which CommCtrl port and which consumer** you target.

### 2.1 How the key normally travels

**Slot:** `CommCtrl.exe` reads the physical iButton reader over a serial COM port and acts as a **TCP server on `127.0.0.1:30800`**. Clients (`OneHand.exe`, `BiOS2.exe`, …) connect and receive ASCII events terminated by `\r\n`:

```
700      701      666      01D68A721B000019   ← a key tap (16 hex chars + CRLF = 18 bytes)
```

When a key is physically tapped, CommCtrl **fans the ROM out to every connected client**. Note: `700`, `701`, `766`, `ID 98`, etc. are constant background polls — only the ROM line is unique to a key.

**Roulette:** the same ROM line is delivered on **`127.0.0.1:30300`** to **`ruleta`** (keyboard / KeyCtrl / ReadDallas). During game, `:30800` often has **no** client → slot splice returns `NO_TARGET`. Use the roulette tools and `:30300` instead.

### 2.2 Why simple injection fails
- **Writing to the CommCtrl port ourselves** — CommCtrl does **not** relay client→client traffic. Our bytes die there; the consumer never sees them.
- **Raw sockets** — Windows blocks sending forged TCP via raw sockets. Dead end in user mode.

To put bytes onto the consumer's *existing* socket as if from CommCtrl, we must work **below the application, inside the OS network stack**. That's WinDivert.

### 2.3 What WinDivert is
`WinDivert` = a small **kernel driver** (`WinDivert64.sys`) + user library (`WinDivert.dll`) that hooks the **Windows Filtering Platform (WFP)**. It lets a normal program:

1. **Divert** packets matching a filter (pull them out of the kernel's normal flow), including loopback `127.0.0.1` traffic.
2. **Modify** the raw bytes (IP/TCP headers + payload).
3. **Re-inject** packets (unchanged, modified, or brand-new ones we craft).

It's a programmable checkpoint in the middle of the network path. We scope the filter to **only the one consumer connection**, so other subscribers are never touched.

- Requires **admin/SYSTEM** and loads a `.sys` driver.
- The driver **auto-unloads** when our program closes cleanly.
- "Divert" mode is **blocking**: a diverted packet does not continue until we re-inject it, so we must re-inject everything.

### 2.4 The core obstacle: TCP sequence numbers
TCP is a **byte counter** between the two ends. Every byte has a **SeqNum**; each side tracks **AckNum** = "next byte I expect from you." Both ends must always agree on the count, or TCP treats the data as corrupt and kills the connection (RST → consumer crash / watchdog reboot).

If we inject 18 bytes into the consumer:
1. The consumer now expects the next byte at `S+18`.
2. CommCtrl never sent those 18 bytes; it still thinks it's at `S`.
3. The two ends **disagree by 18** = **desync** → CommCtrl's next real frame looks like garbage → RST.

This is exactly why a naïve "just inject a packet" reboots the cabinet.

### 2.5 The fix, part A — hide the injection (rewrite)
The splicer becomes a transparent rewriter for that one connection, tracking `delta` = bytes injected (18). For **every** subsequent packet it patches the header before re-injecting:

```
server -> client (CommCtrl -> consumer):   SeqNum += delta
client -> server (consumer -> CommCtrl):   AckNum -= delta
```

So each end keeps its own consistent view and the insertion is invisible.

### 2.6 The fix, part B — clean teardown (drain)
While we keep rewriting, all is well — but stopping would re-expose the `+18` mismatch. So before exiting, the splicer **gives the 18 bytes back**:

1. Wait for CommCtrl to send 18 bytes of normal junk poll frames.
2. **Swallow** them (don't deliver to the consumer) → shrinks the surplus.
3. **Forge an ACK to CommCtrl** on the consumer's behalf so CommCtrl doesn't retransmit.
4. Each swallowed byte drops `delta` by 1. At `delta == 0` both ends genuinely agree again → safe to stop.

Net: the consumer got one extra key frame and lost a few meaningless polls; CommCtrl's books balance; connection ends synced. **No RST, no reboot.**

### 2.7 End-to-end flow (slot)

```
Workstation                         EGM (10.0.0.90)
-----------                         ----------------
Invoke-DallasSpliceRemote.ps1
  │ stage WinDivert.dll + .sys + DallasSplice.exe to C:\Windows\Temp\wd
  │ PsExec (SYSTEM) ───────────►    resolve OneHand's TCP port (e.g. 50218)
                                    DallasSplice.exe loads WinDivert kernel driver
                                      │ filter scopes to 30800 <-> 50218 only
                                      │ ① forward packets normally (delta=0)
                                      │ ② inject 01D68A721B000019\r\n  → delta=18
                                      │ ③ rewrite seq/ack on all packets (hide it)
                                      │ ④ drain 18 bytes + forge ACKs   → delta=0
                                      │ WinDivertClose() → driver auto-unloads
                                    OneHand reacts to a "key tap" mid-game; nothing reboots
```

### 2.8 End-to-end flow (roulette)

```
Workstation                         EGM (10.0.0.90)
-----------                         ----------------
Invoke-DallasSpliceRouletteRemote.ps1
  │ stage WinDivert + DallasSpliceRoulette.exe to C:\Windows\Temp\wd_roulette
  │ WinRM (admin) ──────────────►   resolve ruleta's TCP port on :30300
                                    DallasSpliceRoulette.exe + WinDivert
                                      │ filter scopes to 30300 <-> ephemeral only
                                      │ inject same ROM line → delta=18 → drain
                                    Fast path: return ~3s after inject; drain may finish in background
                                    ruleta logs KEY/DALLAS EVENT=IN
```

Do **not** stop Serial Communication Gateway. Avoid the stub `:30800` path (`Invoke-SendDallasKeyRouletteRemote.ps1`) — it failed / is risky.

---

## 3. Components (files)

All in `C:\Users\Ezbogar\GoldclubLogInvestigator\`:

### Slot

| File | Role |
|------|------|
| `lab\Invoke-DallasSpliceRemote.ps1` | **Main slot orchestrator**. Stages files, resolves the target consumer, runs the splicer via PsExec, prints the log, and cleans up the driver. |
| `probes\DallasSplice.cs` / `DallasSplice.exe` | Slot WinDivert splicer (x64). Modes: `passthru`, `inject`, `heal`. |
| `lab\Invoke-DallasSniffRemote.ps1` | Read-only WinDivert **sniff** (recon / health check). Shows live `:30800` frames. |
| `lab\Recover-EgmCabinet.ps1` | Recovery helper (kills zombie Bootstrap, reverts config, optional reboot). |

### Roulette

| File | Role |
|------|------|
| `lab\roulette\Invoke-DallasSpliceRouletteRemote.ps1` | **Main roulette orchestrator** (fast WinRM). Defaults to `inject` on `:30300`. |
| `probes\DallasSpliceRoulette.cs` / `DallasSpliceRoulette.exe` | Roulette WinDivert splicer (x64). Same rewrite + drain idea; different default port/target. |
| `lab\roulette\README.md` | Short roulette-specific pointer. |

External tooling (workstation):
- **PsExec** at `C:\Tools\PSTools\PsExec.exe` (Sysinternals) — used by **slot** path.
- **WinRM / LabAccess** — used by **roulette** path (`LabAccess.ps1`, TrustedHosts, `GOLD-CLUB\test`).
- **WinDivert 2.2.2 x64** (provides `WinDivert.dll`, `WinDivert64.sys`). Slot and roulette scripts may point at slightly different extract folders; ensure the DLL+sys pair exists where each script's `-WinDivertDir` expects.

---

## 4. Modes

| Mode | What it does | Safe to stop anytime? |
|------|--------------|------------------------|
| `passthru` | Divert + re-inject **unchanged** (`delta` stays 0). Pre-flight check. **Never injects.** Slot **default**. | Yes |
| `inject` | Inject the ROM once → hold the rewrite → **drain** to 0 before exit. Roulette **default**. | Yes (auto-drains; roulette may return early while drain finishes in background) |
| `heal` | Start from a known offset (`-InitialDelta N`) and drain to 0. Repairs a connection left desynced by an earlier hard-stop. | Yes |
| `capture` | Roulette-only recon mode (see roulette script). | Yes |

---

## 5. Parameters

### Slot — `Invoke-DallasSpliceRemote.ps1`

| Parameter | Default | Notes |
|-----------|---------|-------|
| `-ComputerName` | `10.0.0.90` | EGM host. |
| `-Mode` | `passthru` | `passthru` \| `inject` \| `heal`. **Use `inject` to fire a key.** |
| `-RunSeconds` | `20` | Total window. Use ~25 for inject so drain has room. |
| `-InjectAfterMs` | `2500` | Delay before injecting (4000 gives time to watch the screen). |
| `-DrainReserveSec` | `6` | Seconds before the end to begin the drain. Use ~8 for inject. |
| `-InitialDelta` | `0` | `heal` mode only: the offset to drain away. |
| `-Rom` | `01D68A721B000019` | The 16-hex-char key ROM to inject. |
| `-TargetProcess` | *(auto)* | Force a consumer process. Auto order: `OneHand` → `BiOS2` → any. |
| `-PsExecPath` | `C:\Tools\PSTools\PsExec.exe` | |
| `-WinDivertDir` | `...\WinDivert-2.2.2-A\x64` | Folder with `WinDivert.dll` + `WinDivert64.sys`. |
| `-ExePath` | `...\DallasSplice.exe` | The compiled splicer. |

### Roulette — `Invoke-DallasSpliceRouletteRemote.ps1`

| Parameter | Default | Notes |
|-----------|---------|-------|
| `-ComputerName` | `10.0.0.90` | EGM host. |
| `-Mode` | `inject` | One-liner needs no extra flags. |
| `-ServerPort` | `30300` | CommCtrl keyboard/Dallas port for ruleta. **Not** `30800`. |
| `-InjectAfterMs` | `0` | Immediate inject. |
| `-Rom` | `01D68A721B000019` | Same ROM line as slot. |
| `-TargetProcess` | `ruleta` | |
| `-RunSeconds` / `-DrainReserveSec` | `55` / `54` | Full window when waiting for drain (`-WaitForDrain`). |
| `-HitTimeoutSec` | `12` | How long to watch for inject / ruleta log hit on the fast path. |
| `-WaitForDrain` | off | If set, block until drain completes instead of returning after inject. |

---

## 6. Reading the log

### Slot splicer

```
S2C template seq=... ack=... payLen=5      ← learned the server->client stream
C2S template seq=... win=...               ← learned the client->server stream
INJECTED ROM: clientSeq=... bytes=18 delta=18    ← the key was inserted
DRAIN phase begin (delta=18)               ← starting clean teardown
DRAIN complete -> connection back in sync  ← surplus fully returned
DONE. s2c=.. c2s=.. injected=True drained=18 finalDelta=0
Connection left in sync (delta=0).         ← safe exit, no desync
```

- `injected=True` + `finalDelta=0` = full success.
- `injected=False` = you were in `passthru` mode.

### Roulette

Splicer lines are the same idea (`INJECTED ROM`, drain / sync). Additionally watch **ruleta** logs under `C:\goldclub\var\log\ruleta Roulette\` for:

```
KEY = admin, CODE = 01, DALLAS = 01D68A721B000019, EVENT = IN
```

Fast WinRM path may report inject success before drain finishes; optional `-WaitForDrain` waits for full sync.

---

## 7. Troubleshooting

| Symptom | Cause / Fix |
|---------|-------------|
| **"Login didn't happen", log says `injected=False`** (slot) | You ran in `passthru` (the default). Add `-Mode inject`. |
| **Roulette `NO_TARGET`** | `ruleta` not connected to `:30300`. Confirm the game is up and using the keyboard/Dallas path. |
| **Roulette `Injected=True` but `DallasHit=False`** | Bytes hit TCP but ruleta did not accept a key. Default payload is `700\|\|\|701\|\|\|01D68A721B000019`. ROM-only often fails after desync. Check ruleta for `Unknown code 1` / `keyboard disconnected`. Default orchestrator waits for drain; avoid `-Fast` stacking. |
| **`DallasHit=True` but no service UI** | Default path roundtrips eject→insert and waits for handpay unlock after admin IN (`UiReady`). Guest handpay is cleared by the inject; only keyboard-down hard-blocks. If `HANDPAY_LOCK_AFTER_HIT`, re-run once after drain. |
| **ruleta `Unknown code 1 from keyboard`** | Garbled ASCII on `:30300` — almost always a second inject while the previous WinDivert drain was still active, or ROM without `700`/`701` context. Clear WinDivert / wait ~`RunSeconds`, then retry with the default `-Rom`. |
| **Used slot tools during roulette / `NO_TARGET` on `:30800`** | Wrong port. Roulette consumers are on `:30300` — use `Invoke-DallasSpliceRouletteRemote.ps1`. |
| **Stopped Serial Communication Gateway** | Breaks HW / Dallas. Do not stop it for inject. |
| **`#< CLIXML <Objs …>` blob in output** (slot/PsExec) | Harmless. It's PsExec relaying PowerShell's progress stream. The splicer log below it is what matters. |
| **`powershell.exe exited … error code 1`** (slot) | Cosmetic if the splicer log shows `DONE … in sync`. |
| **`WinDivert64.sys … being used by another process` when staging** | Driver left resident. On the EGM: `sc.exe stop WinDivert` then `sc.exe delete WinDivert`. Orchestrators try this automatically. |
| **Target is `BiOS2`/`powershell`, not `OneHand`** (slot) | Use `-TargetProcess OneHand`, or check with the sniff tool. |
| **Connection looks desynced afterward** | Slot: `-Mode heal -InitialDelta 18`. Roulette: same idea with `-ServerPort 30300`. Worst case: `Recover-EgmCabinet.ps1` / restart ruleta via game-start. |
| **No frames / template never captured** | Connection idle or closed. Slot: sniff `:30800`. Roulette: confirm `:30300` ↔ `ruleta`. |

---

## 8. Safety & recovery

- **Lab cabinet only.** This loads a kernel driver into the EGM. A bad driver load or a desync can, in principle, RST the connection → unexpected game stop → watchdog reboot. The drain prevents this in practice.
- The WinDivert driver is **transient** — it auto-unloads on clean exit, and orchestrators force-remove it as a safety net where implemented.
- Staging dirs on the EGM: slot `C:\Windows\Temp\wd`, roulette `C:\Windows\Temp\wd_roulette` (no config changes).
- **Recovery:** `Recover-EgmCabinet.ps1` (and reboot if you need a guaranteed clean slate).
- **Health/state check (slot):** `Invoke-DallasSniffRemote.ps1 -Seconds 8` shows live `:30800` frames.

---

## 9. Cabinet facts (validated)

### Slot (`:30800`)

- `CommCtrl.exe` = TCP server on `127.0.0.1:30800`; reads iButton from serial (COM2–COM8 @ 9600 per `CommControler.ini`); **does not relay client→client**.
- Consumers connect outbound to `:30800`; identify them via `Get-NetTCPConnection -RemotePort 30800 -State Established` → OwningProcess.
- Typical consumers: `OneHand` (the game), `BiOS2` (service UI). The consumer's **ephemeral local port** is the splice target.
- Real key frame on the wire: ASCII `01D68A721B000019` + `\r\n` = **18 bytes**, sent as a `PSH,ACK` segment from `:30800`.
- Background poll frames (`700`, `701`, `766`, `762`, `ID 98`, `655`=eject, `666`, `777`) flow constantly and are safe to drop during drain.
- `Bootstrap.exe` is the watchdog/parent; if `OneHand` terminates unexpectedly it **reboots the machine** — which is why we never kill/restart OneHand.

### Roulette (`:30300`)

- Same ROM payload as slot: ASCII `01D68A721B000019\r\n`.
- Consumer: `ruleta` on CommCtrl **`:30300`** (keyboard / KeyCtrl / ReadDallas) — **not** `:30800`.
- During roulette gameplay, `:30800` may have no client → slot splice `NO_TARGET`.
- Do **not** stop Serial Communication Gateway.
- Validated success (10.0.0.90): ruleta log `KEY = admin, CODE = 01, DALLAS = 01D68A721B000019, EVENT = IN`.

---

## 10. Rebuilding the splicers (if `.cs` changes)

Compile on the workstation with the .NET Framework C# compiler (x64).

**Slot** (do not change unless intentionally updating the slot tool):

```powershell
& 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe' /platform:x64 /nologo /optimize+ `
  /out:'C:\Users\Ezbogar\GoldclubLogInvestigator\probes\DallasSplice.exe' `
  'C:\Users\Ezbogar\GoldclubLogInvestigator\probes\DallasSplice.cs'
```

**Roulette:**

```powershell
& 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe' /platform:x64 /nologo /optimize+ `
  /out:'C:\Users\Ezbogar\GoldclubLogInvestigator\probes\DallasSpliceRoulette.exe' `
  'C:\Users\Ezbogar\GoldclubLogInvestigator\probes\DallasSpliceRoulette.cs'
```

Each exe needs `WinDivert.dll` + `WinDivert64.sys` beside it at runtime (orchestrators stage all three to the EGM automatically).

---

## 11. One-time setup on a fresh workstation

1. Install PsExec to `C:\Tools\PSTools\` (slot path).
2. Download WinDivert 2.2.2 and extract so DLL+sys are available to both scripts' `-WinDivertDir` defaults.
3. Lab access: `.\Initialize-LabAccess.ps1 -Verify` (cmdkey + TrustedHosts for WinRM; roulette path).
4. Compile splicers (section 10) if not already built.
5. Run the quick-start command for the product you need (Slot vs Roulette section above).
