# Cabinet 10.0.0.171 SAS state (before)

Captured (UTC): 2026-07-10T11:52:27Z

## Services
Method: sc.exe per-service query (fixed list); WinRM not used

- GoldClub.Aurum.Services: RUNNING (Running=True)
- GoldClub Serial Communication Gateway: RUNNING (Running=True)
- GoldClub Serial Communication Gateway SAS: RUNNING (Running=True)
- GoldClub Hardware Subsystem: RUNNING (Running=True)
- GoldClub.Logging.LogDaemon: RUNNING (Running=True)

## Key config paths

- slot\themes\mgconfig.xml size=116918 sha256=AD36B60EA838D07F8226B4BB3679445A5C664B3B7F38CA0DCBC072A4BE2C1DFF
- services\aurum\config\SASControler1\SASsetupData.xml size=1506 sha256=FE840740403CCA2A414635EBE2E345CD56464828FB6527AD279104C1940CD544
- services\aurum\AurumServicesConfig.xml size=1709 sha256=0ECDC3AC001E1C0FF87077339AF5CAA4B1AEBAA5E1257F17B066E2C6265FE4E6
- services\aurum\config\AurumSetup.xml size=22027 sha256=6F0E3EA2B8BFDB9DC16B10191D8CE64A933FC404BB7EA49719CB239C84BEECAF
- services\CommCtrlSAS\CommControler.ini size=232 sha256=28EF12FCCC936391105CBAFE65003596C6212F1117D69F89449C3B636653A3AF
- services\CommCtrl\CommControler.ini size=472 sha256=54897D4E4D0600C2D068A33619FC71E912DAD239286A8AB983706ECB8B6206B7
- var\run\taskhost.1\CommCtrlSAS.xml size=271 sha256=6206FEE9AC04B1939FB09419960450D0BCD7E92F0342D085108D444337A85041
- var\run\taskhost.1\CommCtrl.xml size=257 sha256=F30145A9C16B53D96F6EECB7C9569B6C7E67546B1603F562FEF43EC0840C5B9D
- var\run\taskhost.1\GoldClub.Aurum.Services.xml size=278 sha256=FD4AA666EAAF026934C000BAE2E8CF8E596DB0CD0DD4D40BCCA8A01CC0CFDA13

## Poll signals

- No qGMID1:80/81 in sasmsgr today

## Log last line

- \\10.0.0.171\c$\Goldclub\var\log\GoldClub.Aurum.Services SASControler1\2026-07-10.log -> 2026-07-10T12:26:08.011+01:00 INFO [:] device GCC_ST_19737_01.0.optionConfig
- \\10.0.0.171\c$\Goldclub\var\log\CommCtrlSAS\2026-07-10.log -> 2026-07-10T12:24:23.190+01:00 INFO [:3204] 10849988 Listening on port 40000
- \\10.0.0.171\c$\Goldclub\var\log\SlotLog\2026-07-10.log -> 2026-07-10T12:25:08.006+01:00 INFO  [SlotMachine] ... machine locked (communications offline)
