# EGM comparison: 10.0.0.90 (reference) vs 10.0.0.171 (target)

Generated (UTC): 2026-07-13T08:06:26.3150143Z
Reference capture: 2026-07-13T08:06:04.2330517Z label=enabled gst=GST20664
Target capture:    2026-07-13T08:06:26.0590155Z label=current gst=GST19737

## Target Apply assessment

**partial-Apply (channels assigned, runtime not live)** (score 6/11)

- Channels with OwnerHostId=1: 6 (reference expects 5+)
- Channels with LastConfigurationChange=2: 6 (reference expects 4+)
- WatAccounts present: True
- sasmsgr log dir: False
- qGMID1:80/81 polls today: False
- CommCtrlSAS 31100/31150 bridge: False

## HEADLINE signals

| Signal | .90 | .171 |
|--------|-----|------|
| Key SAS services running | True | True |
| sasmsgr log folder | True | False |
| qGMID1:80/81 polls today | True | False |
| CommCtrlSAS 31100/31150 bridge | True | False |

## Top 5 actionable differences

1. **runtime_signal/SasmsgrLogDirExists**: ref=True target=False - Target needs SasmsgrLogDirExists=True to match reference transfer-ready state
2. **runtime_signal/HasQGMIDPollsToday**: ref=True target=False - Target needs HasQGMIDPollsToday=True to match reference transfer-ready state
3. **runtime_signal/CommCtrlSasBridge311**: ref=True target=False - Target needs CommCtrlSasBridge311=True to match reference transfer-ready state
4. **config_file/services\aurum\config\AurumSetup.xml**: ref=EFDD68C2B41C075BAD6B890EC28163BF43D0A702F672E4A88871F51CD2260D11 target=4995600BF4585C2DA6DAFC791DBADC18A4024B8DDA876DFD657FB63713554022 - Review config gap vs reference transfer-ready cabinet

## Services

| Service | .90 | .171 |
|---------|-----|------|
| GoldClub Hardware Subsystem | RUNNING | RUNNING |
| GoldClub Serial Communication Gateway | RUNNING | RUNNING |
| GoldClub Serial Communication Gateway SAS | RUNNING | RUNNING |
| GoldClub.Aurum.Services | RUNNING | RUNNING |
| GoldClub.Logging.LogDaemon | RUNNING | RUNNING |

## Config file SHA256

| Path | .90 | .171 | Same? |
|------|-----|------|-------|
| slot\themes\mgconfig.xml | 015B6F48... | AD36B60E... | NO (machine-id expected) |
| services\aurum\config\SASControler1\SASsetupData.xml | FE840740... | FE840740... | YES |
| services\aurum\AurumServicesConfig.xml | 0ECDC3AC... | 0ECDC3AC... | YES |
| services\aurum\config\AurumSetup.xml | EFDD68C2... | 4995600B... | NO |
| services\CommCtrlSAS\CommControler.ini | 28EF12FC... | 28EF12FC... | YES |
| services\CommCtrl\CommControler.ini | 54897D4E... | 54897D4E... | YES |
| var\run\taskhost.1\CommCtrlSAS.xml | 6206FEE9... | 6206FEE9... | YES |
| var\run\taskhost.1\CommCtrl.xml | F30145A9... | F30145A9... | YES |
| var\run\taskhost.1\GoldClub.Aurum.Services.xml | FD4AA666... | FD4AA666... | YES |

## Aurum SAS channel devices

| Device | .90 OH/LCC | .171 OH/LCC |
|--------|------------|-------------|
| noteAcceptor | 1/2 | 1/2 |
| handpay | 1/2 | 1/2 |
| bonus:0 | 1/2 | 1/2 |
| bonus:1 | 1/2 | 1/2 |
| voucher | 1/2 | 1/2 |
| WAT | 1/2 | 1/2 |
| communications | 1/0 | 1/0 |

- SASControler1 ConfigurationId: ref=51 target=51
- WatAccounts: ref=True target=True

## Runtime / ports

- Reference transport: WinRM
- Target transport: Unavailable
- .90 TCP 31100: 5:62917, 2:0
- .90 TCP 31150: 5:62918, 2:0
- .90 TCP 40000: 2:0

## Poll / log signals

- .90 last poll: 2026-07-13T08:05:55.125+01:00 INFO [:] qGMID1:80
- .171 last poll: (none today)
- .90 CommCtrlSAS: 2026-07-13T08:10:17.337+01:00 INFO [:5028] 328214125 Connection 3115000006 established on port 31150
- .171 CommCtrlSAS: 2026-07-13T08:52:37.129+01:00 INFO [:5628] 257343927 Listening on port 40000
- .90 SlotLog tail: 2026-07-13T08:05:55.705+01:00 INFO  [SlotMachine] OneHand.Utilities.InformationManager - Information item added: ['TextGameSelect'] [INFO] [1000000] 'Select A Game' on 235760.1385207
- .171 SlotLog tail: 2026-07-13T08:53:27.001+01:00 INFO  [SlotMachine] OneHand.Utilities.InformationManager - Information item added: ['TextGameSelect'] [INFO] [1000000] 'Seleccione un juego' on 240940.7503136

## Machine-id expected diffs (ignore for Apply)

- slot\themes\mgconfig.xml: different SHA (GST identity / game pack)
