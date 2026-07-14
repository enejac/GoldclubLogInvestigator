# Cabinet 10.0.0.90 SAS state (enabled)

Captured (UTC): 2026-07-13T08:06:04.2330517Z
SMB: OK - \\10.0.0.90\c$ + Goldclub\var\log

## Key signals
- sasmsgr log dir exists: True
- qGMID1:80/81 today: True
- CommCtrlSAS log mentions 31100/31150: True
- Key SAS services running: True
- Runtime transport: WinRM

- CommCtrlSAS process: True
- TCP 31150: 5:62918, 2:0
- TCP 31100: 5:62917, 2:0

## Services (sc.exe \\ip query type= service (single pass, no PsExec))

- GoldClub.Aurum.Services: RUNNING
- GoldClub Serial Communication Gateway: RUNNING
- GoldClub Serial Communication Gateway SAS: RUNNING
- GoldClub Hardware Subsystem: RUNNING
- GoldClub.Logging.LogDaemon: RUNNING

## Config files

- slot\themes\mgconfig.xml: 115245 bytes sha256=015B6F48D8DE77D1A429744826A3440A854C868AF3347924615EA030EF870760
- services\aurum\config\SASControler1\SASsetupData.xml: 1506 bytes sha256=FE840740403CCA2A414635EBE2E345CD56464828FB6527AD279104C1940CD544
- services\aurum\AurumServicesConfig.xml: 1709 bytes sha256=0ECDC3AC001E1C0FF87077339AF5CAA4B1AEBAA5E1257F17B066E2C6265FE4E6
- services\aurum\config\AurumSetup.xml: 23398 bytes sha256=EFDD68C2B41C075BAD6B890EC28163BF43D0A702F672E4A88871F51CD2260D11
- services\CommCtrlSAS\CommControler.ini: 232 bytes sha256=28EF12FCCC936391105CBAFE65003596C6212F1117D69F89449C3B636653A3AF
- services\CommCtrl\CommControler.ini: 472 bytes sha256=54897D4E4D0600C2D068A33619FC71E912DAD239286A8AB983706ECB8B6206B7
- var\run\taskhost.1\CommCtrlSAS.xml: 271 bytes sha256=6206FEE9AC04B1939FB09419960450D0BCD7E92F0342D085108D444337A85041
- var\run\taskhost.1\CommCtrl.xml: 257 bytes sha256=F30145A9C16B53D96F6EECB7C9569B6C7E67546B1603F562FEF43EC0840C5B9D
- var\run\taskhost.1\GoldClub.Aurum.Services.xml: 278 bytes sha256=FD4AA666EAAF026934C000BAE2E8CF8E596DB0CD0DD4D40BCCA8A01CC0CFDA13

## Aurum SAS channel devices

- noteAcceptor id=0 enabled=false ownerHost=1 lcc=2
- handpay id=0 enabled=false ownerHost=1 lcc=2
- voucher id=0 enabled=false ownerHost=1 lcc=2
- WAT id=0 enabled=false ownerHost=1 lcc=2
- bonus id=0 enabled=false ownerHost=1 lcc=2
- bonus id=1 enabled=false ownerHost=1 lcc=2
- communications id=1 enabled=false ownerHost=1 lcc=0
- SASControler1 Enabled=true configId=51 WatAccounts=True

## Poll signals

sasmsgr dir: \\10.0.0.90\c$\Goldclub\var\log\GoldClub.Aurum.Services sasmsgr of SASControler1
- 2026-07-13T08:05:53.325+01:00 INFO [:] qGMID1:81
- 2026-07-13T08:05:53.525+01:00 INFO [:] qGMID1:80
- 2026-07-13T08:05:53.725+01:00 INFO [:] qGMID1:81
- 2026-07-13T08:05:53.925+01:00 INFO [:] qGMID1:80
- 2026-07-13T08:05:54.125+01:00 INFO [:] qGMID1:81
- 2026-07-13T08:05:54.324+01:00 INFO [:] qGMID1:80
- 2026-07-13T08:05:54.525+01:00 INFO [:] qGMID1:81
- 2026-07-13T08:05:54.725+01:00 INFO [:] qGMID1:80
- 2026-07-13T08:05:54.925+01:00 INFO [:] qGMID1:81
- 2026-07-13T08:05:55.125+01:00 INFO [:] qGMID1:80

## COM4 vs SMB (HOST inject)

- SMB C$ admin share = file access to CABINET disk over network.
- COM4 on HOST = local serial on YOUR workstation.
- com_port_blocked / PermissionError on COM4 = HOST port locked by another app or driver - NOT SMB.
