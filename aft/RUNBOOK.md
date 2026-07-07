# AFT Promo-Credit Injection via WinDivert — Runbook

Trigger a **$1,000 promo AFT (cashless) credit** on the lab cabinet by injecting a raw SAS `0x72` "transfer funds" command into the live `CommCtrlSAS -> Aurum` loopback SAS stream — **without using the IGT SAS tester UI** to send the transfer. WinDivert replaces **only the `0x72` AFT frame**; a physical SAS host must still be connected and polling so the session is online.

This is achieved by using WinDivert to inject a single TCP segment, carrying the `0x1B`-framed SAS `0x72` command, **into the existing** `CommCtrlSAS:31150 -> Aurum:<ephemeral>` flow at the correct sequence number, so Aurum reads it as the next in-order bytes on its established SAS session and commits the transfer.

- **Lab cabinet:** `10.0.0.90` (host `GST20664`)
- **EGM:** `GCC_ST_20664_01`, asset `777`
- **Registration:** `GAMING_MACHINE_NOT_REGISTERED` (registration key = 20 zero bytes). Transfers still commit unregistered — WAT authorization, not SAS AFT registration, is the active gate.
- **Status:** working when a SAS host is connected and polling (confirmed 2026-06-17 on `.90`; see §6 and [`README.md`](README.md)).

## Prerequisites (check before every inject)

1. **SAS tester or SAS host connected** on COM11 / MUX upstream channel.
2. Steady **`qGMID1:80/81`** in `GoldClub.Aurum.Services sasmsgr of SASControler1` log
   (or Stage 0 sniff shows `1B80`/`1B81` on `31150`).
3. **No** `NO OWNED DEVICE FOUND FOR WAT` in WAT2AFT log.
4. Lab SMB access: `.\Initialize-LabAccess.ps1 -Verify` (credential `GOLD-CLUB\test`).

Without (1)–(2), expect **ingest without credit** — not a tool failure. Full investigation context: [`README.md`](README.md).

---

## 1. Quick start (the commands that work)

From the repo folder `C:\Users\Ezbogar\GoldclubLogInvestigator`:

```powershell
# Simplest — thin wrapper (DryRun is the default; -Send actually injects):
.\Send-TestAft1000.ps1 -Send

# Or drive the injector directly (auto-picks the next transaction number):
.\Invoke-WinDivertAft.ps1 -Send

# Target a different cabinet by IP (-IP is an alias of -ComputerName; default 10.0.0.90):
.\Invoke-WinDivertAft.ps1 -Send -IP 10.0.0.110
.\Send-TestAft1000.ps1 -Send -IP 10.0.0.110

# Custom amount (raw credits / base units, NOT dollars) into the non-restricted (promo) field:
.\Send-TestAft1000.ps1 -Send -IP 10.0.0.110 -Amount 1000000 -nr

# Route the same amount into the cashable field instead (-c), or restricted (-r):
.\Invoke-WinDivertAft.ps1 -Send -IP 10.0.0.110 -Amount 1000000 -c
```

### Amount and transfer-type parameters

- **`-Amount <int>`** — a raw integer amount in the EGM's **base units ("credits", not dollars)**, placed directly into the selected SAS `0x72` amount field (same unit as the legacy `-AmountCents`). Must be `1..9999999999` (fits the 5-byte BCD field). Takes **precedence** over `-AmountCents` when both are given. Omitting both keeps the default of `100000` ( = `$1,000.00` if 1 unit = 1 cent), i.e. behavior is unchanged when `-Amount` is omitted.
- **`-AmountCents <int>`** — legacy name, still supported (default `100000`). Identical unit to `-Amount`.
- **Transfer type — `-c` / `-r` / `-nr`** — choose which of the three SAS `0x72` amount fields carries the amount:
  - `-nr` -> **non-restricted (promo)** — **the default** and the verified working injection field.
  - `-c`  -> cashable
  - `-r`  -> restricted
  Exactly one may be specified; supplying more than one throws `Specify only one transfer type (-c, -r, or -nr)`. Omitting all three is identical to `-nr`. The other two amount fields are always sent as BCD `0000000000`. The packet's transfer-type byte stays `0x00` for every selection; only the non-zero BCD amount field changes (the verified promo run used `0x00`).

> **Most common mistake:** running with no `-Send`. Both scripts default to **DryRun**, which prints the crafted packet and the command that *would* run but **injects nothing** (no staging, no driver, no credit). You **must** pass `-Send` to fire the transfer.
>
> **Targeting another cabinet:** the cabinet defaults to `10.0.0.90`. Pass `-IP <addr>` (alias of `-ComputerName`) to point at a different machine, e.g. `-IP 10.0.0.110`. The prerequisites (admin C$ reach, etc.) must hold for that target.
>
> **Transaction numbers:** leave `-TransactionNumber` unset for normal use. The script keeps a local per-IP counter and picks the next two-digit transaction id automatically. Reusing an old transaction id can produce `sasmsgr` ingest with **no new cabinet credit**, because the EGM/Aurum treats it as a duplicate/replay.

**Success looks like this** (from the injector and the cabinet logs):

```
OBSERVED dport=56616 seq=4203484091 ack=66203552 ipHdr=20 tcpHdr=20 origPayloadLen=2
ANCHOR mode=payload
INJECTED seq=4203484093 dport=56616 len=75 sent=115
CLOSE ok injected=True
EXITCODE=0
...
[+] sasmsgr INGESTED the injected 0x72 command:
INGESTED : YES
CREDITED : evidence found
```

A post-injection TCP RST / reconnect of the SAS link is **expected and harmless** — the transfer has already been delivered and processed by the time the link re-syncs.

---

## 2. Prerequisites

- **PsExec** (Sysinternals) at `C:\Tools\PSTools\PsExec.exe`. The injector runs the remote payload as `SYSTEM` (`-s`) so WinDivert can load its kernel driver.
- **WinDivert 2.2.2 x64** extracted at `C:\Tools\WinDivert\extracted\WinDivert-2.2.2-A\x64\` (provides `WinDivert.dll` and `WinDivert64.sys`).
- **Admin reach to the cabinet C$ share** (`\\10.0.0.90\c$`) to stage files and read logs.
- The cabinet must have a working .NET Framework C# compiler (`csc.exe`) — used to compile `WdInject.cs` on the cabinet once per run cache.

---

## 3. Architecture (why injection works here)

### 3.1 Who owns what

`CommCtrlSAS.exe` bridges the physical RS-232 SAS link (`COM11`) onto loopback TCP and **owns/listens on `127.0.0.1:31100` and `127.0.0.1:31150`**. `GoldClub.Aurum.Services` (the "sasmsgr of SASControler1", i.e. the EGM-side SAS messenger) connects to those ports **as the CLIENT, from an ephemeral local port**, and is the EGM-side endpoint that decodes every SAS frame into a `qGMID1:<hex>` log line.

| Port | Owner | Role |
|---|---|---|
| `31150` | `CommCtrlSAS.exe` | Host-side poll/command flow: `31150 -> Aurum:<ephemeral>` |
| `31100` | `CommCtrlSAS.exe` | EGM-side ack/response flow: `<ephemeral> -> 31100` |

### 3.2 Direction and framing

- **Host -> Aurum** SAS commands ride the existing flow `CommCtrlSAS:31150 -> Aurum:<ephemeral>`, and are **`0x1B`-framed**: each SAS frame is prefixed with one `0x1B` bridge byte (e.g. `1B80`, `1B81` general polls, and the AFT status poll `1B 01 72 02 FF 00 0F 22`). The SAS frame proper begins at the byte after `0x1B`.
- **Aurum -> CommCtrl** responses ride `<ephemeral> -> 31100` and are **NOT** `0x1B`-framed (e.g. the `01 72 4A 1E ...Transaction.. ` AFT status RESPONSE).

Live steady-state traffic (read-only from the sasmsgr log) is the alternating `81`/`80` general poll:

```text
2026-06-15T08:13:19.104+01:00 INFO [:] qGMID1:81
2026-06-15T08:13:19.105+01:00 INFO [:] qGMID1:80
```

TCP-level view of the bridge (WinDivert capture, `\\10.0.0.90\c$\Windows\Temp\aurumtap\dump.txt`):

```text
TCP [SrcPort=31150 DstPort=56616 ...]   payload 1B80   <- SAS poll 0x80 over bridge (host->Aurum)
TCP [SrcPort=31150 DstPort=56616 ...]   payload 1B81   <- SAS poll 0x81 over bridge (host->Aurum)
TCP [SrcPort=56615 DstPort=31100 ...]   payload 00     <- EGM idle/ack byte back over bridge (Aurum->host)
```

A real AFT transfer, once decoded by the messenger, appears as a `qGMID1:0172…` line:

```text
2026-06-15T13:14:12.609+01:00 INFO [:] qGMID1:017245000000000000000000000000000000100000000903000000000000000000000000000000000000000000001200657374205472616E73616374696F6E3232053020200C00000A45
```

### 3.3 The injection point

To make Aurum execute a transfer we inject **one TCP segment into the EXISTING `31150 -> ephemeral` flow** (the server->client direction owned by CommCtrlSAS), with:

- **payload** = `0x1B` + the raw SAS `0x72` transfer-funds command,
- **SEQ** = `origSeq + origPayloadLen` (placed right after the most recent real segment, so it is the next in-order byte for Aurum),
- recomputed IP/TCP checksums.

The ephemeral destination port is **discovered live** from the first observed `31150` data packet — it is never hardcoded.

---

## 4. The SAS `0x72` packet (host -> EGM AFT transfer funds, $1000 promo, asset 777)

The injected bytes are `0x1B` + the following SAS frame. Field shape:

| Field | Value |
|---|---|
| Address | `0x01` |
| Command | `0x72` (AFT transfer funds) |
| Length | `0x45` |
| Transfer code | `0x00` (in-house amount, host -> gaming machine) |
| Transaction index | `0x00` |
| Transfer type | `0x00` |
| Cashable amount | BCD `0000000000` ($0.00) |
| Restricted amount | BCD `0000000000` ($0.00) |
| **Non-restricted (promo) amount** | **BCD `0000100000` = $1,000.00** |
| Transfer flags | `0x00` |
| Asset number | little-endian `09 03 00 00` (= 777) |
| Registration key | 20 × `0x00` (machine NOT registered) |
| Transaction id | length-prefixed ASCII (e.g. `…est Transaction41`) |
| Expiration | BCD |
| Pool id | `0x000C` |
| Receipt data length | `0x00` |
| Lock / timeout | per frame |
| CRC | CRC-16/KERMIT (poly `0x1021`, reflected `0x8408`), sent little-endian |

Amounts are **BCD in cents**: `0000100000` = 100000 cents = `$1,000.00`. The full byte-level decode (including the `0x72`/`0x45` transfer and the `0172 02 FF 00 0F22` status interrogate) lives in `protocol-raw-traffic.md` §1–§4 — cite that file for the authoritative byte layout. The builder lives in `Invoke-WinDivertAft.ps1` (`New-AftTransferPacket` + `Get-SasCrc16`).

---

## 5. The injection algorithm

`WdInject.cs` (compiled to `WdInject.exe` on the cabinet) does the work:

1. Open WinDivert in divert mode with filter
   `outbound and loopback and ip and tcp and tcp.SrcPort == 31150 and tcp.Ack == 1 and tcp.Syn == 0 and tcp.Rst == 0 and tcp.Fin == 0`.
   This captures **both** payload-bearing segments **and** bare ACKs (established segments are also `Ack==1`), while excluding TCP control packets (SYN/RST/FIN).
2. **Forward every captured packet unchanged**, so normal SAS polling keeps flowing and the link stays in sync.
3. Anchor the TCP sequence number using whichever segment we see first:
   - **Payload anchor (preferred, proven).** The first segment **with** payload yields `SEQ = origSeq + origPayloadLen` (placed right after the forwarded data). Logged as `ANCHOR mode=payload`.
   - **ACK anchor (fallback for an idle-but-established link).** When the physical SAS link (`COM11`) is missing, `CommCtrlSAS` emits no payload on `31150`, but the connection is still `ESTABLISHED` and sends bare ACKs whose `seq == SND.NXT`. For a pure ACK `origPayloadLen == 0`, so `SEQ = seq` is the correct injection point. WdInject records the latest pure-ACK seq, waits `AckGraceMs` (default 600 ms) preferring a payload segment if one arrives in that window, and otherwise anchors on the ACK. Logged as `ANCHOR mode=ack graceMs=<n>`.
4. Build a new segment = original 40-byte header + our `0x1B + SAS 0x72` payload, set `SEQ` to the anchored value, force `PSH|ACK`, zero then recompute IP/TCP checksums via `WinDivertHelperCalcChecksums`, and `WinDivertSend` it into the same flow.
5. Keep forwarding for ~1.5 s so the stack advances and Aurum can ACK/process, then `WinDivertClose` (driver auto-unloads).

### Sequence-number anchoring knobs

- **`-ObserveMs <ms>`** (default `8000`) — how long WdInject waits for **any** outbound segment on `31150` before giving up. If the connection is **fully silent** (no payload *and* no ACK at all) the run still times out cleanly — ACK-anchoring cannot help a connection that emits zero outbound segments.
- **`-AckGraceMs <ms>`** (default `600`) — the grace window for ACK-anchoring. After the first bare ACK is seen, WdInject waits this long for a payload segment (which it prefers) before anchoring on the latest pure-ACK seq. The grace window always fits inside `-ObserveMs`. **`-AckGraceMs 0` disables ACK-anchoring entirely** (legacy payload-only behavior: WdInject ignores ACKs and waits for a payload segment).

Both knobs are exposed on `Invoke-WinDivertAft.ps1` and forwarded through the thin `Send-TestAft1000.ps1` wrapper.

> **Idle-link caveat:** an ACK-anchored injection into an idle connection is more likely to be followed by a TCP RST / reconnect than a payload-anchored one, because the real sender's `SND.NXT` was not advanced by any preceding data segment. As with the payload path, the post-injection RST/reconnect is expected and harmless — the transfer is already delivered and processed before the link re-syncs.

`Invoke-WinDivertAft.ps1` orchestrates: it builds the SAS `0x72` + `0x1B` frame locally, then stages to a **content-hashed cache folder** `\\10.0.0.90\c$\Windows\Temp\aurumtap\bin-<sha12-of-WdInject.cs>`. On a **cache miss** it copies `WinDivert.dll` + `WinDivert64.sys` + `WdInject.cs` and compiles `WdInject.exe` once with `csc`; on a **cache hit** it copies/compiles nothing and runs the cached exe directly. It runs `WdInject.exe <payloadHex> 31150 <ObserveMs> <AckGraceMs>` (default `31150 8000 600`) via PsExec (`-accepteula -s`), then leaves the WinDivert service registered as demand-start (no per-run `sc delete`). Because the folder name is keyed to the `WdInject.cs` hash, any source change forces a one-time recompile into a new `bin-*` folder, so the cache can never go stale.

**Speed:** the first run for a given `WdInject.cs` pays the compile; subsequent runs skip staging + compile. On lab fleet IPs with WinRM up, inject transport is typically **~2–5s (WinRM)** vs **~45s (PsExec)**. Ingest/credit verification reads only the **tail** of the cabinet logs.

### SEQ-race retry

Because the injector must land its segment immediately after a real `31150` data packet (and the live link keeps moving), a given attempt can lose the SEQ race: the TCP write completes but Aurum's stack does not accept it as in-order, so no `qGMID1:` line appears. `Invoke-WinDivertAft.ps1` retries up to **3 times** (`-MaxRetries`), re-discovering the live ephemeral port each attempt, until the sasmsgr log records the full packet.

If the target has no live `CommCtrlSAS:31150 -> Aurum:<ephemeral>` traffic at all, the injector times out cleanly instead of waiting forever:

```text
TIMEOUT no data packet seen on srcPort 31150 within 8000ms
CLOSE ok injected=False
```

Because the injector now **also anchors on bare ACKs** (not just payload segments), a timeout means the connection emitted **no outbound segments whatsoever** during the observe window — neither payload nor a single ACK — i.e. it is truly silent (not merely idle-but-established). If the link is established but idle, a periodic ACK is normally enough for the ACK-anchor fallback to inject; a hard timeout points instead at: OneHand/Aurum/CommCtrlSAS not running yet, no established `31150 -> Aurum` connection, a different cabinet role/build, the SAS bridge using different ports, or the wrong target IP. Restore SAS/`COM11` traffic on the cabinet, or raise `-ObserveMs`, then retry. Check the remote bridge state before retrying:

```powershell
Get-NetTCPConnection -LocalPort 31100,31150
```

If staging fails with:

```text
Copy-Item : The process cannot access the file '\\<ip>\c$\Windows\Temp\aurumtap\WinDivert.dll'
because it is being used by another process.
```

you are using an older script version or a stale run left a staging folder locked. Current `Invoke-WinDivertAft.ps1` stages into a content-hashed `aurumtap\bin-<hash>` folder and, on a cache hit, does **not** re-copy `WinDivert.dll` at all (so it cannot hit the copy-lock).

If the injector output contains:

```text
REMOTE_EXCEPTION: WinDivertOpen FAILED GetLastError=1058
```

check the cabinet driver service:

```powershell
sc.exe query WinDivert
sc.exe qc WinDivert
```

Error `1058` is Windows `ERROR_SERVICE_DISABLED`. On `10.0.0.171` this was verified as `STATE: STOP_PENDING`, `START_TYPE: DISABLED`, `BINARY_PATH_NAME: \??\C:\Windows\Temp\aurumtap\WinDivert64.sys`, and `sc config` / `sc delete` both failed with `1072` (`marked for deletion`). That state cannot be repaired from user mode; reboot the cabinet to clear the stuck kernel-driver service, then rerun. The current script attempts a pre-open repair for a disabled-but-not-stuck service and fast-fails on `1058` instead of wasting all retry attempts.

#### Why it wedged, and how the script now prevents it

WinDivert installs the `WinDivert` kernel-driver service on `WinDivertOpen()` and unloads the driver when the **last handle closes**. A kernel driver can only stop once its reference count hits zero; Windows will **never force-unload a referenced driver** (doing so risks a bugcheck), so a leaked handle leaves it in `STOP_PENDING`. The fatal step was the old per-run cleanup issuing **`sc stop` + `sc delete`** unconditionally: deleting a service whose driver is still referenced marks it for deletion (`1072`), and that registration is only cleared on reboot — the wedge.

Two changes remove the trigger:

- **`WdInject.cs` always releases the handle.** A single idempotent `CloseOnce()` (`WinDivertShutdown` + `WinDivertClose`) runs from the `finally`, from `Console.CancelKeyPress` (Ctrl-C), and from `AppDomain.ProcessExit`. The driver unloads cleanly on every graceful exit.
- **The per-run remote cleanup no longer `sc stop`/`sc delete`s WinDivert.** The service is left **registered as demand-start**: idle when unused, instantly reusable next run. Even a hard-killed/orphaned run now just leaves a *reusable* loaded driver — never a deleted-but-referenced wedge.

To actually remove the driver, use the explicit safe teardown, which deletes **only** when the service is genuinely `STOPPED` (and refuses while `STOP_PENDING`, so it can never create the wedge):

```powershell
.\Invoke-WinDivertAft.ps1 -IP 10.0.0.90 -RemoveDriver
# or: .\Send-TestAft1000.ps1 -IP 10.0.0.90 -RemoveDriver
```

The only remaining way to reach the stuck state is `TerminateProcess` on `WdInject.exe` mid-run (uncatchable) immediately followed by a manual `sc delete`; the script no longer does either, so a reboot-clear should not recur in normal use.

### Transport (WinRM vs PsExec)

The remote payload (compile `WdInject.cs`, run `WdInject.exe`, clean up the driver) can be delivered to the cabinet over two transports:

- **PsExec (default, proven).** Runs the payload as `SYSTEM` (`-s`). This is the original, reliable path, and it is also the **main latency cost** per run (~20-30s per PsExec call to the cabinet).
- **WinRM (preferred once available).** `Invoke-Command -ComputerName <ip> -Authentication Negotiate` against port `5985`. **Requires credentials** for NTLM-by-IP: the lab cabinet has no usable Kerberos SPN, so the client IP must be in `TrustedHosts` (added automatically when possible) **and** an explicit account must be supplied. **Lab fleet IPs** (`10.0.0.83`, `.90`, `.100`, `.110`, `.112`, `.171`) auto-use `GOLD-CLUB\test` from `LabAccess.ps1` — no `-Credential` needed. Override with `-Credential (Get-Credential)` if required. Without any credential (non-lab IP), WinRM is skipped and PsExec is used.

The injector **prefers WinRM first whenever it is reachable** and enables it **asynchronously** so no single run pays the enable cost:

- At the start of each `-Send` run it does a fast bounded TCP probe of port `5985` (no `Test-NetConnection`, so it never hangs ~30s). **If reachable, WinRM is used first this run** (PsExec fallback), and the preference is persisted per IP in `.aft-windivert-transport.json`.
- **If not reachable**, the run completes over the proven PsExec path and, *after* the injection finishes, fires a **detached, fire-and-forget** PsExec that runs `Enable-PSRemoting -Force -SkipNetworkProfileCheck` on the cabinet (`Start-Process` + PsExec `-d`, so it never blocks and survives the script exiting). The **next** run's probe finds `5985` open and switches to WinRM automatically.

`-SkipNetworkProfileCheck` is required because the lab cabinets run on a **Public** network profile; plain `Enable-PSRemoting` / `Set-WSManQuickConfig` fails there with `WSManFault 2150859113`. The skip switch installs a WinRM firewall rule scoped to the local subnet, which is sufficient because the host and cabinet share `10.0.0.x`.

The async enable is launched **after** the inject (not before) to avoid two PsExec sessions fighting over the cabinet's `PSEXESVC` service during the critical injection.

WinDivert still loads its kernel driver on the cabinet under whichever remote session runs the payload. PsExec-as-SYSTEM carries a sufficient token; a WinRM admin network logon normally does too. As a safety net, if a WinRM run produces `inject_out.txt` **without** an `OPEN ok` line (or containing `WinDivertOpen FAILED`), the driver could not load under that session, so the injector **demotes** WinRM for that host: it records `driverFailed` in `.aft-windivert-transport.json` (shape `{ "10.0.0.90": { "winrm": true, "driverFailed": false } }`) and stays on PsExec for this and all future runs. Delete that file to re-test WinRM. All transport helpers are bounded and best-effort and never abort a run.

`-DryRun` does **not** probe, enable, or stage anything.

---

## 6. Verification (which logs prove success)

A run is only "successful" when the **full per-transaction packet hex** appears in the sasmsgr log (the txn id + CRC are unique per run, so this is an exact match, compared in UTC), followed by the downstream credit lines. The injector checks all four automatically:

1. **sasmsgr ingest** — `…\GoldClub.Aurum.Services sasmsgr of SASControler1\<date>.log`, full-hex `qGMID1:0172…` match.
2. **SlotLog** — `Cashless In: $1,000.00` and `Aurum promo credit state increased to 100000`.
3. **OneHand TRANSACTION EVENTS** — `Transfer IN $1,000.00(promo:0)…`.
4. **OneHand GM2AU** — `Withdraw successful GCC_ST_20664_01 … 100000(promo:0)…`.

### Proven evidence — Run B (txn 41), 2026-06-15 15:00:30 cabinet time

Two independent successful live injections with SAS host connected: **Run A** (txn 23) at 14:43:11 and **Run B** (txn 41) at 15:00:30 cabinet time. Run B end-to-end:

WdInject output:

```text
OBSERVED dport=56616 seq=4203484091 ack=66203552 ipHdr=20 tcpHdr=20 origPayloadLen=2
INJECTED seq=4203484093 dport=56616 len=75 sent=115
CLOSE ok injected=True
EXITCODE=0
```

sasmsgr ingest (contains "Transaction41" = `5472616E73616374696F6E3431`, marker `00001000000009030000`, CRC `C56B`):

```text
2026-06-15T15:00:30.520+01:00 INFO [:] qGMID1:017245000000000000000000000000000000100000000903000000000000000000000000000000000000000000001200657374205472616E73616374696F6E3431053020200C0000C56B
```

Credit posted, within ~230 ms of ingest:

```text
2026-06-15T15:00:30.722+01:00 INFO  [GM2AU_aurumExecute] OneHand.AurumEGM - Cashless In: $1,000.00
2026-06-15T15:00:30.722+01:00 INFO  [GM2AU_aurumExecute] OneHand.AurumEGM - Aurum promo credit state increased to 100000
2026-06-15T15:00:30.750+01:00 INFO [:18554352] Transfer IN $1,000.00(promo:0); $0.00(cashable:0); $0.00(cashable:0)
2026-06-15T15:00:30.748+01:00 INFO [:18554352] Withdraw successful GCC_ST_20664_01 in 100000(promo:0); 0(cashable:0); 0(cashable:0)
```

The ingest -> credit chain completes in **~230 ms**.

---

## 7. What does NOT work, and why

These approaches were tried and are dead-ends. Do not retry them; they are recorded here so nobody does.

1. **Raw TCP socket inject** (open a new `TcpClient` to `31150` and write the framed `0x72`). The write succeeds at the TCP layer, but `CommCtrlSAS` never merges that separate connection into the live serial-backed SAS session, so Aurum never decodes it — **no `qGMID1:` line, no transfer**. The bridge faithfully relays only its one serial-backed session; an extra TCP client is not the polled peer. The fix is to inject into the **existing** flow with the correct SEQ via WinDivert — which is exactly what this runbook does.
2. **.NET-remoting WAT `requestTransfer` (direct-post to the Aurum service, Hop 3).** Posting `WAT.requestTransfer` directly to `http://GST20664:50011/SASControler1` reaches the engine, but `GoldClub.Aurum.WATmanager.RequestTransferPosted` expects an already-existing Aurum transaction and throws a `NullReferenceException` when minting a wholly new transaction id. It does not complete a fresh transfer.
3. **Legacy `setBonusAward` jackpot path** (GM2AU bonus). Host-rejected with `TRANSACTIONID SEQUENCE NOT ALLOWED TO USE ON HOST SIDE`. Not a usable promo-credit path.
4. **Generic .NET remoting invoke on `:50010` / `:50011` (2026-06-17 probe).** Untyped clients get `MarshalByRefObject` proxies only — no WAT/credit methods visible. GM2AU binds `169.254.243.18:50010`, not `localhost`. See `investigations/hop3-spoof-probe-20260617.md`.
5. **AFT XML state hand-edit (2026-06-17 probe).** `aftMostRecentTransaction_v1.xml` is WAT2AFT write-only output; editing it does not make OneHand emit `Cashless In`. Real state is in memory / binary `.dat` files.
6. **Direct `authorizeTransfer` / `commitTransfer` without typed client.** Not reachable on generic remoting proxy; does not bypass the need for a live WAT transaction context.

The working decouple point is therefore **Hop 2 (the CommCtrlSAS -> Aurum bridge), via in-stream WinDivert injection** — not a new socket, and not Hop 3 remoting or XML spoofing.

---

## 8. Cleanup & safety

- **DryRun is safe.** With no `-Send`, nothing is staged, no driver loads, and no credit is posted.
- The WinDivert driver service is **stopped and deleted after every run** (`sc.exe stop WinDivert` + `sc.exe delete WinDivert`), even on error, so no driver is left resident.
- A **SAS link TCP RST / reconnect after injection is expected and harmless** — the transfer is already delivered/processed before the link re-syncs.
- **Lab cabinet only.** This loads a kernel driver into the EGM and injects into a live link. Use only on `10.0.0.90` with authorization.
- The only files left on the cabinet are cached staging folders under `C:\Windows\Temp\aurumtap\bin-<hash>` (`WinDivert.dll`, `WinDivert64.sys`, `WdInject.cs/.exe`, `inject_out.txt`), intentionally retained to speed up later runs. No config changes. (Older versions left `run-*` folders; those are safe to delete.)

---

## 9. Components (files)

All in `C:\Users\Ezbogar\GoldclubLogInvestigator\`:

| File | Role |
|---|---|
| `Invoke-WinDivertAft.ps1` | **Main orchestrator.** Builds the SAS `0x72` + `0x1B` frame, stages WinDivert + `WdInject.cs`, remote-compiles and runs `WdInject.exe` via PsExec as SYSTEM, removes the driver, then verifies via the cabinet logs. ParameterSets: `DryRun` (default, injects nothing) and `Send`. Params: `-ComputerName` (10.0.0.90, alias `-IP`), `-Amount` (raw credits/base units, precedence over `-AmountCents`), `-AmountCents` (100000), transfer type `-c`/`-r`/`-nr` (default `-nr` non-restricted/promo), `-AssetNumber` (777), optional `-TransactionNumber` (auto-generated when omitted/0, and auto-skips ids already seen in today's sasmsgr log), `-Credential` (cabinet admin, enables the faster WinRM transport), `-BridgePort` (31150), `-ObserveMs` (8000), `-AckGraceMs` (600; 0 disables ACK-anchoring), `-MaxRetries` (3). |
| `WdInject.cs` | The WinDivert P/Invoke injector (x64). Diverts the `31150` outbound loopback flow (now capturing ACK segments too), forwards every packet, and anchors the inject SEQ on the first **payload** segment (`SEQ+payloadLen`, preferred) or, on an idle-but-established link, on a bare **ACK** seq after a short grace window (`-AckGraceMs`). Recomputes checksums, keeps forwarding ~1.5 s, closes. CLI args: `<payloadHex> [srcPort=31150] [observeMs=8000] [ackGraceMs=600]`. |
| `Send-TestAft1000.ps1` | Thin convenience wrapper around `Invoke-WinDivertAft.ps1` (DryRun default; `-Send` to inject). |
| `Invoke-AurumTrafficCapture.ps1` | Read-only WinDivert netdump capture / recon tool. How the bridge flow and ports were discovered. |
| `protocol-raw-traffic.md` | Byte-level `0x72`/`0x45` transfer + status-poll decode reference (in this `aft/` folder). |

Read-only AFT audit tooling (unchanged by this method, kept for verification): `Convert-AftHistory.ps1`, `Parse-AftHistory.ps1`, `Invoke-AftTransferTest.ps1` (tester-mimic read-only verifier), and the generated `aft/report/` audit.

---

## 10. One-time setup on a fresh workstation

1. Install PsExec to `C:\Tools\PSTools\`.
2. Download WinDivert 2.2.2 and extract:
   ```powershell
   Invoke-WebRequest 'https://github.com/basil00/WinDivert/releases/download/v2.2.2/WinDivert-2.2.2-A.zip' -OutFile 'C:\Tools\WinDivert\WinDivert-2.2.2-A.zip'
   Expand-Archive 'C:\Tools\WinDivert\WinDivert-2.2.2-A.zip' 'C:\Tools\WinDivert\extracted'
   ```
3. Ensure admin rights to the EGM's `c$` share.
4. Run the quick-start command (§1) — `WdInject.cs` is compiled on the cabinet automatically on first `-Send`.
