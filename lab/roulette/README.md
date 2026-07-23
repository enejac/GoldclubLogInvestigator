Roulette Dallas inject (fork) vs slot
====================================

Slot (DO NOT MODIFY):
  lab\Invoke-DallasSpliceRemote.ps1 + probes\DallasSplice.exe
  WinDivert splice into live OneHand/BiOS <-> CommCtrl:30800

Working roulette path (validated on 10.0.0.90):
  ruleta.exe  <-->  CommCtrl :30300  (keyboard / KeyCtrl / ReadDallas)
  Inject ASCII:  700\r\n 701\r\n <16-hex-ROM>\r\n
  (default -Rom 700|||701|||01D68A721B000019)
  ruleta log: KEY=admin CODE=01 DALLAS=<ROM> EVENT=IN|OUT

Fast WinRM (default = robust):
  cd lab\roulette
  .\Invoke-DallasSpliceRouletteRemote.ps1 -ComputerName 10.0.0.90

  Default Action=insert: exactly ONE Dallas splice per run (no insert_retry).
  Inject 700/701/ROM, wait for a fresh KEY=admin IN (menu login). Drain runs in
  the background. Guest handpay is cleared by that same insert when present.
  Success = UiReady=True only with a fresh KEY=admin IN this run.

  Options:
    -Action insert|eject|roundtrip
    -WaitForDrain       block until TCP drain finishes (can cause KEYBOARD TIMEOUT)
    -SkipGate           do not wait for keyboard-down hard gate
    -GateTimeoutSec N   max wait for keyboard reconnect (default 60)
    -HandpayClearTimeoutSec N  wait for unlock after admin IN (default 15)
    -KeyboardRecoverSec N      wait after KEY=admin for kb reconnect (default 12)
    -Mode capture       passthru + payload log
    -Mode heal -InitialDelta N

  If you only see KEYBOARD TIMEOUT: wait ~10s, re-run (do not spam).
  If Blocker=NO_KEY_EVENT: keyboard/splice race — wait, then re-run once.

Do NOT use the :30800 stub path (stops gateway / breaks physical Dallas).
--------------------------------------------------------------------
Roulette AFT (SAS 0x72) - different path from Dallas
--------------------------------------------------------------------

Slot AFT uses CommCtrlSAS:31150 -> Aurum.
Roulette (when MUX is up) uses CommCtrlSAS **WakeUpPort** (usually **30550**) after
COM5 @921600 CheckForMux. Channel comes from:

  C:\goldclub\services\aurum\config\SASControler1\ClientsSet.xml
    <SASAddress>1</SASAddress>   <Port>30500</Port>   <WakeUpPort>30550</WakeUpPort>

  From repo root (or cd lab\roulette):
  .\lab\roulette\Invoke-WinDivertAftRoulette.ps1 -Send
  .\lab\roulette\Invoke-WinDivertAftRoulette.ps1 -Send -IP 10.0.0.90 -nr 100000

  Match successful IGT tester cashable landing on .90 (2026-07-20):
  .\lab\roulette\Invoke-WinDivertAftRoulette.ps1 -Send -c 1000000

  Or promo-style (default amount when no -c/-r/-nr is 100000):
  .\lab\roulette\Invoke-WinDivertAftRoulette.ps1 -Send -nr 100000

Works with **IGT tester on or off**:
  - Tester on: organic qGMID1:80/81; script still uses pollaft for post-AFT polls,
    but use -NoAutoWake so it does not kill the tester session.
  - Tester off: AutoWake recycles Gateway SAS + Aurum if WAT is stale, then
    pollaft simulates 80/81 on the WakeUpPort loopback and injects 0x72.

Cashout `AFT EXCEPTION ISSUED: 66` is **ignored** (not a pending host->EGM busy).
Hard block remains open transfer / exception 69.

Proven WinDivert pollaft run (2026-07-23, .90 roulette — no IGT session):
  Command:
    .\lab\roulette\Invoke-WinDivertAftRoulette.ps1 -Send -IP 10.0.0.90 -nr 100000
  Observed:
    ClientsSet: SASAddress=1 Port=30500 WakeUpPort=30550 EgmId=GCC_RT_330106_01
    BridgePort=30550 (ESTABLISHED); AutoWake via WinRM (Gateway SAS + Aurum)
    pollaft: AFT_INJECTED on :30550; sasmsgr INGESTED qGMID1:0172... (txn 37)
    Aurum: FULL_TRANSFER_SUCCESSFUL
      NonRestricted Req(100000), NonRestricted Com(100000)
      TransferType TRANSFER_INHOUSE_AMOUNT_FROM_HOST_TO_GAMING_MACHINE
  Do **not** use slot-style :31150 inject on this roulette image.

Proven tester flow (Aurum):
  TRANSFER REQUEST STARTED (cashable) -> ALL WAT FINISHED -> FULL_TRANSFER_SUCCESSFUL
  sasmsgr: qGMID1:0172... (addr 1) on the same GMID1 channel as 80/81.

If Gateway SAS only listens on :40000 (no 30550):
  COM5/MUX USB is missing or failed after a service restart.
  Cold-boot the cabinet (or restore the USB serial device); wake alone often stays on :40000.
  Slot-style inject against 31150 will fail with NO_ESTABLISHED on this image.
--------------------------------------------------------------------
IGT SAS tester TX-only / LED mismatch (fresh EGM)
--------------------------------------------------------------------

See `..\SAS-ADDRESS-IGT-CHECKLIST.md`.

After a fresh build, open `ClientsSet.xml` and force `<SASAddress>1</SASAddress>`
to match lab IGT `Address = 1` (Port stays COM4). Image may ship as address 11.

