# Dallas iButton Key Injection — Runbook

Simulate a physical Dallas (iButton) key tap on the Goldclub EGM **remotely**, **while the game (`OneHand.exe`) is running**, with **no reboot and no service restart**.

This is achieved by injecting the key's ROM frame directly into the live `CommCtrl → OneHand` TCP connection using a kernel-level packet splicer (WinDivert), then cleanly re-synchronising the connection so neither end notices.

- **Lab cabinet:** `10.0.0.90`
- **Key ROM used:** `01D68A721B000019`
- **Status:** working & repeatable (confirmed: BIOS/service login appears during live gameplay, no reboot)

---

## 1. Quick start (the command that works)

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

### 2.1 How the key normally travels
`CommCtrl.exe` reads the physical iButton reader over a serial COM port and acts as a **TCP server on `127.0.0.1:30800`**. Clients (`OneHand.exe`, `BiOS2.exe`, …) connect to it and receive a stream of tiny ASCII events, each terminated by `\r\n`:

```
700      701      666      01D68A721B000019   ← a key tap (16 hex chars + CRLF = 18 bytes)
```

When a key is physically tapped, CommCtrl **fans the ROM out to every connected client**. Note: `700`, `701`, `766`, `ID 98`, etc. are constant background polls — only the ROM line is unique to a key.

### 2.2 Why simple injection fails
- **Writing to `:30800` ourselves** — CommCtrl does **not** relay client→client traffic. Our bytes die there; OneHand never sees them. (Confirmed twice.)
- **Raw sockets** — Windows blocks sending forged TCP via raw sockets. Dead end in user mode.

To put bytes onto OneHand's *existing* socket as if from CommCtrl, we must work **below the application, inside the OS network stack**. That's WinDivert.

### 2.3 What WinDivert is
`WinDivert` = a small **kernel driver** (`WinDivert64.sys`) + user library (`WinDivert.dll`) that hooks the **Windows Filtering Platform (WFP)**. It lets a normal program:

1. **Divert** packets matching a filter (pull them out of the kernel's normal flow), including loopback `127.0.0.1` traffic.
2. **Modify** the raw bytes (IP/TCP headers + payload).
3. **Re-inject** packets (unchanged, modified, or brand-new ones we craft).

It's a programmable checkpoint in the middle of the network path. We scope the filter to **only the one OneHand connection**, so the other subscribers are never touched.

- Requires **admin/SYSTEM** (we run it via PsExec as SYSTEM) and loads a `.sys` driver.
- The driver **auto-unloads** when our program closes cleanly.
- "Divert" mode is **blocking**: a diverted packet does not continue until we re-inject it, so we must re-inject everything.

### 2.4 The core obstacle: TCP sequence numbers
TCP is a **byte counter** between the two ends. Every byte has a **SeqNum**; each side tracks **AckNum** = "next byte I expect from you." Both ends must always agree on the count, or TCP treats the data as corrupt and kills the connection (RST → OneHand crash → watchdog reboot).

If we inject 18 bytes into OneHand:
1. OneHand now expects the next byte at `S+18`.
2. CommCtrl never sent those 18 bytes; it still thinks it's at `S`.
3. The two ends **disagree by 18** = **desync** → CommCtrl's next real frame looks like garbage to OneHand → RST → reboot.

This is exactly why a naïve "just inject a packet" reboots the cabinet.

### 2.5 The fix, part A — hide the injection (rewrite)
The splicer becomes a transparent rewriter for that one connection, tracking `delta` = bytes injected (18). For **every** subsequent packet it patches the header before re-injecting:

```
server -> client (CommCtrl -> OneHand):   SeqNum += delta
client -> server (OneHand -> CommCtrl):   AckNum -= delta
```

So each end keeps its own consistent view and the insertion is invisible. (This is how a transparent TCP proxy works at the packet level.)

### 2.6 The fix, part B — clean teardown (drain)
While we keep rewriting, all is well — but stopping would re-expose the `+18` mismatch. So before exiting, the splicer **gives the 18 bytes back**:

1. Wait for CommCtrl to send 18 bytes of normal junk poll frames (`700`/`701`/…).
2. **Swallow** them (don't deliver to OneHand) → shrinks the surplus.
3. **Forge an ACK to CommCtrl** on OneHand's behalf so CommCtrl doesn't retransmit.
4. Each swallowed byte drops `delta` by 1. At `delta == 0` both ends genuinely agree again → safe to stop.

Net: OneHand got one extra key frame and lost a few meaningless polls; CommCtrl's books balance; connection ends perfectly synced. **No RST, no reboot.**

### 2.7 End-to-end flow
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

---

## 3. Components (files)

All in `C:\Users\Ezbogar\GoldclubLogInvestigator\`:

| File | Role |
|------|------|
| `Invoke-DallasSpliceRemote.ps1` | **Main orchestrator** (run this). Stages files, resolves the target consumer, runs the splicer via PsExec, prints the log, and cleans up the driver. |
| `DallasSplice.cs` / `DallasSplice.exe` | The WinDivert splicer (x64). Modes: `passthru`, `inject`, `heal`. Does the rewrite + drain. |
| `Invoke-DallasSniffRemote.ps1` | Read-only WinDivert **sniff** (recon / health check). Shows live `:30800` frames with SeqNum/AckNum. |
| `Recover-EgmCabinet.ps1` | Recovery helper (kills zombie Bootstrap, reverts config, optional reboot). |

External tooling (workstation):
- **PsExec** at `C:\Tools\PSTools\PsExec.exe` (Sysinternals).
- **WinDivert 2.2.2 x64** extracted at `C:\Tools\WinDivert\extracted\WinDivert-2.2.2-A\x64\` (provides `WinDivert.dll`, `WinDivert64.sys`, and sample `netdump.exe`).

---

## 4. Modes

| Mode | What it does | Safe to stop anytime? |
|------|--------------|------------------------|
| `passthru` *(default)* | Divert + re-inject **unchanged** (`delta` stays 0). Pre-flight check: confirms the path is healthy and targets OneHand. **Never injects.** | Yes |
| `inject` | Inject the ROM once → hold the rewrite → **drain** to 0 before exit (self-healing clean teardown). | Yes (auto-drains) |
| `heal` | Start from a known offset (`-InitialDelta N`) and drain to 0. Repairs a connection left desynced by an earlier hard-stop. (Rarely needed — Windows loopback self-heals an `+18` offset on its own.) | Yes |

---

## 5. Parameters (`Invoke-DallasSpliceRemote.ps1`)

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

---

## 6. Reading the log

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

---

## 7. Troubleshooting

| Symptom | Cause / Fix |
|---------|-------------|
| **"Login didn't happen", log says `injected=False`** | You ran in `passthru` (the default). Add `-Mode inject`. |
| **`#< CLIXML <Objs …>` blob in output** | Harmless. It's PsExec relaying PowerShell's progress stream. The splicer log below it is what matters. |
| **`powershell.exe exited … error code 1`** | Cosmetic (from the CLIXML/progress stream). If the splicer log shows `DONE … in sync`, it worked. |
| **`WinDivert64.sys … being used by another process` when staging** | The driver was left resident (e.g., a sniff was force-killed). Fix: on the EGM, `sc.exe stop WinDivert` then `sc.exe delete WinDivert`. The orchestrators now do this automatically. |
| **Target is `BiOS2`/`powershell`, not `OneHand`** | OneHand wasn't connected at that moment, or another consumer matched first. Use `-TargetProcess OneHand`, or check with the sniff tool. |
| **Connection looks desynced afterward** | Run `... -Mode heal -InitialDelta 18`. (Usually unnecessary — it self-heals.) Worst case: `Recover-EgmCabinet.ps1`. |
| **No frames / template never captured** | The connection was idle or closed. Confirm OneHand is running and connected to `:30800` (use the sniff tool). |

---

## 8. Safety & recovery

- **Lab cabinet only.** This loads a kernel driver into the EGM. A bad driver load or a desync can, in principle, RST the connection → `Unexpected game stop` → watchdog reboot. The drain prevents this in practice; the worst observed outcome was a self-healing offset (no reboot).
- The WinDivert driver is **transient** — it auto-unloads on clean exit, and the orchestrator force-removes it as a safety net (`sc stop/delete WinDivert`).
- The only files left on the EGM are temp staging in `C:\Windows\Temp\wd` (no config changes, unlike the older BiOS2-MITM route).
- **Recovery:** `Recover-EgmCabinet.ps1` (and reboot if you really need a guaranteed clean slate).
- **Health/state check anytime:** `Invoke-DallasSniffRemote.ps1 -Seconds 8` shows live `:30800` frames; advancing SeqNum/AckNum with no stuck retransmits = healthy.

---

## 9. Cabinet facts (validated)

- `CommCtrl.exe` = TCP server on `127.0.0.1:30800`; reads iButton from serial (COM2–COM8 @ 9600 per `CommControler.ini`); **does not relay client→client**.
- Consumers connect outbound to `:30800`; identify them via `Get-NetTCPConnection -RemotePort 30800 -State Established` → OwningProcess.
- Typical consumers: `OneHand` (the game), `BiOS2` (service UI). The consumer's **ephemeral local port** (e.g. 50218) is the splice target.
- Real key frame on the wire: ASCII `01D68A721B000019` + `\r\n` = **18 bytes**, sent as a `PSH,ACK` segment from `:30800`.
- Background poll frames (`700`, `701`, `766`, `762`, `ID 98`, `655`=eject, `666`, `777`) flow constantly and are safe to drop during drain.
- `Bootstrap.exe` is the watchdog/parent; if `OneHand` terminates unexpectedly it **reboots the machine** — which is why we never kill/restart OneHand.

---

## 10. Rebuilding the splicer (if `DallasSplice.cs` changes)

Compile on the workstation with the .NET Framework C# compiler (x64):

```powershell
& 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe' /platform:x64 /nologo /optimize+ `
  /out:'C:\Users\Ezbogar\GoldclubLogInvestigator\DallasSplice.exe' `
  'C:\Users\Ezbogar\GoldclubLogInvestigator\DallasSplice.cs'
```

`DallasSplice.exe` needs `WinDivert.dll` + `WinDivert64.sys` beside it at runtime (the orchestrator stages all three to the EGM automatically).

---

## 11. One-time setup on a fresh workstation

1. Install PsExec to `C:\Tools\PSTools\`.
2. Download WinDivert 2.2.2 and extract:
   ```powershell
   Invoke-WebRequest 'https://github.com/basil00/WinDivert/releases/download/v2.2.2/WinDivert-2.2.2-A.zip' -OutFile 'C:\Tools\WinDivert\WinDivert-2.2.2-A.zip'
   Expand-Archive 'C:\Tools\WinDivert\WinDivert-2.2.2-A.zip' 'C:\Tools\WinDivert\extracted'
   ```
3. Ensure admin rights to the EGM's `c$` share and PsExec.
4. Compile `DallasSplice.exe` (section 10) if not already built.
5. Run the quick-start command (section 1).
