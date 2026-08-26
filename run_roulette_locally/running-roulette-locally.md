# Running GoldClub Roulette on a development PC

> A step-by-step setup guide. No roulette wheel, no WIBU dongle, no bill acceptor, no cabinet
> hardware — just a Windows dev box.
>
> Every step here exists because skipping it produces a symptom that points somewhere else entirely.
> Where that is true, the symptom is written next to the step. If you hit something not listed,
> **read §9 before debugging** — the answer is probably there.

---

## Status on this PC (`ENEJZBOGAR`) — 2026-07-30

| Item | State |
|---|---|
| Full roulette image | On **`G:\`** (write-protected). Junctions under `C:\goldclub\` point at it. |
| `StartRig.ps1` paths | `C:\goldclub\ruleta` → `G:\ruleta`, `C:\goldclub\bin` → `G:\bin`, `C:\goldclub\config` → `G:\config` |
| Aurum host | Writable override at `C:\services\aurum\config\AurumSetup.xml` with `<NetworkHostName>ENEJZBOGAR</NetworkHostName>` (G: original stays `GRT330106`) |
| Keyboard stub | Present in this folder (`KeyboardStub.ps1`) |
| **Blocker** | Only **Release** `ruleta.exe` copies exist; no WIBU dongle → last run logged `ERR: Licence.dll file is missing` |
| **Blocker** | No VS2019 / v141 toolset and no `GC-Roulette-automation` repo → cannot build Debug |
| Lab cabinets | `.90` / `.112` are **slot** (OneHand), not roulette — no `ruleta.exe` on the fleet right now |

**To finish local launch:** obtain a `Debug\|x64` `ruleta.exe` + Debug GoldClub DLLs + `boost_*-vc141-mt-gd-x64-1_67.dll`, drop them into `C:\goldclub\ruleta` (writable overlay) or unlock `G:`, then `.\StartRig.ps1 -Check`.

---

## 1. What you need first

| | |
|---|---|
| **Visual Studio 2019**, any edition | **Mandatory.** `Ruleta.vcxproj` uses PlatformToolset **v141**, which only VS2019 ships. VS2022 has v143 and fails with `MSB8020`. |
| **Visual Studio 2022** | Optional, for the C# tooling (SuperDragica, the QA bots). Not usable for the C++ core. |
| **A copy of `C:\goldclub\` from a real cabinet** | The game reads config, paytables and libraries from hard-coded paths under here. You cannot construct it from the repo. Copy the whole tree. |
| **The repo** | `C:\development\git\GC-Roulette-automation` (branch `tooling/superdragica-and-qa`). |

You do **not** need: a dongle, a wheel, a ball sensor, a bill acceptor, a touch panel, or admin rights
for normal use.

---

## 2. Build `ruleta.exe`

**`Debug|x64`. Not Release.** `Debug` defines `_DEBUG`, and together with `_TESTING` that compiles out
the WIBU dongle check and the `Licence.dll` check — the `#if defined(_TESTING) || defined(_DEBUG)`
guards at `ARuleta.cpp:624` and `ARuleta.cpp:648`. A `Release|x64` build without a physical dongle
simply `return 0`s and exits, with nothing in the log to tell you why.

```bat
"C:\Program Files (x86)\Microsoft Visual Studio\2019\Professional\MSBuild\Current\Bin\MSBuild.exe" ^
   Sources\Ruleta\Ruleta\Ruleta.vcxproj ^
   /p:Configuration=Debug /p:Platform=x64 /p:BuildProjectReferences=false /v:minimal /nologo
```

Adjust `Professional` to your edition. Opening `Sources\Ruleta\RuletaCertifiedDebug.sln` in VS2019 and
pressing F5 works too, and is the easier way to debug.

---

## 3. A Debug exe needs **Debug** GoldClub DLLs — this is not optional

This is the step people skip, and it costs a day.

Interfaces such as `virtual std::wstring MessageFromRoulette(std::wstring)` pass `std::wstring`
**by value across the exe↔DLL boundary**. A Debug build sets `_ITERATOR_DEBUG_LEVEL=2`, which changes
both the object layout *and* which heap owns the allocation. A Debug `ruleta.exe` calling Release
GoldClub DLLs is therefore undefined behaviour: you get garbage strings and
`STATUS_HEAP_CORRUPTION` (`0xC0000374`).

It appears to work under a debugger, because the NT debug heap pads allocations enough to hide it.
That is the trap — it "works in VS" and crashes when launched normally.

Deploy the **Debug** builds of these into `C:\goldclub\ruleta\lib\`:

```
GoldClub.OnLine.dll   GoldClub.Render.dll   GoldClub.HW.dll
RuletaCrypt.dll       GoldClub.Settings.ATL.dll   GoldClub.Logging.Native.dll
```

Keep the originals — on this machine they are in `C:\goldclub\ruleta\lib\ReleaseDllBackup\`. Do the
same, so you can put the cabinet build back.

---

## 4. Fill the gaps in the deployment folder

The game runs from `C:\goldclub\ruleta`. Config paths are relative to the **working directory**;
managed assembly probing is relative to the **exe**. So it must be launched from a complete folder.

A fresh copy from a cabinet is usually missing these. Each row is a real failure someone has already
chased:

| Missing piece | What you actually see |
|---|---|
| Debug boost DLLs (`boost_*-vc141-mt-**gd**-x64-1_67.dll`) | `0xC06D007E` delay-load failure. Exits **silently, before logging starts** — no log file to read. |
| `lib-common\Newtonsoft.Json.9.dll`, `EmbedIO\3.4.3.0\`, `Swan.Lite\3.0.0.0\` | `Load of web server failed!` — nothing ever listens on `:8090`. |
| `lib-common\NUlid.dll` (1.2.0) | Every `/api/data` call throws; responses come back `304` or 0 bytes. |
| `lib-common\Newtonsoft.Json.dll`, the real strong-named 9.0.0.0 (token `30ad4fe6b2a6aeed`) | `MessageFromRoulette` throws. This is a **different file** from `Newtonsoft.Json.9.dll` — you need both. |
| `C:\goldclub\config\etc\application\ruleta\paytables\` | `PayTable.cpp:302` bare-`throw`s on the first paytable it cannot load. |
| `C:\services\aurum\config\AurumSetup.xml` | `CONFIG FOR GM2AU NOT FOUND!` → the game sends itself `WM_STOPRULETA` and shuts down. |

---

## 5. Point AurumSetup at *your* machine

`AurumSetup.xml` picks its configuration **by network host name**. If no entry matches your PC,
nothing is selected and the game stops itself.

Open `C:\services\aurum\config\AurumSetup.xml` and set a `<NetworkHostName>` to your machine name
(`echo %COMPUTERNAME%`), or add a block for it.

Aurum's *socket* failures (`:25077`, `:25071`, `MeterHost`, `SASControler1`) are tolerated retries and
can be ignored. Only the missing **config** is fatal.

---

## 6. Settings to change

Edit these with `C:\goldclub\bin\Setup.2.exe`. **Never hand-edit `setup.xml`** — it is obfuscated
(GoldClub "mangler `type0`") and editing it by hand corrupts it.

### Required — the game will not run correctly without these

| Setting | Value | Why |
|---|---|---|
| `game settings → additional → number of player stations` | **1** | Defaults to `MAXP` (8). Eight render windows on one monitor gives **ERROR 28 "Display adapters"**. |
| `game settings → additional → ballread → sensor type` | **2** (sim-all) | The real no-hardware switch: it short-circuits the mainboard port, the POWER-ON handshake and the whole sensor stack. `HWDummy` is dead code — do not go looking for it. |

### Turn off the hardware this PC does not have

| Setting | Value | Device |
|---|---|---|
| `hardware settings → ups → active` | **0** | UPS |
| `hardware settings → bill dispenser → active` | **0** | note dispenser |
| `hardware settings → additional → keyboardpanel → IsPing` | **0** | stops the panel keep-alive. The stub in §7 is still required — that covers the initial connect, this covers the ongoing ping |
| `game settings → additional → jackpot` | **0** | only if no jackpot system is connected |

### What a working dev rig actually reads

Decoded from a rig where everything above is done and the game runs. Useful as a reference when
something behaves oddly — anything differing from this column is worth a look:

```
hardware settings.tito.active                 0      hardware settings.egasa.active       0
hardware settings.lights.active               0      hardware settings.cylinder.active    0
hardware settings.mechanical counters.active  0      hardware settings.switches additional.active  0
hardware settings.bill.serial bill.active     1      hardware settings.handpay.active     1
game settings.timing.screensaver.active       1      game settings.additional.IsLockWhenNoCommunication  1
```

The bottom row is **not** a list of things to change — the rig works with them as they are. Two are
worth knowing about:

- **`screensaver.active = 1`** will break an automated click run. The clicking bot (§11) holds the
  frontend window in the foreground for the whole scenario; a screensaver waking up takes the
  foreground and the run aborts. Turn it off before any unattended or overnight run.
- **`IsLockWhenNoCommunication = 1`** is worth remembering if a station ever locks itself for no
  visible reason on a box with no online link.

### Reading the settings without Setup.2.exe

`setup.xml` is obfuscated, so a text editor shows nothing useful. **SuperDragica → Diagnostics →
Roulette Settings** decodes it read-only, using GoldClub's own settings library. That is the quickest
way to confirm what a machine is actually configured with.

---

## 7. The keyboard stub is mandatory

`OpenPorts()` (`ARuleta.cpp:785`) TCP-connects to CommCtrl for each player station. Unlike its
neighbours it has **no sim-mode guard**, so with nothing listening the core throws `CRuletaError(19)`,
paints **ERROR 19**, drops into `STOPDIALOG` and tears down — taking `:8090` with it.

From the outside that looks like a **frontend bug**: a permanent loading spinner with a flickering,
jumping countdown. It is not. It is a dead stub.

Ports come from `C:\goldclub\ruleta\config\commconfig.cfg` — station 1 is **30300**.
`CSocketConnection::Open()` does connect → non-blocking → `TCP_NODELAY` and nothing else: there is
**no handshake**, so a listener that accepts and stays silent is enough.

`tools-internal\RouletteQA\KeyboardStub.ps1` is that listener. `StartRig.ps1` starts it for you. It
must stay running for the whole session.

---

## 8. Start it

```powershell
cd C:\development\git\GC-Roulette-automation\tools-internal\RouletteQA
.\StartRig.ps1
```

It checks everything in §4–§7 **before** starting anything, starts the keyboard stub if nothing is on
30300, launches the game, waits for `:8090`, and prints live state.

```
.\StartRig.ps1 -Check     # verify only, start nothing
.\StartRig.ps1 -Restart   # stop a running game first
```

**Exit code 0 means ready.** Anything else names the step that failed, and the message tells you what
the symptom would have looked like. That is the point of the script — it front-loads a day of
debugging.

To start the game by hand instead: run `C:\goldclub\ruleta\ruleta.exe` **with the working directory
set to `C:\goldclub\ruleta`**. Launching it from anywhere else breaks the config paths.

---

## 9. Using it

### The admin menu, without a Dallas key

**F11 opens the admin menu.** The middleware maps F11 to a `Dallas` command carrying the hardcoded
iButton ID `1234567890ABCDEF`, which the core accepts as user `testing`. F11 *is* the Dallas key.

Press it in the Godot window, or over the API:

```
PUT http://localhost:8090/api/action/0    {"Type":"Keyboard","Data":"F11"}
```

`Keyboard` passes exactly two keys through: **F11**, and **Ctrl+C**, which stops the roulette.

### Inserting credit

Exactly as an operator does on a cabinet:

1. **F11** — opens the admin menu
2. **Buy credits**
3. Choose an amount (the amount buttons repeat: pressing "6" three times buys 3×10 000)
4. **F11** — commits the transaction and closes the menu
5. **Choose a paytable** — this prompt appears **only when the balance was zero** before the purchase, and the game will not start a round until you answer it

Do **not** use Test mode for anything you intend to trust. It hands out non-cash credit and disables
the online link and the meters, so it exercises a different code path from production.
`BuyCredits` (9998) is a **no-op** in the middleware — don't build on it.

### Where the logs actually are

`bootstrap.ini` sets `LogDir %:/var/log/`, so `logger.xml`'s `%LOGROOT%` resolves to:

```
C:\goldclub\var\log\<app> <group>\
```

**Not** `C:\goldclub\ruleta\log`, which is an unused legacy path. **Read both of these:**

| Folder | What is in it |
|---|---|
| `C:\goldclub\var\log\ruleta\` | general startup |
| `C:\goldclub\var\log\ruleta Roulette\` | **the C++ core's own trace — including the fatal `ERR:` lines and `STOPDIALOG`** |
| `C:\goldclub\var\log\godot1\` | the frontend |

Chasing a failure in the wrong file wastes hours. If the game died, the answer is almost always in
**`ruleta Roulette\`**.

---

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Exits instantly, no log at all | Missing **debug** boost DLLs (`-mt-gd-x64-`) → `0xC06D007E` | §4 |
| `0xC0000374` heap corruption, or garbage strings | Debug exe + **Release** GoldClub DLLs | §3 |
| Works under F5, crashes when launched normally | Same as above — the debug heap was hiding it | §3 |
| Runs, but `Release` build silently `return 0`s | No dongle, and Release doesn't compile the check out | §2 — build Debug |
| **ERROR 28** "Display adapters" | 8 player stations configured on one monitor | §6 — set stations to 1 |
| **ERROR 19**, then shutdown | Nothing listening on 30300 | §7 — the keyboard stub |
| Permanent loading spinner, flickering/jumping countdown | Also ERROR 19 — the core tore down and took `:8090` with it | §7 |
| `CONFIG FOR GM2AU NOT FOUND!` then shutdown | `AurumSetup.xml` has no entry for your host | §5 |
| Nothing on `:8090`, `Load of web server failed!` | Missing EmbedIO / Swan.Lite / Newtonsoft | §4 |
| `/api/data` returns `304` or 0 bytes | Missing `NUlid.dll` | §4 |
| `MSB8020` on build | Using VS2022; the project needs v141 | §2 — use VS2019 |
| Exit codes `0xC000013A` / `0x3` that look like crashes | Your tooling job-killed the child process. `ruleta.exe` is a **GUI-subsystem** app | Launch with something that genuinely detaches before concluding anything about stability |

---

## 11. Once it runs: driving it automatically

With the rig up, the QA bots in `tools-internal\RouletteQA\` can play it. Start here:

```powershell
cd tools-internal\RouletteQA\ActionBot\bin\Debug\net472
.\ActionBot.exe --scenario ..\..\..\Scenarios\smoke-round.json
.\ActionBot.exe --all                                          # the whole corpus
```

`ActionBot` funds itself through the admin menu exactly as §9 describes, plays a scenario, and asserts
against `GET /api/data/{player}`.

**`ScenarioRecorder.exe`** records new scenarios: press Record, play the game, press Stop, Save. It can
replay them over HTTP, or — with **Replay by clicking** ticked — by injecting real clicks into the
Godot window, which puts the frontend in the loop.

`Scenarios\README.md` documents the JSON format and the traps that have cost real debugging time.
**Read it before writing or editing a scenario.**

---

## 12. Two core bugs that were fixed while getting this working

Mentioned so you recognise them if you work on an older revision:

- **`CLogging::_write` corrupted the heap on every formatted log call.** It sized its buffer with
  `_vsnwprintf(NULL, 0, ...)`, which returns **−1**, giving `malloc(0)` — and then the legacy unbounded
  3-argument `vswprintf` wrote the whole message past it. The fix is `_vscwprintf` + `va_copy` +
  `vswprintf_s`.
- **`WriteLogChar` passed an already-formatted message to the *variadic* `write()` overload**, so any
  `%` in a message became a conversion specifier — e.g. `total memory load 039%`. Harmless-looking
  until the heap bug above was fixed, at which point it started tripping a debug-CRT assertion dialog.
  Construct a `std::wstring` to select the non-variadic overload.
