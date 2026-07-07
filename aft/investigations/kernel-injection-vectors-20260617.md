# Kernel/Process-Level Credit Injection Vectors - Investigation (2026-06-17)

Systematic probe of kernel and process-level injection points between
GoldClub.Aurum.Services and OneHand.exe on cabinet 10.0.0.90.

Goal: Inject credits without SAS host and without Hop 2 WinDivert, by
targeting the internal traffic between Aurum and OneHand.

---

## 1. Network topology (mapped 2026-06-17)

### Processes

| Process | PID | Role |
|---------|-----|------|
| CommCtrl | 4688 | Serial bridge controller |
| CommCtrlSAS | 4988 | SAS serial<->TCP bridge (31100/31150) |
| GoldClub.Aurum.Services | 3688 | WAT2AFT service (credit decision) |
| GoldClub.Logging.LogDaemon | 2384 | Log writer |
| OneHand | 5264 | Slot/game client (applies credit) |

### Network endpoints

| Endpoint | Binding | Protocol | Purpose |
|----------|---------|----------|---------|
| 169.254.243.18:50010 | LISTENING | .NET remoting HTTP | GM2AU (OneHand cashless gateway) |
| 169.254.243.18:50011 | LISTENING | .NET remoting HTTP | SASControler1 (Aurum HOST interface) |
| 127.0.0.1:31100 | LISTENING | SAS-over-TCP (0x1B framing) | CommCtrlSAS bridge (host side) |
| 127.0.0.1:31150 | LISTENING | SAS-over-TCP (0x1B framing) | CommCtrlSAS bridge (EGM side) |

### Established connections (Aurum <-> OneHand)

169.254.243.18:49689 -> 169.254.243.18:50011  (OneHand -> Aurum HOST)
169.254.243.18:49692 -> 169.254.243.18:50010  (Aurum -> OneHand EGM)
169.254.243.18:49727 -> 169.254.243.18:50011  (OneHand -> Aurum HOST)
169.254.243.18:49739 -> 169.254.243.18:50010  (Aurum -> OneHand EGM)

Key finding: Aurum and OneHand communicate via 2 TCP connections each on
the link-local adapter 169.254.243.18. No named pipes, no shared memory, no
file watchers between them. All IPC is .NET remoting over HTTP.

### Internal IPC channels

| Channel | Status |
|---------|--------|
| Named pipes | None found (no Aurum/GoldClub/OneHand/SAS pipes) |
| Shared memory (Global\) | None found |
| File watchers | AFT XML is write-only; OneHand does not re-read |
| ETW providers | None (only storage driver SAS providers found) |
| .NET remoting | Active on 169.254.243.18:50010 and :50011 |

---

## 2. Candidate vectors (ranked)

### Vector A - WinDivert on 169.254.243.18:50010 (Aurum->OneHand remoting)

Concept: Use WinDivert to intercept the .NET remoting binary frames that
Aurum sends to OneHand GM2AU endpoint when a credit transfer is committed.
Capture the exact binary payload, then replay or forge it.

Why promising:
- The remoting call from Aurum to OneHand is what triggers Cashless In
- If we can capture and replay the binary frame, we bypass the entire WAT2AFT commit chain
- WinDivert works at kernel level - no need for SAS host or COM11

Blockers:
- .NET remoting uses binary serialization with session state
- TCP sequence numbers must be correct
- The remoting call likely includes a transaction ID that OneHand validates
- Need to capture a real transfer first to understand the binary format

Tools written:
- WdRemotingInject.cs - WinDivert capture/replay/inject for remoting traffic
- CaptureRemotingTraffic.cs - passive HTTP probe of remoting endpoints

Status: Tools written, not yet tested on cabinet.

---

### Vector B - .NET remoting binary capture + replay

Concept: Use a custom HTTP client to capture the exact binary remoting
message format by triggering a real transfer and sniffing the traffic. Then
construct a synthetic remoting call that mimics the credit notification.

Why promising:
- .NET remoting binary format is documented (MS-NRBF)
- If the GM2AU endpoint does not validate transaction IDs, a replay could work
- The remoting call is the direct trigger for Cashless In

Blockers:
- Binary serialization includes type metadata that requires Aurum assemblies
- The server may validate the call against the AFT XML state
- Session/channel binding may prevent replay

Status: Analysis only; requires live capture during transfer.

---

### Vector C - OneHand.exe process memory injection

Concept: Read OneHand.exe process memory to find the credit state variable,
then write a new value directly. The credit state is maintained in-memory by
OneHand.AurumEGM.

Why promising:
- Bypasses all protocol layers - direct memory write
- The credit state is a simple integer (cents)
- OpenProcess + WriteProcessMemory is trivial from a local admin context

Blockers:
- .NET objects are GC-managed; the credit state location may move
- OneHand may validate credit state against AFT XML on startup
- Writing memory without understanding the object layout could crash OneHand
- Need Aurum assemblies to understand the object structure

Tools written:
- OneHandMemoryProbe.cs - read-only memory scanner for credit strings/amounts

Status: Probe written, not yet tested.

---

### Vector D - Aurum DLL injection / method hook

Concept: Inject a DLL into GoldClub.Aurum.Services or OneHand.exe and
hook the credit application method to force a credit value.

Why promising:
- Direct access to the credit application logic
- Can bypass validation by hooking at the right point

Blockers:
- Requires writing and injecting a .NET or native DLL
- Anti-tamper measures may be present
- Need to identify the exact method to hook

Status: Not attempted.

---

### Vector E - AFT XML interception + modification

Concept: Intercept the AFT XML write operation and modify the transaction
before OneHand reads it. Use a file system filter driver or directory junction.

Why NOT promising:
- AFT XML is write-only output; OneHand does not re-read it for credit
- The credit is applied from the remoting call, not from the XML file
- Already proven dead-end in hop3-spoof-probe-20260617.md

Status: Dead-end.

---

## 3. Recommended next steps

1. Vector A (WinDivert on 50010): Deploy WdRemotingInject.cs -capture on
   cabinet, trigger a real transfer, capture the remoting binary frame. This is
   the most promising path - it targets the exact traffic that triggers credit.

2. Vector C (Memory probe): Run OneHandMemoryProbe.cs to find the credit
   state location in OneHand.exe memory. If found, a simple memory write could
   inject credits without any protocol manipulation.

3. Vector B (Binary replay): After capturing the remoting frame from step 1,
   analyze the binary format and construct a synthetic call.

---

## 4. Tools

| File | Purpose |
|------|---------|
| WdRemotingInject.cs | WinDivert capture/replay/inject for remoting traffic |
| CaptureRemotingTraffic.cs | Passive HTTP probe of remoting endpoints |
| OneHandMemoryProbe.cs | Read-only memory scanner for credit state |

---

## 5. Dead-ends (confirmed)

| Vector | Why dead |
|--------|----------|
| Named pipes / shared memory | None exist between Aurum and OneHand |
| AFT XML hand-edit | Write-only output; OneHand does not re-read |
| ETW tracing | No Aurum/OneHand providers |
| Generic remoting invoke | Interface hidden behind MarshalByRefObject |
| Hop 3 direct-post | RequestTransferPosted NRE |
| Hop 2 WinDivert (31150) | Requires SAS host polling |

Investigation date: 2026-06-17. Cabinet: 10.0.0.90 (GST20664).