# Map SAS 10.2 paytable host ids onto names Ruleta 10.1 can load.
# Does NOT swap in 10.2 binaries (that freeze ERROR 30).
# Does NOT rewrite DeviceManagerData XML (16-byte MAC -> DATA TAMPERED).
# Does not touch licences or serialport layout/locations.
param(
    [switch]$FilesOnly,
    [switch]$Force
)
$ErrorActionPreference = 'Continue'
$utf8 = [Text.UTF8Encoding]::new($false)
$log = 'C:\goldclub\var\log\fix-crashloop.log'
$done = 'C:\goldclub\bin\fix-crashloop.done'

function L([string]$m) {
    $line = '[{0}] {1}' -f (Get-Date -Format o), $m
    try { [IO.File]::AppendAllText($log, $line + [Environment]::NewLine, $utf8) } catch {}
}

function Restore-SignedDeviceManager {
    $bak = 'D:\ConfigScanner\backup-devicemgr-20260818'
    $pairs = @(
        @{ Src = (Join-Path $bak 'gm2au_DeviceManagerData.xml_1'); Dst = 'C:\goldclub\ruleta\var\gm2au\DeviceManagerData.xml_1' },
        @{ Src = (Join-Path $bak 'gm2au_DeviceManagerData.xml_2'); Dst = 'C:\goldclub\ruleta\var\gm2au\DeviceManagerData.xml_2' },
        @{ Src = (Join-Path $bak 'SASControler1_DeviceManagerData.xml_1'); Dst = 'C:\goldclub\ruleta\var\SASControler1\DeviceManagerData.xml_1' },
        @{ Src = (Join-Path $bak 'SASControler1_DeviceManagerData.xml_2'); Dst = 'C:\goldclub\ruleta\var\SASControler1\DeviceManagerData.xml_2' }
    )
    $n = 0
    foreach ($p in $pairs) {
        if (-not (Test-Path -LiteralPath $p.Src)) { L ('missing backup {0}' -f $p.Src); continue }
        try {
            $srcBytes = [IO.File]::ReadAllBytes($p.Src)
            $srcText = [Text.Encoding]::GetEncoding(1252).GetString($srcBytes)
            $liveVer = ''
            try {
                $liveVer = [Diagnostics.FileVersionInfo]::GetVersionInfo('C:\goldclub\ruleta\Ruleta.exe').ProductVersion
            } catch {}
            if ($liveVer.StartsWith('10.1') -and ($srcText -match 'paytable_elite_double_zero|paytable_premium_double_zero')) {
                L ('skip restore {0}: backup still has 10.2 SAS paytable names (would crash 10.1)' -f $p.Dst)
                continue
            }
            attrib.exe -R $p.Dst 2>$null | Out-Null
            Copy-Item -LiteralPath $p.Src -Destination $p.Dst -Force
            L ('restored signed {0} ({1} bytes)' -f $p.Dst, (Get-Item -LiteralPath $p.Dst).Length)
            $n++
        } catch {
            L ('restore fail {0}: {1}' -f $p.Dst, $_.Exception.Message)
        }
    }
    return $n
}

function Patch-AurumSetup([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { L ('missing AurumSetup {0}' -f $path); return $false }
    try {
        $t = [IO.File]::ReadAllText($path, $utf8)
        $n = $t.Replace('paytable_elite_double_zero', 'paytable_double_zero')
        $n = $n.Replace('paytable_premium_double_zero', 'paytable_premium')
        if ($n -eq $t) { L ('AurumSetup already mapped {0}' -f $path); return $true }
        [IO.File]::WriteAllText($path, $n, $utf8)
        L ('patched AurumSetup paytableId {0}' -f $path)
        return $true
    } catch {
        L ('AurumSetup fail: {0}' -f $_.Exception.Message)
        return $false
    }
}

function Disable-Ruleta102PaytableJson([string]$folder) {
    if (-not (Test-Path -LiteralPath $folder)) { return }
    foreach ($name in @('paytable_elite_double_zero.json', 'paytable_premium_double_zero.json')) {
        $p = Join-Path $folder $name
        if (-not (Test-Path -LiteralPath $p)) { continue }
        $dest = $p + '.disabled-101'
        try {
            if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Force }
            Rename-Item -LiteralPath $p -NewName ([IO.Path]::GetFileName($dest))
            L ('quarantined {0}' -f $name)
        } catch {
            L ('quarantine fail {0}: {1}' -f $name, $_.Exception.Message)
        }
    }
}

function Enable-Ruleta102PaytableJson([string]$folder) {
    if (-not (Test-Path -LiteralPath $folder)) { return }
    Get-ChildItem -LiteralPath $folder -Filter '*.disabled-101' -ErrorAction SilentlyContinue | ForEach-Object {
        $destName = $_.Name -replace '\.disabled-101$', ''
        $dest = Join-Path $folder $destName
        try {
            if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Force }
            Rename-Item -LiteralPath $_.FullName -NewName $destName
            L ('restored {0}' -f $destName)
        } catch {
            L ('restore paytable fail {0}: {1}' -f $_.Name, $_.Exception.Message)
        }
    }
}

function Test-FileHas102PaytableIds([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    try {
        $t = [IO.File]::ReadAllText($path, $utf8)
        return [bool]($t -match 'paytable_elite_double_zero|paytable_premium_double_zero')
    } catch { return $false }
}

function Restore-Ruleta102WritableConfig {
    # Undo the 10.1 remap so 10.2 exe + signed SAS names match again.
    $combo = 'C:\goldclub\ruleta\var\SASControler1\GCC_RT_330106_01_combo.dat'
    $comboBak = $combo + '.bak-20260818'
    $aurum = 'C:\goldclub\services\aurum\config\AurumSetup.xml'
    $aurumBak = $aurum + '.bak-102names'
    $ok = $false
    try {
        if (Test-FileHas102PaytableIds $combo) {
            L 'combo already has 10.2 paytable ids'
            $ok = $true
        } elseif (Test-Path -LiteralPath $comboBak) {
            if (Test-Path -LiteralPath $combo) {
                Copy-Item -LiteralPath $combo -Destination ($combo + '.bak-101remap') -Force
            }
            Copy-Item -LiteralPath $comboBak -Destination $combo -Force
            L 'combo restored from bak-20260818 (10.2 paytable ids)'
            $ok = $true
        } else {
            L 'missing combo bak-20260818'
        }
    } catch {
        L ('combo 10.2 restore fail: {0}' -f $_.Exception.Message)
    }
    try {
        if (Test-FileHas102PaytableIds $aurum) {
            L 'AurumSetup already has 10.2 paytable ids'
            $ok = $true
        } elseif (Test-Path -LiteralPath $aurumBak) {
            if (Test-Path -LiteralPath $aurum) {
                Copy-Item -LiteralPath $aurum -Destination ($aurum + '.bak-101remap') -Force
            }
            Copy-Item -LiteralPath $aurumBak -Destination $aurum -Force
            L 'AurumSetup restored from bak-102names'
            $ok = $true
        } else {
            L 'missing AurumSetup bak-102names'
        }
    } catch {
        L ('Aurum 10.2 restore fail: {0}' -f $_.Exception.Message)
    }
    Enable-Ruleta102PaytableJson 'C:\goldclub\config\etc\application\ruleta\paytables'
    Enable-Ruleta102PaytableJson 'C:\goldclub\bios\etc\application\ruleta\paytables'
    return $ok
}

function Test-SignedSasHas102Paytable {
    $paths = @(
        'C:\goldclub\ruleta\var\SASControler1\DeviceManagerData.xml_1',
        'C:\goldclub\ruleta\var\SASControler1\DeviceManagerData.xml_2'
    )
    foreach ($p in $paths) {
        if (-not (Test-Path -LiteralPath $p)) { continue }
        try {
            $raw = [IO.File]::ReadAllBytes($p)
        } catch { continue }
        if ($raw.Length -lt 20) { continue }
        $text = [Text.Encoding]::GetEncoding(1252).GetString($raw)
        if ($text -match 'paytable_elite_double_zero|paytable_premium_double_zero') {
            return $true
        }
    }
    return $false
}

function Write-RuletaCompatHold([string]$reason) {
    $dir = 'C:\goldclub\var\state'
    $path = Join-Path $dir 'ruleta-compat-hold.json'
    try {
        if (-not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
        $body = '{{"holdStart":true,"reason":"{0}"}}' -f ($reason -replace '"', "'")
        [IO.File]::WriteAllText($path, $body, $utf8)
        L ('wrote hold {0}' -f $path)
    } catch {
        L ('hold write fail: {0}' -f $_.Exception.Message)
    }
}

function Test-LiveRuletaIs10_2 {
    $exe = 'C:\goldclub\ruleta\Ruleta.exe'
    if (-not (Test-Path -LiteralPath $exe)) { return $false }
    try {
        $v = [string][Diagnostics.FileVersionInfo]::GetVersionInfo($exe).ProductVersion
        return ($v -match '^10\.2(\.|$)')
    } catch { return $false }
}

function Set-RuletaCompatHoldForLiveExe {
    $sas102 = Test-SignedSasHas102Paytable
    $exe102 = Test-LiveRuletaIs10_2
    if ($sas102 -and -not $exe102) {
        Write-RuletaCompatHold '10.1 exe + 10.2 SAS paytable - hold start'
        return $true
    }
    Clear-RuletaCompatHold
    if ($sas102 -and $exe102) {
        L 'SAS still has 10.2 paytable ids; Ruleta.exe is already 10.2 - no hold'
    }
    return $false
}

function Clear-RuletaCompatHold {
    $path = 'C:\goldclub\var\state\ruleta-compat-hold.json'
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
        L 'cleared ruleta-compat-hold'
    }
}

function Patch-ComboDat([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { L ('missing combo {0}' -f $path); return $false }
    $bak = $path + '.bak-gr0072'
    try {
        if (Test-Path -LiteralPath $bak) {
            Copy-Item -LiteralPath $bak -Destination $path -Force
            L ('combo restored from bak-gr0072 (keep GR0072, EgmPaytableId paytable_double_zero)')
        } else {
            $t = [IO.File]::ReadAllText($path, $utf8)
            $n = $t.Replace('paytable_elite_double_zero', 'paytable_double_zero')
            $n = $n.Replace('paytable_premium_double_zero', 'paytable_premium')
            if ($n -eq $t) { L 'combo.dat already mapped' }
            else {
                [IO.File]::WriteAllText($path, $n, $utf8)
                L ('patched combo.dat {0}' -f $path)
            }
        }
        $xml = New-Object xml
        $xml.Load($path)
        if ([string]$xml.GamesL.LastGameNr -ne '7') {
            $xml.GamesL.LastGameNr = '7'
            $xml.Save($path)
            L 'combo LastGameNr=7 (GR0072 paytable_double_zero)'
        }
        return $true
    } catch {
        L ('combo fail: {0}' -f $_.Exception.Message)
        return $false
    }
}

L ('start whoami={0} FilesOnly={1} Force={2}' -f (whoami), [bool]$FilesOnly, [bool]$Force)
# Always restore files. The done flag only skips the service bounce so a
# later boot still replaces 223-byte SAS DeviceManager stubs.
if ((-not $Force) -and (-not $FilesOnly) -and (Test-Path -LiteralPath $done)) {
    L 'done flag present - files still run, skip service bounce'
    $FilesOnly = $true
}

$comboOk = $false
$aurumOk = $false
if (Test-LiveRuletaIs10_2) {
    L 'Live Ruleta is 10.2 - skip 10.1 remap; restore writable 10.2 names'
    $comboOk = Restore-Ruleta102WritableConfig
    $aurumOk = $comboOk
} else {
    $comboOk = Patch-ComboDat 'C:\goldclub\ruleta\var\SASControler1\GCC_RT_330106_01_combo.dat'
    $aurumOk = Patch-AurumSetup 'C:\goldclub\services\aurum\config\AurumSetup.xml'
    [void](Patch-AurumSetup 'C:\goldclub\config\etc\application\aurum\AurumSetup.xml')
    Disable-Ruleta102PaytableJson 'C:\goldclub\config\etc\application\ruleta\paytables'
    Disable-Ruleta102PaytableJson 'C:\goldclub\bios\etc\application\ruleta\paytables'
}
$okFiles = Restore-SignedDeviceManager
[void](Set-RuletaCompatHoldForLiveExe)

if (-not $FilesOnly) {
    foreach ($svc in @('GoldClub.Aurum.Services')) {
        try {
            Stop-Service -Name $svc -Force -ErrorAction Stop
            L ('stopped service {0}' -f $svc)
        } catch {
            L ('service stop {0}: {1}' -f $svc, $_.Exception.Message)
        }
    }
    Start-Sleep -Seconds 2
    foreach ($svc in @('GoldClub.Aurum.Services')) {
        try {
            Start-Service -Name $svc -ErrorAction Stop
            L ('started service {0}' -f $svc)
        } catch {
            L ('service start {0}: {1}' -f $svc, $_.Exception.Message)
        }
    }
    $kill = @('Ruleta.exe', 'godot.exe', 'Godot_v4.exe')
    if (Set-RuletaCompatHoldForLiveExe) {
        $kill += 'HIH.exe'
        L 'HOLD START: 10.1 exe + 10.2 SAS paytable; stopping HIH crash loop'
    }
    foreach ($im in $kill) {
        & cmd.exe /c ('taskkill /F /T /IM {0} 1>nul 2>nul' -f $im) | Out-Null
        L ('taskkill {0} exit={1}' -f $im, $LASTEXITCODE)
    }
}

if ($comboOk -or $aurumOk) {
    try { [IO.File]::WriteAllText($done, (Get-Date -Format o), $utf8) } catch {}
}
L ('done filesOk={0} comboOk={1}' -f $okFiles, $comboOk)
exit 0
