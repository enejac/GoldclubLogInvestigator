# Put quarantined signed DeviceManager files back after a failed regen.
# Stops Aurum + SAS first so the 222-byte stubs are not locked.
# Does not touch licences or serialport. Does not rewrite the MAC.
param(
    [string]$QuarantineDir = 'C:\goldclub\var\state\sas-101-quarantine-20260819-133649'
)
$ErrorActionPreference = 'Stop'
$utf8 = [Text.UTF8Encoding]::new($false)
$log = 'C:\goldclub\var\log\restore-sas-quarantine.log'
$hold = 'C:\goldclub\var\state\ruleta-compat-hold.json'
$lic = 'C:\goldclub\config\Licences\37A55022DCBEF351AE27471D181B1EF5.xml'
$dll = 'C:\goldclub\ruleta\licence.dll'

function L([string]$m) {
    $line = '[{0}] {1}' -f (Get-Date -Format o), $m
    Write-Host $line
    try { [IO.File]::AppendAllText($log, $line + [Environment]::NewLine, $utf8) } catch {}
}

function Sha1([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return '' }
    return (Get-FileHash -LiteralPath $path -Algorithm SHA1).Hash
}

function Write-Hold {
    $dir = Split-Path -Parent $hold
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $body = @{
        holdStart = $true
        liveRuleta = '10.1'
        signedSasPaytableIds = @('paytable_elite_double_zero', 'paytable_premium_double_zero')
        reason = 'Ruleta 10.1 cannot load signed SAS paytable paytable_elite_double_zero, paytable_premium_double_zero (PutRemoteThemeAndCombo bad conversion).'
        bypass = 'Need licensed 10.2 matching this dongle, or GoldClub-signed DeviceManager with paytable_double_zero. Do not unsigned-patch the MAC.'
    } | ConvertTo-Json
    [IO.File]::WriteAllText($hold, $body, $utf8)
    L 'wrote ruleta-compat-hold'
}

$map = @(
    @{ Src = 'SASControler1_DeviceManagerData.xml_1'; Dst = 'C:\goldclub\ruleta\var\SASControler1\DeviceManagerData.xml_1' },
    @{ Src = 'SASControler1_DeviceManagerData.xml_2'; Dst = 'C:\goldclub\ruleta\var\SASControler1\DeviceManagerData.xml_2' },
    @{ Src = 'MeterHost_DeviceManagerData.xml_1'; Dst = 'C:\goldclub\services\aurum\var\MeterHost\DeviceManagerData.xml_1' },
    @{ Src = 'MeterHost_DeviceManagerData.xml_2'; Dst = 'C:\goldclub\services\aurum\var\MeterHost\DeviceManagerData.xml_2' }
)

if (-not (Test-Path -LiteralPath $QuarantineDir)) {
    L ('FAIL missing quarantine {0}' -f $QuarantineDir)
    exit 2
}

L ('start whoami={0} from {1}' -f (whoami), $QuarantineDir)
$licBefore = Sha1 $lic
$dllBefore = Sha1 $dll

foreach ($n in @('GoldClub.Aurum.Services', 'GoldClub Serial Communication Gateway SAS')) {
    $svc = Get-Service | Where-Object { $_.Name -eq $n -or $_.DisplayName -eq $n } | Select-Object -First 1
    if (-not $svc) { continue }
    try {
        Stop-Service -InputObject $svc -Force -ErrorAction Stop
        L ('stopped {0}' -f $svc.Name)
    } catch {
        L ('stop fail {0}: {1}' -f $n, $_.Exception.Message)
    }
}
Start-Sleep -Seconds 2

$ok = 0
foreach ($p in $map) {
    $src = Join-Path $QuarantineDir $p.Src
    if (-not (Test-Path -LiteralPath $src)) { L ('missing {0}' -f $src); continue }
    $len = (Get-Item -LiteralPath $src).Length
    if ($len -lt 1000) { L ('skip tiny {0} ({1})' -f $src, $len); continue }
    $parent = Split-Path -Parent $p.Dst
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    attrib.exe -R $p.Dst 2>$null | Out-Null
    Copy-Item -LiteralPath $src -Destination $p.Dst -Force
    $got = (Get-Item -LiteralPath $p.Dst).Length
    if ($got -ne $len) { L ('FAIL size {0} {1}!={2}' -f $p.Dst, $got, $len); exit 3 }
    L ('restored {0} ({1} bytes)' -f $p.Dst, $got)
    $ok++
}

foreach ($n in @('GoldClub Serial Communication Gateway SAS', 'GoldClub.Aurum.Services')) {
    $svc = Get-Service | Where-Object { $_.Name -eq $n -or $_.DisplayName -eq $n } | Select-Object -First 1
    if (-not $svc) { continue }
    try {
        Start-Service -InputObject $svc -ErrorAction Stop
        L ('started {0}' -f $svc.Name)
    } catch {
        L ('start fail {0}: {1}' -f $n, $_.Exception.Message)
    }
}

Write-Hold
if ((Sha1 $lic) -ne $licBefore -or (Sha1 $dll) -ne $dllBefore) {
    L 'FAIL licence changed'
    exit 4
}
if ($ok -lt 4) {
    L ('FAIL restored {0}/4' -f $ok)
    exit 2
}
L 'done OK - signed DeviceManager restored (still 10.2 names; hold stays)'
exit 0
