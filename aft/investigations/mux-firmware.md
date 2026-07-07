# MUX firmware parity: 10.0.0.90 vs 10.0.0.171

Read-only investigation, 2026-06-17. Goal: explain the `CheckForMux` identity string delta, whether it is real MUX firmware or a CommCtrlSAS artifact, what update/route options exist on the cabinets, and ranked remediation to restore organic `80`/`81` host polls on COM11 for `.171` (`GST19737`).

**Cabinets:** `.90` (`GST20664`, working AFT / steady general polls) vs `.171` (`GST19737`, bridge up, zero serial-side polls).

---

## Executive summary

| Question | Answer |
|----------|--------|
| Is the SI-1.0.3 vs 2.0.1 delta real MUX firmware? | **Yes — device-reported identity.** Same `CommCtrlSAS.exe` on both cabinets; firmware version text is **not** embedded in the binary. |
| Can `.171` match `.90` by PC config only? | **No evidence.** `CommControler.ini` is byte-identical; negotiation is identical (`CH 2`, `SA 1`); no MUX flash/route utilities found under `C:\Goldclub` on either host. |
| Can software/firmware on the PC fix polls without hardware? | **Unlikely with what is on disk.** No `.bin`/`.hex`, no `*mux*` tools, no `CommConfig.exe`. Reflash would require **external** IGT/vendor tooling and an SI-1.0.3 image not present on either cabinet. |
| Most probable poll gap | **Upstream of CommCtrlSAS:** external SAS **host/poller** on the MUX non-PC channel (documented on `.90` in `poll-source.md`). Different MUX hardware (`206139555241` vs `2063374E4741`) and firmware generation may correlate with lab wiring, but **PC stack parity does not restore polls.** |

**Plain recommendation:** Treat `.171` as missing the same **physical host path** as `.90` until proven otherwise. **Next action:** side-by-side **MUX wiring / upstream host** audit (and optional **swap of the `.90` USB MUX** onto `.171` as a one-shot test). Do **not** flash MUX firmware until an official image + procedure exist and the user authorizes it.

---

## 1. PnP / driver (both cabinets, PsExec SYSTEM)

| Field | 10.0.0.90 | 10.0.0.171 |
|-------|-----------|------------|
| Friendly name | `MUX/SAS (COM:11)` | `MUX/SAS (COM:11)` |
| USB identity | `USB\VID_0483&PID_5740\206139555241` | `USB\VID_0483&PID_5740\2063374E4741` |
| Status | OK | OK |
| Driver | `usbser.inf`, Microsoft **10.0.17763.1** (USB Serial Device) | Same |
| Firmware in PnP properties | **None exposed** (standard STM32 VCP stack) | Same |

Both hosts also retain **stale enum instances** for other `VID_0483&PID_5740` serials (lab history of multiple MUX boards on COM10–COM14). The **active** COM11 instance differs by USB serial → **two physical MUX units**, not one image difference on the same device.

---

## 2. CommCtrlSAS binary parity (rules out “different exe prints different text”)

| | 10.0.0.90 | 10.0.0.171 |
|---|-----------|------------|
| Path | `C:\Goldclub\services\CommCtrlSAS\CommCtrlSAS.exe` | Same |
| SHA256 | `A480531EA81067FDE2A42BA2D8E2192E19712510457906B191716178E06A51A0` | **Identical** |
| FileVersion | 1.2.5.4 | 1.2.5.4 |
| `CommControler.ini` SHA256 | `28EF12FCCC936391105CBAFE65003596C6212F1117D69F89449C3B636653A3AF` | **Identical** (`<11> <921600>`) |
| Service folder | `CommCtrlSAS.exe`, `XYNTService.exe`, install scripts only — no MUX tools | Same file set |

ASCII scan of `CommCtrlSAS.exe`: contains `CheckForMux` / `MULTIPLEXER`; does **not** contain `SI-1.0.3`, `2.0.1`, `4004145`, or `SERIAL INTERFACE`. The banner is read from the MUX over the serial protocol after open @ 921600.

---

## 3. CommCtrlSAS MUX negotiation (log comparison)

### Working `.90` (example `2026-06-10.log`)

```text
serial port \\.\COM11 open on baudrate 921600
CCommConnection::CheckForMux Detected: VE MULTIPLEXER 4004145 SERIAL INTERFACE 2CH SI-1.0.3
CH 2
SA 1
Listening on port 31150 / 31151
Connection 3110000003 established on port 31100
Connection 3115000004 established on port 31150
```

### Broken `.171` (example `2026-06-16.log`, repeat after full stack restart `22:46`)

```text
serial port \\.\COM11 open on baudrate 921600
CCommConnection::CheckForMux Detected: VE MULTIPLEXER 4004145 SI 2CH - 2.0.1
CH 2
SA 1
Listening on port 31150 / 31151
Connection 3110000001 established on port 31100
Connection 3115000002 established on port 31150
```

**Observations**

- Only durable difference in the negotiation block is the **MUX identity string** (older SI reports `SERIAL INTERFACE 2CH SI-1.0.3`; newer reports `SI 2CH - 2.0.1`).
- **Channel and station:** both select **`CH 2`** and **`SA 1`** with the same timing pattern (~500 ms steps).
- **No** log lines on either cabinet for DIP, CTS/DSR, explicit host/slave routing, or channel switch commands beyond `CH 2`.
- **Poll bytes** (`1B80`/`1B81`) do **not** appear in CommCtrlSAS text logs on either host (poll proof on `.90` is from loopback capture / Aurum `sasmsgr`, per `poll-source.md`).
- **Errors:** both show benign TCP `GetOverlappedResult error: 64` on bridge reconnect; `.171` shows **no** MUX-specific serial fault after successful `CheckForMux`.

---

## 4. Install-tree search: MUX firmware / update utilities

Searched on **both** `.90` and `.171` (SMB + targeted PsExec):

- `C:\Goldclub\services`, `slot`, `tools`, `bin`, `AurumConfigurer`, `HardwareSetup` — **no** files matching `*mux*`, `*multiplex*`, `*4004145*`, `*SI*2CH*` as names; **no** `.bin`/`.hex` blobs tied to MUX/STM32/5740.
- `C:\Tools`, `C:\Program Files*`, `C:\IGT` (depth-limited): **no** MUX updater found.
- Present on **both**, identical sizes: `services\aurum\Setup\SASSetup.exe`, `services\aurum\bin\lib\AuProgsWAPservicesTester.exe` — **not** running in production; **no** MUX/4004145 strings in `SASSetup.exe` ASCII.
- **No** `CommConfig.exe` on either cabinet (only a historical comment in `CommControler.ini`).

**Conclusion:** Cabinets do not ship a documented on-box path to reflash or reconfigure the VE MUX. Any flash would use **off-box** IGT/STM32 tooling and a firmware file not stored under `Goldclub`.

---

## 5. HardwareSetup / AurumConfigurer / CommCtrl logs

| Source | 10.0.0.90 | 10.0.0.171 |
|--------|-----------|------------|
| `var\log\HardwareSetup` | Present; **no** MUX/COM11/5740 hits in recent logs | **Folder absent** |
| `var\log\AurumConfigurer` | **No** MUX hits in recent logs | (not fully scanned; no `HardwareSetup` analogue) |
| `var\log\CommCtrl` | **No** MUX hits | **No** MUX hits |

MUX bring-up is entirely visible in **CommCtrlSAS** only.

---

## 6. Repo cross-references

| Document | MUX-relevant point |
|----------|-------------------|
| `poll-source.md` | Polls are **external hardware** on COM11 upstream of CommCtrlSAS; `.171` lacks that source. |
| `config-parity.md` | SAS XML/INI parity; notes firmware delta as remaining differentiator. |
| `com11-emulator-plan.md` | Option A = physical host on MUX; Option D = TCP inject (does not fix organic serial polls). |
| `hops\hop1-igt-tester-to-commctrlsas.md` | Hop1 = IGT tester ↔ COM11 @ 921600. |

---

## 7. (a) Real firmware vs CommCtrlSAS artifact

**Real MUX firmware / identity revision.**

Evidence: identical `CommCtrlSAS.exe` hash; version strings absent from binary; two different USB serial numbers; stable per-cabinet strings across many log days (`SI-1.0.3` always on `.90`, `2.0.1` always on `.171`).

The wording change (`SERIAL INTERFACE 2CH SI-1.0.3` vs `SI 2CH - 2.0.1`) is consistent with a **product firmware generation change**, not a logging quirk.

---

## 8. (b) Tools / files to update or route the MUX

| Mechanism | On-cabinet status |
|-----------|-------------------|
| INI / CommCtrlSAS config | **Only** COM port + baud — no CH/host route knob. |
| CommCtrlSAS runtime | Auto `CheckForMux` → reports `CH 2` / `SA 1` on both; no user-facing route API in logs. |
| Firmware image | **Not found** on `.90` or `.171`. |
| Flash/update utility | **Not found** under `Goldclub` or common lab paths. |
| `SASSetup.exe` / tester EXEs | Present but **not** MUX flash tools. |

**Channel routing:** Software consistently lands on **CH 2**. There is **no** documented PC-side switch to CH 1 found in config or logs. If 2.0.1 hardware defaults differ internally, remediation is likely **DIP/jumper/wiring** per IGT MUX manual or **firmware downgrade**, not an INI edit.

---

## 9. (c) Proposed remediation (ranked by risk)

### Tier 1 — Config / service (lowest risk) — **nothing to apply**

- `CommControler.ini` already matched; full stack restart on `.171` already reproduces good MUX negotiation without polls.
- **Do not** apply mystery INI changes without IGT documentation.
- **Proposed but NOT executed (needs user GO):** none identified as safe and evidence-backed.

### Tier 2 — Physical / routing (low–medium risk) — **preferred**

1. **Wiring audit:** Compare `.90` vs `.171` MUX **host vs EGM** ports (2CH upstream downstream). Confirm the same **external SAS host / IGT tester** that feeds `.90` is cabled into the MUX channel that forwards to the PC VCP.
2. **Swap test:** Move the `.90` USB MUX (`…555241`) to `.171` (or swap cables only). Restart CommCtrlSAS service.
   - If identity becomes `SI-1.0.3` **and** polls appear → MUX hardware/firmware + wiring on `.171` unit suspect.
   - If identity changes but **still no polls** → missing upstream host, not MUX firmware alone.

### Tier 3 — Firmware flash (high risk) — **only with vendor package + explicit GO**

1. Obtain **SI-1.0.3** (or IGT-approved) image and official updater for `4004145` / STM32 `VID_0483&PID_5740`.
2. Backup current identity string and capture `CheckForMux` line; flash per vendor procedure (may require boot/DFU mode, service downtime).
3. Re-verify `CH 2` / `SA 1` and poll stream.

**Not recommended** without IGT docs: guessing STM32 DFU with unknown `.bin`.

### Tier 4 — Hardware rewire / replace (highest structural risk)

- Permanent replacement of `.171` MUX with `.90`-generation hardware.
- Re-pin lab harness to match known-good `.90` topology.

### Alternative (does not achieve “organic COM11 polls”)

- TCP loopback poll inject (`com11-emulator-plan.md` Option D) — Aurum-only path; **not** parity with `.90` serial host behavior.

---

## 10. (d) Success evidence

Confirm in this order:

1. **CommCtrlSAS** after service start: `CheckForMux` (firmware string optional if polls work), `CH 2`, `SA 1`, bridge ESTABLISHED on `31100`/`31150`.
2. **Loopback sniff** (same method as `../captures/stage0-90-steady-*`): repeating **HOST >> `1B81` / `1B80`** at ~200 ms cadence on `31150`→Aurum direction.
3. **Aurum** `GoldClub.Aurum.Services sasmsgr of SASControler1\*.log`: slave responses / `qGMID` activity tied to general polls.

Optional: serial tap **upstream of the MUX** (hardware) showing raw `80`/`81` entering the host channel — proves external poller independent of TCP inject.

---

## 11. Can `.171` reach parity with `.90` via software/firmware only?

**Partial / conditional.**

- **Software-only on the PC:** **No** — configs and binaries already match; restart does not create polls.
- **Firmware-only on the MUX:** **Unknown but plausible** only if IGT confirms 2.0.1 breaks host-channel forwarding and provides a **supported** downgrade to SI-1.0.3. No on-cabinet assets to do this today.
- **Most likely complete fix:** Restore the **same external SAS host + MUX wiring** as `.90` (may include using the older MUX hardware). Firmware alignment is a **secondary** hypothesis test (swap or flash), not the first lever.

### Exact next action

**On the lab floor:** Document photograph/wire list for MUX CH1/CH2 on `.90` and `.171`, verify the IGT tester (or equivalent host) is connected on `.171`, then run a **MUX USB swap test** from `.90` to `.171` with CommCtrlSAS log + a 45 s `31150` capture. **Do not flash** until swap/wiring tests separate “missing host” from “bad MUX firmware.”

---

## Investigation commands / artifacts (this session)

- PsExec SYSTEM: PnP/CIM for `VID_0483&PID_5740`, USB enum registry `PortName` list.
- SMB: `CommCtrlSAS` binary hash, `CommControler.ini` hash, log grep all `CheckForMux` days in `var\log\CommCtrlSAS`.
- Scratch scripts (remote `C:\Windows\Temp\_tmp_mux_*.ps1`, `mux-pnp.txt`, `mux-driver.txt`) — safe to delete.

No service stop, flash, reboot, or config write was performed.
