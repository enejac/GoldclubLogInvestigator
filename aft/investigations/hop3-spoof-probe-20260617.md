# Hop 3 alternative spoof probe - cabinet `10.0.0.90` (2026-06-17)

Read-only / controlled probe of **alternative Hop 3 credit paths** that do not use
WinDivert (Hop 2) and do not require a SAS host. Goal: land real `Cashless In`
credits without the CommCtrlSAS bridge inject.

**Cabinet:** `10.0.0.90` (`GST20664`), EGM `GCC_ST_20664_01`
**Transport:** WinRM (`GOLD-CLUB\test`) for remote C#/PowerShell probes
**Oracle:** `\\10.0.0.90\c$\Goldclub\var\log\SlotLog\2026-06-17.log` - `Cashless In:` lines

**Result:** All five vectors probed; **none produced credits.** Hop 2 WinDivert
in-stream injection remains the only proven decouple point.

---

## 1. Vectors tested

| # | Vector | Endpoint / target | Outcome |
|---|--------|-------------------|---------|
| 1 | EGM remoting enumeration + invoke | `http://169.254.243.18:50010/GM2AU` | **DEAD** - interface hidden |
| 2 | HOST remoting + pre-create txn | `http://localhost:50011/SASControler1` | **DEAD** - no service methods visible |
| 3 | Direct `authorizeTransfer` / `commitTransfer` | `:50011` proxy | **DEAD** - methods not found |
| 4 | AFT XML state hand-edit | `aftMostRecentTransaction_v1.xml`, history | **DEAD** - write-only output, not read by OneHand |
| 5 | Trigger OneHand re-read after XML write | `:50010` remoting, trigger file | **DEAD** - no Cashless In |

---

## 2. EGM remoting (`:50010` / GM2AU)

### Binding

| Property | Value |
|----------|-------|
| URL | `http://169.254.243.18:50010/GM2AU` |
| Process | `OneHand.exe` (e.g. PID 5264) |
| `localhost:50010` | **Refused** - binds link-local only |
| Protocol | .NET Remoting over HTTP (binary serialization) |

### Reflection

- Transparent proxy type: `System.MarshalByRefObject` only
- **3 methods** visible: `GetLifetimeService`, `InitializeLifetimeService`, `CreateObjRef`
- **88+** guessed method names via `InvokeMember` - all **method not found**

### GM2AU log behavior (observed)

- Cashless withdrawals: `Withdraw successful GCC_ST_20664_01 in ...`
- Host credit can be **denied**: `Credit Transfer from Host denied!`
- SAS ack queue: `pstAck outQ:http://GST20664:50011/SASControler1`

**Conclusion:** Interface hidden behind `MarshalByRefObject`. Requires decompiling
`OneHand.exe` or binary remoting capture.

---

## 3. HOST remoting (`:50011` / SASControler1)

- Connection succeeds; transparent proxy obtained
- Only **7 base methods** visible; WAT methods not found on untyped proxy
- Pre-create `AurumTransaction` failed: type not in loaded assemblies

**Contrast with 2026-06-15:** typed WAT client reached `RequestTransferPosted` NRE.
Generic probe never reached WAT methods.

---

## 4. AFT XML state manipulation

- PowerShell `[xml]` could not edit `transactionId` char arrays or related fields
- **No Cashless In** after spoof write
- AFT XML is WAT2AFT **write-only output**; real state in memory / `.dat` files

---

## 5. Cross-cutting findings

- Remoting binds `169.254.243.18`, not `127.0.0.1`
- `Cashless In` requires full WAT2AFT commit chain

---

## 6. Dead-ends summary

| Vector | Why dead |
|--------|----------|
| Generic remoting `:50010` / `:50011` | Interface hidden |
| Pre-create txn + `requestTransfer` | Type unavailable; typed path NRE |
| AFT XML hand-edit | Output-only; not read by OneHand |
| `.aft_refresh_trigger` | No effect |
| `setBonusAward` (prior) | Host rejects id sequence |

---

## 7. Recommended next steps

1. Hop 2 WinDivert (proven)
2. Typed remoting with Aurum DLLs (NRE only)
3. Decompile `OneHand.exe`
4. Binary remoting capture during good transfer
5. Deserialize `wataccounts_SASControler1.dat` (high risk)

---

## 8. Injection loop stopped (2026-06-17)

Four local `Forever-Inject-Verify.ps1` processes stopped. Last loop Cashless In:
`2026-06-17T14:03:09` ($1,000).

---

## Source map

- `../hops/hop3-aurum-wat2aft-to-aft-xml.md` sections 4 and 6
- `../RUNBOOK.md` section 7
- `alt-credit-paths.md`
- `RunProbeSASControler1.ps1`, `Probe-AFT-XML-Spoof.ps1`, `Hop3Probe.cs`

*Probe date: 2026-06-17. Cabinet: 10.0.0.90 (GST20664).*