# Frontend → Middleware: `/action` endpoint commands & data

Every command the Godot/C# frontend can send to the middleware through the action endpoint, **and the
full data payload each accepts**.

- **Endpoint:** `PUT http://<host>:8090/api/action/{playerId}`
- **Body (JSON):** `{ "Type": "<CommandName>", "Data": <payload> }`
- **Response:** `{ "Success": true|false }` + an `ETag` header with the new data "age".
- **Source of truth:** `ManagedRendererWebApiServer\Controllers\RouletteController.cs` →
  `PerformAction()` `switch (request.Type)`. Each command's data is translated to a numeric **wire
  code** for the C++ `ruleta.exe` core by `ManagedRendererWebApiProxy\RouletteCommands.cs`. An
  unrecognized `Type` is ignored and logged (`WARN: No action of this type`).

19 commands in release + 1 debug-only. `Success: true` on the async commands means *accepted* (queued
to the core), not *completed* — confirm real state via `GET /api/data/{playerId}` or the message tap.

---

## Quick reference

| Command | Data | Format |
|---------|------|--------|
| `PlaceBet` | JSON list of bet strings | each = `+`-joined numbers **or** a named-bet keyword; **no amount** |
| `CancelBet` | JSON list of bet strings | same grammar as PlaceBet |
| `CancelAllBets` | — | none |
| `CancelLastBet` | — | none |
| `RepeatBet` | — | none |
| `SetChip` | chip index | 0-based integer string, `"0".."ChipValues.Count-1"` |
| `SetNeighboursPower` | power index | 0-based integer string, `"0".."NumberOfNeighbours-1"` |
| `ChangeDenom` | — | none (cycles the settings denom list) |
| `StartGame` | — | none |
| `Collect` | — | none |
| `CallAttendant` | — | none |
| `Paytable` | `"<id>[:<numbersJson>]"` | id `0..7`; numbers = JSON array of number strings (UI caps 5) |
| `AdminMenu` | keyword | lowercase keyword string (enumerated below) |
| `MenuCommands` | raw keycode | numeric keycode string, optionally `"code arg"` |
| `UserLock` | — | none (throttled 2000 ms) |
| `Pin` | key id | integer string `"0".."12"` |
| `Keyboard` | key tokens | comma-separated key names; only `Control,C` and `F11` act |
| `SpanishGameChange` | — | none |
| `SpanishGameplay` | — | none |
| `Dallas` *(DEBUG only)* | string | test command |

---

## Betting — `PlaceBet`, `CancelBet`

`Data` is a **JSON array of bet strings**; the endpoint places/cancels each element. **A bet string
carries no stake** — the amount is whatever chip is currently selected (`SetChip`). Each bet string is
one of two forms:

**Form A — named-bet keyword** (exact, case-sensitive):
`Neighbours`, `Finales`, `MaxBet`, `Series58`, `Orphans`, `CloseToZero`, `CloseToDoubleZero`,
`ZeroSpiel`. (Some resolve differently by wheel type.)

**Form B — number-set field bet:** the covered wheel numbers joined by `+`. **Order does not matter**
(the set is sorted and matched by equality); the only in-string separator is `+`. A set not in the
master table is silently dropped. Double-zero is number **`37`**.

Concrete `Data` examples:
```json
["17"]                                     // straight-up 17
["1+2"]                                     // split 1/2
["1+2+3"]                                   // street 1-2-3
["1+2+4+5"]                                 // corner
["1+2+3+4+5+6"]                             // six-line
["1+4+7+10+13+16+19+22+25+28+31+34"]        // 1st column
["1+3+5+7+9+12+14+16+18+19+21+23+25+27+30+32+34+36"]   // Red
["17","1+2","Neighbours"]                   // multi-chip batch
```
`CancelBet` uses the identical grammar, e.g. `["17"]` or `["1+2+4+5"]`.

The complete enumerated set of accepted number-sets is **Appendix A**.

## Chips, denomination & neighbours — `SetChip`, `ChangeDenom`, `SetNeighboursPower`

- **`SetChip`** — `Data` = a **0-based chip index** as a string, e.g. `"0"`, `"2"`. Valid range is
  `0 .. ChipValues.Count-1`. The chip count and each chip's credit value are **data-driven from game
  settings** (`game settings.chips.number` / `chips.value.<name>`), not hard-coded. Wire code =
  `193 + index`.
- **`ChangeDenom`** — no data; cycles to the next denomination. The denomination set/order is the
  settings list (`game settings.denominations.value.<name>`). Wire code `277`.
- **`SetNeighboursPower`** — `Data` = a **0-based index** into the neighbours-power levels, e.g. `"0"`.
  Default levels are `{1, 2, 4, 6, 8, 10}` (indices 0–5) unless settings override
  (`game settings.neighbors.number` / `neighbors.value.*`). Wire code = `231 + index`.

## Game flow — `StartGame`, `Collect`, `CallAttendant`, `RepeatBet`, `Paytable`

- **`StartGame`**, **`Collect`**, **`CallAttendant`**, **`RepeatBet`** — no data.
- **`Paytable`** — `Data` = `"<paytableId>[:<numbersJson>]"`:
  - `<paytableId>` — integer, valid **0–7** (hard cap `< 8`); wire code `3590 + id`. The real set of
    ids is whatever paytables the game defines.
  - `<numbersJson>` — optional JSON array of number strings, e.g. `["7","11","23"]` (the user-paytable
    UI caps selection at **5**). Each entry maps through the same bet parser as `PlaceBet`.
  - The sentinel `"<id>:paytable"` clears a user selection (the literal `paytable` skips the numbers).

## Admin / menu / lock — `AdminMenu`, `MenuCommands`, `UserLock`

- **`AdminMenu`** — `Data` = a **lowercase keyword string** (not a number). Full enumerated set in
  **Appendix C**. Each keyword resolves either to a named command or to a `+`-joined field navigation.
- **`MenuCommands`** — `Data` = a **raw keycode string**, forwarded verbatim to `ruleta.exe` as
  `"<code>\r\n"`. It may also carry a space-separated argument, e.g. the reset-denomination string
  `"3570 0:0"`. There is **no managed enum constraining it** — the frontend chooses the code and the
  C++ core interprets it in the context of the currently-shown menu. The authoritative code set is the
  `imageCode` enum plus the frontend submenu codes — **Appendix B**.
- **`UserLock`** — no data. **Throttled**: a second UserLock within **2000 ms** is dropped
  (`UserLockThrottleMs`), protecting the C++ core's 1500 ms menu-key time filter. Expands to the
  two-key sequence `178` (open menu) → `181` (lock item).

## Direct input — `Pin`, `Keyboard`

- **`Pin`** — `Data` = an **integer string `"0".."12"`** (a `PinNumpadKeyData` enum). See **Appendix D**.
  Non-numeric data is rejected and logged; `13`/out-of-range is a no-op.
- **`Keyboard`** — `Data` = a **comma-separated list of key names** (browser `KeyboardEvent.key`
  values), e.g. `"Control,C"`. Only two combinations do anything:
  - contains both `Control` **and** `C` → **Stop** the roulette (ignored while bets are closed);
  - contains `F11` → open admin menu (Dallas).
  Every other token is accepted into the array but ignored.

## Spanish bonus — `SpanishGameChange`, `SpanishGameplay`

No data. `SpanishGameChange` → `ChangeBonusBet` (wire `253`); `SpanishGameplay` → `PlayBonus`
(wire `250`).

## Debug builds only — `Dallas`

`Data` = a test command string; compiled only under `#if DEBUG`, **absent in release/production**.

---

## Appendix A — accepted bet number-sets → engine input code

Send the numbers `+`-joined (any order). Codes shown for reference (from `RouletteBets.cs`).

**Straight-up 1–36:** 1→132, 2→80, 3→28, 4→134, 5→82, 6→30, 7→135, 8→83, 9→31, 10→136, 11→84,
12→32, 13→137, 14→85, 15→33, 16→144, 17→92, 18→40, 19→143, 20→91, 21→39, 22→141, 23→89, 24→37,
25→142, 26→90, 27→38, 28→140, 29→88, 30→36, 31→138, 32→86, 33→34, 34→139, 35→87, 36→35.

**Splits (horizontal):** 0+1→183, 1+4→119, 2+5→67, 3+6→15, 4+7→121, 5+8→69, 6+9→17, 7+10→122,
8+11→70, 9+12→18, 10+13→123, 11+14→71, 12+15→19, 13+16→124, 14+17→72, 15+18→20, 16+19→131,
17+20→79, 18+21→27, 19+22→130, 20+23→78, 21+24→26, 22+25→128, 23+26→76, 24+27→24, 25+28→129,
26+29→77, 27+30→25, 28+31→127, 29+32→75, 30+33→23, 31+34→125, 32+35→73, 33+36→21.
**Splits (vertical):** 1+2→106, 2+3→54, 4+5→108, 5+6→56, 7+8→109, 8+9→57, 10+11→110, 11+12→58,
13+14→111, 14+15→59, 16+17→118, 17+18→66, 19+20→117, 20+21→65, 22+23→115, 23+24→63, 25+26→116,
26+27→64, 28+29→114, 29+30→62, 31+32→112, 32+33→60, 34+35→113, 35+36→61.

**Streets:** 1+2+3→158, 4+5+6→160, 7+8+9→161, 10+11+12→162, 13+14+15→163, 16+17+18→170,
19+20+21→169, 22+23+24→167, 25+26+27→168, 28+29+30→166, 31+32+33→164, 34+35+36→165.

**Corners:** 1+2+4+5→93, 2+3+5+6→41, 4+5+7+8→95, 5+6+8+9→43, 7+8+10+11→96, 8+9+11+12→44,
10+11+13+14→97, 11+12+14+15→45, 13+14+16+17→98, 14+15+17+18→46, 16+17+19+20→105, 17+18+20+21→53,
19+20+22+23→104, 20+21+23+24→52, 22+23+25+26→102, 23+24+26+27→50, 25+26+28+29→103, 26+27+29+30→51,
28+29+31+32→101, 29+30+32+33→49, 31+32+34+35→99, 32+33+35+36→47.

**Six-lines:** 1+2+3+4+5+6→145, 4+5+6+7+8+9→147, 7+8+9+10+11+12→148, 10+11+12+13+14+15→149,
13+14+15+16+17+18→150, 16+17+18+19+20+21→157, 19+20+21+22+23+24→156, 22+23+24+25+26+27→154,
25+26+27+28+29+30→155, 28+29+30+31+32+33→153, 31+32+33+34+35+36→151.

**Trio:** 0+1+2→94.
**Columns:** `1+4+7+10+13+16+19+22+25+28+31+34`→126, `2+5+8+11+14+17+20+23+26+29+32+35`→74,
`3+6+9+12+15+18+21+24+27+30+33+36`→22.
**Dozens:** `1..12`→173, `13..24`→120, `25..36`→179.
**Odd/Even:** all-odd→174, all-even→181. **High/Low:** Low `1..18`→171, High `19..36`→177.
**Red/Black:** Red→176, Black→182.
**France/"horse":** two 24-number sets →396/397 and →398/399.

**Wheel-type additions** (double-zero = number `37`):
- Single-zero (default): 0→81, 0+2→68, 0+2+3→42, 0+3→16, basket `0+1+2+3`→146.
- Double-zero: 0→187, 37(=00)→184, 0+37→81, 0+2+37→68, 2+3+37→42, 3+37→16, basket `0+1+2+3+37`→146.

## Appendix B — `MenuCommands` keycodes

**Authoritative `imageCode` enum** (`Ruleta\RenderDeclarations.h`, mirrored in
`GoldClub.ManagedDeclarations\RenderDeclarations.cs`):

| Code | Meaning | Code | Meaning |
|---|---|---|---|
| 9 | Bonus | 240 | Repeat last game / double |
| 11 | Change view | 241 | Collect (hopper) |
| 13 | Credit view change | 242 | Max bet |
| 14 | Cancel all bets | 246 | Multigamer |
| 29 | Neighbours bet | 248 | Language |
| 48 | Zero-spiel | 249 | Collect (TITO) |
| 55 | Close-to-zero | 252 | Collect (bonus) |
| 100 | Finales | 254 | Collect (SAS handpay) |
| 107 | Orphans | 277 | Change denom |
| 133 | Series 5/8 | 278 | Autoplay |
| 152 | Stake | 279 | Neighbours power |
| 159 | Cancel last bet | 280 | Wins-per-number |
| 172 | Reload / repeat | 282 | Collect (remote) |
| 177 | Bingo numbers | 283 | Hide bets |
| 178 | **Open user menu** | 284 | Next bets |
| 190 | Start game | 285 | Collect (dispenser) |
| 192 | Help | 286 | Hide series |
| 193 | Chip 1 (chip/denom base) | 287 | Hide extra series |
| 223 | Statistics | 288 | Side bets set |
| 229 | Orphelins en plein | 289 | Side bets confirm |
| 237 | Collapse last numbers | 401 | Side bets take/leave |
| 239 | Collect (WAT) | 402 | Hot & cold |
| | | 403 | Screensaver |
| | | 442 | Change denom (no delay) |
| | | 1108 | Denomination selection |
| | | 1109 | Test mode |

**Codes the frontend actually emits** (from `RouletteWebApiModels\UI\Menu.cs`, sent by `MainScreen.cs`
et al.):
- **Denomination select:** `193 + index` → **193–197** (DENOM_1..5).
- **Reset denomination:** literal `"3570 0:0"` (code + argument).
- **Quit/power submenu:** YES=176, NO=182, LAST_3=181, CONFIG=177, RESTART_REMOTE=171,
  STOP_REMOTE=174, MODUL_STOP=3574, MODUL_RESTART=3575, MODUL_SHUTDOWN=3576, REMOTE_STOP=3577,
  REMOTE_RESTART=3578, REMOTE_SHUTDOWN=3579, REMOTE_RESET=3580.
- **Lock-on-station submenu:** BACK=200, LOCK_ALL=199, LOCK_THIS=198, LOCK_1..LOCK_10 = **51–60**.
- **Buy-credits keypad:** 1=171, 2=174, 3=176, 4=182, 5=181, 6=177, OK=178, CANCEL=14, CLEAR=159,
  BACK=160.
- **Numeric keypad (digits 1–10):** 132, 80, 28, 134, 82, 30, 135, 83, 31, 136 (0 = 81).
- **Dynamic menu codes:** `{171, 174, 176, 182, 181, 177}` — meaning depends on the active menu
  (that's why these numbers recur across submenus).

> The static codes are fully known, but the six dynamic codes have **no fixed meaning** — their effect
> is decided inside the WIBU-protected C++ core based on the currently displayed menu.

## Appendix C — `AdminMenu` keywords

Lowercase keyword strings (`RouletteBackend.cs`). Named-command keywords: `emptyhopper`,
`historygame`, `dropcurrenttokens`, `hoppercurrenttokens`, `emptystacker`. Field-navigation keywords
(sent as a `Bet`): `emptydispenser`, `historyjackpot`, `historyerror`, `historyhandpay`,
`historydispenser`, `historywat`, `historybill`, `historyhopper`, `historyticket`, `gameinfo`, `logs`,
`volumeup`, `volumedown`, `kbreset`, `lock`, `buycredits`, `financial`, `stop`, `eventviewer`,
`payout`, `crclist`, `testmode`, `bonuslogs`. Anything else → logged error.

## Appendix D — `Pin` key ids

| id | Key | id | Key |
|----|-----|----|-----|
| 0–9 | digits 0–9 | 11 | Exit (discard lock / reset password) |
| 10 | Cancel / Clear | 12 | OK / Enter |
| | | 13 | (sentinel `KEY_MAX` — no-op) |

Each maps to a raw core scancode string (e.g. 0→"81", OK→"178", Exit→"99") sent to `ruleta.exe`.

---

### Wire-code caveat

The numeric codes above (193 chip base, 231 neighbours, 277 denom, 3590 paytable, the `imageCode`
set, PIN scancodes) are the values sent to the C++ `ruleta.exe` core. The managed layer is authoritative
for **what data the endpoint accepts**; the ultimate per-context *effect* of each code is resolved
inside the (WIBU-protected) core, and the "dynamic menu" codes are context-dependent by design.
