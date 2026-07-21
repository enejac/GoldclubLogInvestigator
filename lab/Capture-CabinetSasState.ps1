#Requires -Version 5.1
<#
.SYNOPSIS
    Capture SAS stack state from a lab cabinet for before/after comparison.

.DESCRIPTION
    Uses sc.exe (remote services), SMB admin share (logs/config), and WinRM when
    TrustedHosts allows (process + TCP). Does NOT require PsExec.

    COM4 "Access denied" on the HOST PC is unrelated to SMB C$ on the cabinet:
      - SMB \\<ip>\c$  = network file access to the CABINET disk
      - COM4 on HOST   = local USB/serial on YOUR workstation; denied means another
        app holds the port (IGT SASTest, terminal, inject script) or a driver issue

.EXAMPLE
    .\Capture-CabinetSasState.ps1 -IP 10.0.0.171 -Label before

.EXAMPLE
    .\Capture-CabinetSasState.ps1 -IP 10.0.0.171 -Label after

.EXAMPLE
    .\Capture-CabinetSasState.ps1 -Compare baseline.json after.json

.EXAMPLE
    .\Capture-CabinetSasState.ps1 -ReferenceIP 10.0.0.90 -TargetIP 10.0.0.171 -OutDir aft\investigations
#>
[CmdletBinding(DefaultParameterSetName = 'Capture')]
param(
    [Parameter(ParameterSetName = 'Capture', Mandatory)]
    [Alias('CabinetIp')]
    [string] $IP,

    [Parameter(ParameterSetName = 'Capture')]
    [ValidateSet('before', 'after', 'reference', 'enabled', 'current')]
    [string] $Label = 'before',

    [Parameter(ParameterSetName = 'Capture')]
    [string] $OutDir,

    [Parameter(ParameterSetName = 'CompareTwo')]
    [string] $CompareOutDir,

    [Parameter(ParameterSetName = 'Capture')]
    [string] $OutBaseName,

    [Parameter(ParameterSetName = 'Compare', Mandatory)]
    [string[]] $Compare,

    [Parameter(ParameterSetName = 'CompareTwo', Mandatory)]
    [string] $ReferenceIP,

    [Parameter(ParameterSetName = 'CompareTwo', Mandatory)]
    [string] $TargetIP
)

$ErrorActionPreference = 'Continue'
$RepoRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Definition }
if (-not $OutDir) {
    $OutDir = Join-Path $RepoRoot 'aft\investigations'
}

. "$RepoRoot\LabAccess.ps1"
if (Test-Path -LiteralPath "$RepoRoot\LabRemoteTransport.ps1") {
    . "$RepoRoot\LabRemoteTransport.ps1"
}

function Limit-TextLines {
    param(
        [array] $Lines,
        [int] $MaxLen = 400
    )
    @($Lines | ForEach-Object {
        $s = [string]$_
        if ($s.Length -gt $MaxLen) { $s.Substring(0, $MaxLen) + '...' }
        else { $s }
    })
}

function Write-AsciiJson {
    param(
        [object] $Object,
        [string] $Path,
        [int] $Depth = 8
    )
    $json = $Object | ConvertTo-Json -Depth $Depth -Compress
    [System.IO.File]::WriteAllText($Path, $json, [System.Text.Encoding]::ASCII)
}

function Get-IpSlug {
    param([string] $Ip)
    ($Ip -split '\.')[-1]
}

function Get-CaptureStamp {
    (Get-Date).ToString('yyyyMMdd-HHmmss')
}

function Get-ServiceStates {
    param([string] $Ip)
    $filterPattern = '(?i)goldclub|sas|commctrl|aurum|serial communication'
    $raw = sc.exe "\\$Ip" query type= service state= all 2>&1 | Out-String
    $services = @()
    $blocks = $raw -split '(?=SERVICE_NAME:)'
    foreach ($block in $blocks) {
        if ($block -notmatch 'SERVICE_NAME:\s+(.+?)\r?\n') { continue }
        $name = $Matches[1].Trim()
        if ($name -notmatch $filterPattern) { continue }
        $state = if ($block -match 'STATE\s+:\s+\d+\s+(\w+)') { $Matches[1] } else { 'UNKNOWN' }
        $services += [ordered]@{
            Name    = $name
            State   = $state
            Running = ($state -eq 'RUNNING')
        }
    }
    [pscustomobject]@{
        Method   = 'sc.exe \\ip query type= service (single pass, no PsExec)'
        Services = @($services)
    }
}

function Get-KeyConfigPaths {
    @(
        'slot\themes\mgconfig.xml',
        'services\aurum\config\SASControler1\SASsetupData.xml',
        'services\aurum\AurumServicesConfig.xml',
        'services\aurum\config\AurumSetup.xml',
        'services\CommCtrlSAS\CommControler.ini',
        'services\CommCtrl\CommControler.ini',
        'var\run\taskhost.1\CommCtrlSAS.xml',
        'var\run\taskhost.1\CommCtrl.xml',
        'var\run\taskhost.1\GoldClub.Aurum.Services.xml'
    )
}

function Get-XmlCapture {
    param([string] $Ip)
    $items = @()
    foreach ($rel in (Get-KeyConfigPaths)) {
        $p = "\\$Ip\c$\Goldclub\$rel"
        if (-not (Test-Path -LiteralPath $p)) {
            $items += [ordered]@{ RelativePath = $rel; Exists = $false }
            continue
        }
        $fi = Get-Item -LiteralPath $p
        $highlights = @()
        if ($rel -notmatch 'mgconfig\.xml$') {
            try {
                $highlights = Select-String -LiteralPath $p -Pattern '(?i)SAS|channel|CommCtrl|COM\d+|5001[01]|GST\d+|GCMessenger|communications|Enabled|921600|31100|31150|40000|OwnerHostId|LastConfigurationChange' -ErrorAction SilentlyContinue |
                    Select-Object -First 35 | ForEach-Object { $_.Line.Trim() }
            }
            catch { }
        }
        $items += [ordered]@{
            RelativePath = $rel
            FullPath     = $p
            Exists       = $true
            SizeBytes    = $fi.Length
            LastWriteUtc = $fi.LastWriteTimeUtc.ToString('o')
            Sha256       = (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash
            Highlights   = $highlights
        }
    }
    $items
}

function Get-AurumSasSummary {
    param([string] $Ip)
    $p = "\\$Ip\c$\Goldclub\services\aurum\config\AurumSetup.xml"
    if (-not (Test-Path -LiteralPath $p)) { return $null }
    [xml]$doc = Get-Content -LiteralPath $p
    $nsUri = 'http://tempuri.org/AurumConfiguration.xsd'
    $ns = New-Object System.Xml.XmlNamespaceManager($doc.NameTable)
    $ns.AddNamespace('a', $nsUri)
    $channelClasses = @('noteAcceptor', 'handpay', 'bonus', 'communications', 'voucher', 'WAT')
    $devices = @()
    foreach ($node in @($doc.SelectNodes('//a:EgmsDevices', $ns))) {
        $cls = [string]$node.DeviceClass
        if ($channelClasses -notcontains $cls) { continue }
        $devices += [ordered]@{
            DeviceClass             = $cls
            DeviceId                = [string]$node.DeviceId
            Enabled                 = [string]$node.Enabled
            OwnerHostId             = [string]$node.OwnerHostId
            LastConfigurationChange = [string]$node.LastConfigurationChange
            RequiredForPlay         = [string]$node.RequiredForPlay
            RestartStatus           = [string]$node.RestartStatus
        }
    }
    $hostCfg = $doc.SelectNodes('//a:Configs[a:GCMessengerId="SASControler1"]', $ns) | Select-Object -First 1
    [ordered]@{
        ChannelDevices = $devices
        SASControler1  = if ($hostCfg) {
            [ordered]@{
                Enabled          = [string]$hostCfg.Enabled
                ServiceURI       = [string]$hostCfg.ServiceURI
                HostHomePath     = [string]$hostCfg.HostHomePath
                ConfigurationId  = [string]$hostCfg.ConfigurationId
                HasWatAccounts   = [bool]$hostCfg.WatAccounts
            }
        }
        else { $null }
    }
}

function Get-RuntimeProbe {
    param([string] $Ip)
    $runtime = [ordered]@{
        Transport   = 'None'
        CommCtrlSAS = $null
        CommCtrl    = $null
        Tcp31100    = @()
        Tcp31150    = @()
        Tcp40000    = @()
        Error       = $null
    }
    if (-not (Get-Command Test-LabWinRmReachable -ErrorAction SilentlyContinue)) {
        $runtime.Transport = 'Unavailable'
        $runtime.Error = 'LabRemoteTransport not loaded'
        return [pscustomobject]$runtime
    }
    if (-not ((Test-LabWinRmReachable -Computer $Ip) -and (Test-LabTrustedHostConfigured -Computer $Ip))) {
        $runtime.Transport = 'Unavailable'
        $runtime.Error = 'WinRM not trusted/reachable; sc.exe + SMB only (PsExec not used)'
        return [pscustomobject]$runtime
    }
    try {
        $cred = Get-LabCredential
        $remote = Invoke-Command -ComputerName $Ip -Credential $cred -ScriptBlock {
            $tcp = @(Get-NetTCPConnection -LocalPort 31100, 31150, 40000 -ErrorAction SilentlyContinue |
                Select-Object LocalPort, State, RemotePort)
            [pscustomobject]@{
                CommCtrlSAS = [bool](Get-Process -Name CommCtrlSAS -ErrorAction SilentlyContinue)
                CommCtrl    = [bool](Get-Process -Name CommCtrl -ErrorAction SilentlyContinue)
                Tcp         = $tcp
            }
        } -ErrorAction Stop
        $runtime.Transport = 'WinRM'
        $runtime.CommCtrlSAS = $remote.CommCtrlSAS
        $runtime.CommCtrl = $remote.CommCtrl
        foreach ($t in @($remote.Tcp)) {
            $row = "$($t.State):$($t.RemotePort)"
            switch ([int]$t.LocalPort) {
                31100 { $runtime.Tcp31100 += $row }
                31150 { $runtime.Tcp31150 += $row }
                40000 { $runtime.Tcp40000 += $row }
            }
        }
    }
    catch {
        $runtime.Transport = 'WinRMFailed'
        $runtime.Error = $_.Exception.Message
    }
    return [pscustomobject]$runtime
}

function Get-LogCapture {
    param(
        [string] $Ip,
        [string] $Date
    )
    $base = "\\$Ip\c$\Goldclub\var\log"
    $paths = @(
        "GoldClub.Aurum.Services sasmsgr of SASControler1\$Date.log",
        "GoldClub.Aurum.Services SASControler1\$Date.log",
        "CommCtrlSAS\$Date.log",
        "CommCtrl\$Date.log",
        "SlotLog\$Date.log"
    )
    $logs = @()
    foreach ($rel in $paths) {
        $p = Join-Path $base $rel
        $entry = [ordered]@{
            Path      = $p
            Exists    = (Test-Path -LiteralPath $p)
            Tail      = @()
            LastLine  = $null
            PollLines = @()
        }
        if ($entry.Exists) {
            $tail = @(Get-Content -LiteralPath $p -Tail 50 -ErrorAction SilentlyContinue)
            $entry.Tail = @(Limit-TextLines -Lines @($tail | Select-Object -Last 5))
            if ($tail.Count -gt 0) { $entry.LastLine = [string]$tail[-1] }
            if ($entry.LastLine.Length -gt 400) {
                $entry.LastLine = $entry.LastLine.Substring(0, 400) + '...'
            }
            $entry.PollLines = @(Limit-TextLines -Lines @($tail | Where-Object { $_ -match 'qGMID1:8[01]\s*$' } | Select-Object -Last 3))
        }
        $logs += $entry
    }

    $sasmsgrDir = Get-ChildItem -LiteralPath $base -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like '*sasmsgr*' } | Select-Object -First 1
    $poll = [ordered]@{
        SasmsgrLogDir = $(if ($sasmsgrDir) { $sasmsgrDir.FullName } else { $null })
        TodayLog      = $null
        Matches       = @()
        LastPollLine  = $null
    }
    if ($sasmsgrDir) {
        $today = Join-Path $sasmsgrDir.FullName "$Date.log"
        $poll.TodayLog = $today
        if (Test-Path -LiteralPath $today) {
            $tailPoll = @(Get-Content -LiteralPath $today -Tail 200 -ErrorAction SilentlyContinue |
                Where-Object { $_ -match 'qGMID1:(80|81)' })
            $poll.Matches = @(Limit-TextLines -Lines @($tailPoll | Select-Object -Last 10))
            if ($poll.Matches.Count -gt 0) { $poll.LastPollLine = $poll.Matches[-1] }
        }
    }
    [ordered]@{
        Logs         = $logs
        PollSignals  = $poll
    }
}

function Get-GstIdFromPayload {
    param($Payload)
    $gst = $null
    if ($Payload.AurumSas -and $Payload.AurumSas.SASControler1 -and $Payload.AurumSas.SASControler1.ServiceURI) {
        if ($Payload.AurumSas.SASControler1.ServiceURI -match 'GST(\d+)') { $gst = "GST$($Matches[1])" }
    }
    if (-not $gst) {
        foreach ($cfg in @($Payload.XmlConfigs)) {
            if ($cfg.Highlights) {
                $hit = @($cfg.Highlights | Where-Object { $_ -match 'GST\d+' } | Select-Object -First 1)
                if ($hit -and $hit -match '(GST\d+)') { $gst = $Matches[1]; break }
            }
        }
    }
    $gst
}

function Save-Capture {
    param(
        [string] $Ip,
        [string] $Label,
        [string] $BaseName
    )
    Initialize-LabSmbCredential -Ip @($Ip)
    Write-Host '  SMB...' -ForegroundColor DarkGray
    $smb = Test-LabSmbAccess -Ip $Ip
    $stamp = Get-CaptureStamp
    $date = (Get-Date).ToString('yyyy-MM-dd')
    Write-Host '  Services (sc.exe)...' -ForegroundColor DarkGray
    $svc = Get-ServiceStates -Ip $Ip
    Write-Host '  Logs...' -ForegroundColor DarkGray
    $logs = Get-LogCapture -Ip $Ip -Date $date
    $commLog = $logs.Logs | Where-Object { $_.Path -like '*CommCtrlSAS*' } | Select-Object -First 1
    $bridge311 = ($commLog.Tail -join ' ') -match '31100|31150'

    Write-Host '  Runtime...' -ForegroundColor DarkGray
    $runtime = Get-RuntimeProbe -Ip $Ip
    Write-Host '  Config files...' -ForegroundColor DarkGray
    $xmlConfigs = Get-XmlCapture -Ip $Ip
    Write-Host '  Aurum summary...' -ForegroundColor DarkGray
    $aurumSas = Get-AurumSasSummary -Ip $Ip
    $payload = [ordered]@{
        CapturedAtUtc = (Get-Date).ToUniversalTime().ToString('o')
        Label         = $Label
        CabinetIp     = $Ip
        Smb           = $smb
        ServiceQuery  = $svc
        Runtime       = $runtime
        XmlConfigs    = $xmlConfigs
        AurumSas      = $aurumSas
        Logs          = $logs
        Signals       = [ordered]@{
            SasmsgrLogDirExists  = [bool]$logs.PollSignals.SasmsgrLogDir
            HasQGMIDPollsToday   = ($logs.PollSignals.Matches.Count -gt 0)
            CommCtrlSasBridge311 = [bool]$bridge311
            AllKeyServicesRunning = -not @($svc.Services | Where-Object {
                $_.Name -match 'GoldClub Serial Communication Gateway SAS|GoldClub.Aurum.Services' -and -not $_.Running
            }).Count
        }
        Notes         = [ordered]@{
            ComPortVsSmb = @(
                'SMB C$ admin share = file access to CABINET disk over network.',
                'COM4 on HOST = local serial on YOUR workstation.',
                'com_port_blocked / PermissionError on COM4 = HOST port locked by another app or driver - NOT SMB.'
            )
            PsExec = 'This script does not use PsExec. sc.exe + SMB + optional WinRM only.'
        }
    }

    if ($BaseName) {
        $baseName = $BaseName
    }
    elseif ($Ip -eq '10.0.0.171' -and $Label -eq 'before') {
        $baseName = '171-sas-state-baseline'
    }
    else {
        $baseName = "{0}-sas-state-{1}-{2}" -f (Get-IpSlug $Ip), $Label, $stamp
    }

    if (-not (Test-Path -LiteralPath $OutDir)) {
        New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
    }

    Write-Host '  Writing JSON...' -ForegroundColor DarkGray
    $jsonPath = Join-Path $OutDir ($baseName + '.json')
    $mdPath = Join-Path $OutDir ($baseName + '.md')
    $gstId = Get-GstIdFromPayload -Payload $payload
    $jsonSafe = [ordered]@{
        CapturedAtUtc = $payload.CapturedAtUtc
        Label         = $payload.Label
        CabinetIp     = $payload.CabinetIp
        GstId         = $gstId
        Smb           = $payload.Smb
        ServiceQuery  = $payload.ServiceQuery
        Runtime       = $payload.Runtime
        XmlConfigs    = @($xmlConfigs | ForEach-Object {
            if ($_.Exists) {
                [ordered]@{
                    RelativePath = $_.RelativePath
                    Exists       = $true
                    SizeBytes    = $_.SizeBytes
                    LastWriteUtc = $_.LastWriteUtc
                    Sha256       = $_.Sha256
                }
            }
            else { [ordered]@{ RelativePath = $_.RelativePath; Exists = $false } }
        })
        AurumSas      = $payload.AurumSas
        LogSummary    = @($logs.Logs | ForEach-Object {
            [ordered]@{
                Path      = $_.Path
                Exists    = $_.Exists
                LastLine  = $_.LastLine
                PollLines = $_.PollLines
                Tail      = $_.Tail
            }
        })
        PollSignals   = $logs.PollSignals
        Signals       = $payload.Signals
        Notes         = $payload.Notes
    }
    Write-Host '  Serializing JSON...' -ForegroundColor DarkGray
    Write-AsciiJson -Object $jsonSafe -Path $jsonPath -Depth 8

    $md = @(
        "# Cabinet $Ip SAS state ($Label)",
        "",
        "Captured (UTC): $($payload.CapturedAtUtc)",
        "SMB: $($smb.Status) - $($smb.Detail)",
        "",
        "## Key signals",
        "- sasmsgr log dir exists: $($payload.Signals.SasmsgrLogDirExists)",
        "- qGMID1:80/81 today: $($payload.Signals.HasQGMIDPollsToday)",
        "- CommCtrlSAS log mentions 31100/31150: $($payload.Signals.CommCtrlSasBridge311)",
        "- Key SAS services running: $($payload.Signals.AllKeyServicesRunning)",
        "- Runtime transport: $($payload.Runtime.Transport)",
        ""
    )
    if ($null -ne $payload.Runtime.CommCtrlSAS) {
        $md += "- CommCtrlSAS process: $($payload.Runtime.CommCtrlSAS)"
        $md += "- TCP 31150: $($payload.Runtime.Tcp31150 -join ', ')"
        $md += "- TCP 31100: $($payload.Runtime.Tcp31100 -join ', ')"
        $md += ""
    }
    $md += "## Services ($($svc.Method))", ""
    foreach ($s in $svc.Services) {
        $md += "- $($s.Name): $($s.State)"
    }
    $md += "", "## Config files", ""
    foreach ($x in $payload.XmlConfigs) {
        if ($x.Exists) {
            $md += "- $($x.RelativePath): $($x.SizeBytes) bytes sha256=$($x.Sha256)"
        }
        else {
            $md += "- $($x.RelativePath): MISSING"
        }
    }
    if ($payload.AurumSas) {
        $md += "", "## Aurum SAS channel devices", ""
        foreach ($d in $payload.AurumSas.ChannelDevices) {
            $md += "- $($d.DeviceClass) id=$($d.DeviceId) enabled=$($d.Enabled) ownerHost=$($d.OwnerHostId) lcc=$($d.LastConfigurationChange)"
        }
        $md += "- SASControler1 Enabled=$($payload.AurumSas.SASControler1.Enabled) configId=$($payload.AurumSas.SASControler1.ConfigurationId) WatAccounts=$($payload.AurumSas.SASControler1.HasWatAccounts)"
    }
    $md += "", "## Poll signals", ""
    if ($logs.PollSignals.SasmsgrLogDir) {
        $md += "sasmsgr dir: $($logs.PollSignals.SasmsgrLogDir)"
        if ($logs.PollSignals.Matches.Count -gt 0) {
            $logs.PollSignals.Matches | ForEach-Object { $md += "- $_" }
        }
        else {
            $md += "- No qGMID1:80/81 in today log"
        }
    }
    else {
        $md += "- No sasmsgr log directory"
    }
    $md += "", "## COM4 vs SMB (HOST inject)", ""
    foreach ($n in $payload.Notes.ComPortVsSmb) { $md += "- $n" }
    $md -join "`n" | Out-File -LiteralPath $mdPath -Encoding ascii -Force

    Write-Host "[+] JSON: $jsonPath" -ForegroundColor Green
    Write-Host "[+] Summary: $mdPath" -ForegroundColor Green
    return [pscustomobject]@{ Json = $jsonPath; Markdown = $mdPath; Payload = $payload }
}

function Compare-CaptureFiles {
    param(
        [string] $BaselinePath,
        [string] $AfterPath,
        [string] $OutPath
    )
    $a = Get-Content -LiteralPath $BaselinePath -Raw | ConvertFrom-Json
    $b = Get-Content -LiteralPath $AfterPath -Raw | ConvertFrom-Json
    $lines = @(
        "# SAS state compare",
        "",
        "Baseline: $BaselinePath ($($a.Label) @ $($a.CabinetIp))",
        "After:    $AfterPath ($($b.Label) @ $($b.CabinetIp))",
        "",
        "## Signal deltas",
        ""
    )
    foreach ($k in @('SasmsgrLogDirExists', 'HasQGMIDPollsToday', 'CommCtrlSasBridge311', 'AllKeyServicesRunning')) {
        $av = $a.Signals.$k
        $bv = $b.Signals.$k
        $mark = if ($av -ne $bv) { ' CHANGED' } else { '' }
        $lines += "- $k`: $av -> $bv$mark"
    }
    $lines += "", "## Config SHA256 deltas", ""
    foreach ($cfgB in $b.XmlConfigs) {
        $cfgA = $a.XmlConfigs | Where-Object RelativePath -eq $cfgB.RelativePath | Select-Object -First 1
        if ($cfgA -and $cfgA.Exists -and $cfgB.Exists -and $cfgA.Sha256 -ne $cfgB.Sha256) {
            $lines += "- $($cfgB.RelativePath): CHANGED"
        }
    }
    $lines += "", "## Aurum channel device deltas", ""
    $classes = @('noteAcceptor', 'handpay', 'bonus', 'communications', 'voucher', 'WAT')
    foreach ($cls in $classes) {
        $da = @($a.AurumSas.ChannelDevices | Where-Object DeviceClass -eq $cls)
        $db = @($b.AurumSas.ChannelDevices | Where-Object DeviceClass -eq $cls)
        $sa = ($da | ForEach-Object { "en=$($_.Enabled)/oh=$($_.OwnerHostId)/lcc=$($_.LastConfigurationChange)" }) -join '; '
        $sb = ($db | ForEach-Object { "en=$($_.Enabled)/oh=$($_.OwnerHostId)/lcc=$($_.LastConfigurationChange)" }) -join '; '
        $mark = if ($sa -ne $sb) { ' CHANGED' } else { '' }
        $lines += "- ${cls}: [$sa] -> [$sb]$mark"
    }
    $lines -join "`n" | Out-File -LiteralPath $OutPath -Encoding ascii
    Write-Host "[+] Compare: $OutPath" -ForegroundColor Green
}

function Get-ExpectedMachineIdPaths {
    @(
        'slot\themes\mgconfig.xml',
        'services\aurum\AurumServicesConfig.xml'
    )
}

function Get-ChannelDeviceMap {
    param($Capture)
    $map = @{}
    if (-not $Capture.AurumSas) { return $map }
    foreach ($d in @($Capture.AurumSas.ChannelDevices)) {
        $key = if ($d.DeviceClass -eq 'bonus') { "bonus:$($d.DeviceId)" } else { [string]$d.DeviceClass }
        $map[$key] = $d
    }
    $map
}

function Get-TargetApplyAssessment {
    param($Target)
    $channels = @($Target.AurumSas.ChannelDevices | Where-Object {
        $_.DeviceClass -in @('noteAcceptor', 'handpay', 'bonus', 'voucher', 'WAT')
    })
    $assigned = @($channels | Where-Object { [string]$_.OwnerHostId -eq '1' }).Count
    $configured = @($channels | Where-Object { [string]$_.LastConfigurationChange -eq '2' }).Count
    $hasWat = [bool]$Target.AurumSas.SASControler1.HasWatAccounts
    $polls = [bool]$Target.Signals.HasQGMIDPollsToday
    $bridge = [bool]$Target.Signals.CommCtrlSasBridge311
    $sasmsgr = [bool]$Target.Signals.SasmsgrLogDirExists

    $score = 0
    if ($assigned -ge 5) { $score += 2 }
    if ($configured -ge 4) { $score += 2 }
    if ($hasWat) { $score += 2 }
    if ($sasmsgr) { $score += 1 }
    if ($polls) { $score += 2 }
    if ($bridge) { $score += 2 }

    $state = if ($score -ge 9) { 'post-Apply (transfer-ready)' }
    elseif ($score -ge 4) { 'partial-Apply (channels assigned, runtime not live)' }
    else { 'pre-Apply (channels unassigned)' }

    [ordered]@{
        State                  = $state
        Score                  = $score
        ChannelsOwnerHostId1   = $assigned
        ChannelsLcc2           = $configured
        HasWatAccounts         = $hasWat
        SasmsgrLogDirExists    = $sasmsgr
        HasQGMIDPollsToday     = $polls
        CommCtrlSasBridge311   = $bridge
    }
}

function Compare-EgmCaptures {
    param(
        $Reference,
        $Target,
        [string] $OutDir
    )
    $refIp = $Reference.CabinetIp
    $tgtIp = $Target.CabinetIp
    $expectedPaths = Get-ExpectedMachineIdPaths
    $channelClasses = @('noteAcceptor', 'handpay', 'bonus', 'voucher', 'WAT', 'communications')
    $actionable = @()
    $expected = @()

    foreach ($k in @('SasmsgrLogDirExists', 'HasQGMIDPollsToday', 'CommCtrlSasBridge311', 'AllKeyServicesRunning')) {
        $rv = $Reference.Signals.$k
        $tv = $Target.Signals.$k
        if ($rv -ne $tv) {
            $actionable += [ordered]@{
                Category = 'runtime_signal'
                Item     = $k
                Reference = $rv
                Target    = $tv
                Action   = "Target needs $k=$rv to match reference transfer-ready state"
            }
        }
    }

    foreach ($cfgT in @($Target.XmlConfigs)) {
        $cfgR = @($Reference.XmlConfigs | Where-Object RelativePath -eq $cfgT.RelativePath | Select-Object -First 1)
        if (-not $cfgR) { continue }
        $same = ($cfgR.Exists -eq $cfgT.Exists) -and ((-not $cfgR.Exists) -or ($cfgR.Sha256 -eq $cfgT.Sha256))
        if ($same) { continue }
        $bucket = if ($expectedPaths -contains $cfgT.RelativePath) { 'expected' } else { 'actionable' }
        $entry = [ordered]@{
            Category       = 'config_file'
            Item           = $cfgT.RelativePath
            RelativePath   = $cfgT.RelativePath
            Reference      = if ($cfgR.Exists) { $cfgR.Sha256 } else { 'MISSING' }
            Target         = if ($cfgT.Exists) { $cfgT.Sha256 } else { 'MISSING' }
            Classification = if ($bucket -eq 'expected') { 'machine_id_expected' } else { 'config_gap' }
            Action         = if ($bucket -eq 'expected') {
                'Expected machine/game identity diff; ignore for SAS Apply'
            }
            else {
                'Review config gap vs reference transfer-ready cabinet'
            }
        }
        if ($bucket -eq 'expected') { $expected += $entry } else { $actionable += $entry }
    }

    $refMap = Get-ChannelDeviceMap -Capture $Reference
    $tgtMap = Get-ChannelDeviceMap -Capture $Target
    $xmlDeltas = @()
    foreach ($cls in $channelClasses) {
        $keys = if ($cls -eq 'bonus') {
            @($refMap.Keys + $tgtMap.Keys | Where-Object { $_ -like 'bonus:*' } | Select-Object -Unique)
        }
        else { @($cls) }
        foreach ($key in $keys) {
            $da = $refMap[$key]
            $db = $tgtMap[$key]
            if (-not $da -and -not $db) { continue }
            $delta = [ordered]@{
                DeviceKey = $key
                Reference = if ($da) {
                    [ordered]@{
                        Enabled = $da.Enabled; OwnerHostId = $da.OwnerHostId
                        LastConfigurationChange = $da.LastConfigurationChange
                    }
                } else { $null }
                Target = if ($db) {
                    [ordered]@{
                        Enabled = $db.Enabled; OwnerHostId = $db.OwnerHostId
                        LastConfigurationChange = $db.LastConfigurationChange
                    }
                } else { $null }
            }
            $changed = ($da.Enabled -ne $db.Enabled) -or ($da.OwnerHostId -ne $db.OwnerHostId) -or ($da.LastConfigurationChange -ne $db.LastConfigurationChange)
            if ($changed) {
                $delta.Classification = 'config_gap'
                $xmlDeltas += $delta
                if ($db.OwnerHostId -ne $da.OwnerHostId -or $db.LastConfigurationChange -ne $da.LastConfigurationChange) {
                    $actionable += [ordered]@{
                        Category = 'aurum_channel'
                        Item     = $key
                        Reference = "oh=$($da.OwnerHostId) lcc=$($da.LastConfigurationChange)"
                        Target    = "oh=$($db.OwnerHostId) lcc=$($db.LastConfigurationChange)"
                        Action   = 'Apply SAS channel functionality on target (OwnerHostId=1, LCC=2)'
                    }
                }
            }
        }
    }

    $refSvc = @($Reference.ServiceQuery.Services | Sort-Object Name)
    $tgtSvc = @($Target.ServiceQuery.Services | Sort-Object Name)
    $svcDiff = @()
    foreach ($s in $tgtSvc) {
        $r = @($refSvc | Where-Object Name -eq $s.Name | Select-Object -First 1)
        if ($r -and $r.State -ne $s.State) {
            $svcDiff += [ordered]@{ Name = $s.Name; Reference = $r.State; Target = $s.State }
            $actionable += [ordered]@{
                Category = 'service'
                Item     = $s.Name
                Reference = $r.State
                Target    = $s.State
                Action   = "Start or fix service $($s.Name) on target"
            }
        }
    }

    $refWat = [bool]$Reference.AurumSas.SASControler1.HasWatAccounts
    $tgtWat = [bool]$Target.AurumSas.SASControler1.HasWatAccounts
    if ($refWat -ne $tgtWat) {
        $actionable += [ordered]@{
            Category = 'aurum_transfer'
            Item     = 'WatAccounts'
            Reference = $refWat
            Target    = $tgtWat
            Action   = 'Enable WAT accounts block in AurumSetup (SAS channel Apply)'
        }
    }

    $refCfgId = [string]$Reference.AurumSas.SASControler1.ConfigurationId
    $tgtCfgId = [string]$Target.AurumSas.SASControler1.ConfigurationId
    if ($refCfgId -ne $tgtCfgId) {
        $actionable += [ordered]@{
            Category = 'aurum_transfer'
            Item     = 'ConfigurationId'
            Reference = $refCfgId
            Target    = $tgtCfgId
            Action   = 'ConfigurationId advances after SAS channel Apply on reference'
        }
    }

    $apply = Get-TargetApplyAssessment -Target $Target
    $top5 = @($actionable | Select-Object -First 5)

    $jsonOut = [ordered]@{
        GeneratedAtUtc = (Get-Date).ToUniversalTime().ToString('o')
        Reference      = [ordered]@{
            CabinetIp = $refIp
            GstId     = $Reference.GstId
            Label     = $Reference.Label
            CapturedAtUtc = $Reference.CapturedAtUtc
            JsonPath  = '90-egm-enabled-state.json'
        }
        Target         = [ordered]@{
            CabinetIp = $tgtIp
            GstId     = $Target.GstId
            Label     = $Target.Label
            CapturedAtUtc = $Target.CapturedAtUtc
            JsonPath  = '171-egm-current-state.json'
        }
        TargetApplyAssessment = $apply
        Services       = [ordered]@{
            Reference = $refSvc
            Target    = $tgtSvc
            Differences = $svcDiff
        }
        ConfigFiles    = [ordered]@{
            Reference = @($Reference.XmlConfigs)
            Target    = @($Target.XmlConfigs)
        }
        XmlElementDeltas = $xmlDeltas
        Runtime        = [ordered]@{
            Reference = $Reference.Runtime
            Target    = $Target.Target
        }
        LogSignals     = [ordered]@{
            ReferencePollSignals = $Reference.PollSignals
            TargetPollSignals    = $Target.PollSignals
            ReferenceSignals     = $Reference.Signals
            TargetSignals        = $Target.Signals
        }
        ExpectedMachineIdDiffs = $expected
        ActionableDiffs        = $actionable
        Top5Actionable         = $top5
    }
    # fix Runtime.Target typo
    $jsonOut.Runtime.Target = $Target.Runtime

    $md = @(
        "# EGM comparison: $refIp (reference) vs $tgtIp (target)",
        "",
        "Generated (UTC): $($jsonOut.GeneratedAtUtc)",
        "Reference capture: $($Reference.CapturedAtUtc) label=$($Reference.Label) gst=$($Reference.GstId)",
        "Target capture:    $($Target.CapturedAtUtc) label=$($Target.Label) gst=$($Target.GstId)",
        "",
        "## Target Apply assessment",
        "",
        "**$($apply.State)** (score $($apply.Score)/11)",
        "",
        "- Channels with OwnerHostId=1: $($apply.ChannelsOwnerHostId1) (reference expects 5+)",
        "- Channels with LastConfigurationChange=2: $($apply.ChannelsLcc2) (reference expects 4+)",
        "- WatAccounts present: $($apply.HasWatAccounts)",
        "- sasmsgr log dir: $($apply.SasmsgrLogDirExists)",
        "- qGMID1:80/81 polls today: $($apply.HasQGMIDPollsToday)",
        "- CommCtrlSAS 31100/31150 bridge: $($apply.CommCtrlSasBridge311)",
        "",
        "## HEADLINE signals",
        "",
        "| Signal | .90 | .171 |",
        "|--------|-----|------|",
        "| Key SAS services running | $($Reference.Signals.AllKeyServicesRunning) | $($Target.Signals.AllKeyServicesRunning) |",
        "| sasmsgr log folder | $($Reference.Signals.SasmsgrLogDirExists) | $($Target.Signals.SasmsgrLogDirExists) |",
        "| qGMID1:80/81 polls today | $($Reference.Signals.HasQGMIDPollsToday) | $($Target.Signals.HasQGMIDPollsToday) |",
        "| CommCtrlSAS 31100/31150 bridge | $($Reference.Signals.CommCtrlSasBridge311) | $($Target.Signals.CommCtrlSasBridge311) |",
        "",
        "## Top 5 actionable differences",
        ""
    )
    $i = 1
    foreach ($a in $top5) {
        $md += "$i. **$($a.Category)/$($a.Item)**: ref=$($a.Reference) target=$($a.Target) - $($a.Action)"
        $i++
    }
    if ($top5.Count -eq 0) { $md += "- None; target matches reference transfer-ready profile" }

    $md += "", "## Services", ""
    $md += "| Service | .90 | .171 |", "|---------|-----|------|"
    foreach ($s in $refSvc) {
        $t = @($tgtSvc | Where-Object Name -eq $s.Name | Select-Object -First 1)
        $ts = if ($t) { $t.State } else { 'MISSING' }
        $mark = if ($s.State -ne $ts) { ' **DIFF**' } else { '' }
        $md += "| $($s.Name) | $($s.State) | $ts$mark |"
    }

    $md += "", "## Config file SHA256", ""
    $md += "| Path | .90 | .171 | Same? |", "|------|-----|------|-------|"
    foreach ($cfgR in @($Reference.XmlConfigs)) {
        $cfgT = @($Target.XmlConfigs | Where-Object RelativePath -eq $cfgR.RelativePath | Select-Object -First 1)
        $same = ($cfgR.Exists -and $cfgT.Exists -and $cfgR.Sha256 -eq $cfgT.Sha256)
        $exp = if ((-not $same) -and ($expectedPaths -contains $cfgR.RelativePath)) { ' (machine-id expected)' } else { '' }
        $rHash = if ($cfgR.Exists) { $cfgR.Sha256.Substring(0, 8) + '...' } else { 'MISSING' }
        $tHash = if ($cfgT.Exists) { $cfgT.Sha256.Substring(0, 8) + '...' } else { 'MISSING' }
        $md += "| $($cfgR.RelativePath) | $rHash | $tHash | $(if ($same) { 'YES' } else { 'NO' })$exp |"
    }

    $md += "", "## Aurum SAS channel devices", ""
    $md += "| Device | .90 OH/LCC | .171 OH/LCC |", "|--------|------------|-------------|"
    foreach ($cls in @('noteAcceptor', 'handpay', 'bonus:0', 'bonus:1', 'voucher', 'WAT', 'communications')) {
        $da = $refMap[$cls]
        $db = $tgtMap[$cls]
        $ra = if ($da) { "$($da.OwnerHostId)/$($da.LastConfigurationChange)" } else { '-' }
        $ta = if ($db) { "$($db.OwnerHostId)/$($db.LastConfigurationChange)" } else { '-' }
        $mark = if ($ra -ne $ta) { ' **DIFF**' } else { '' }
        $md += "| $cls | $ra | $ta$mark |"
    }
    $md += ""
    $md += "- SASControler1 ConfigurationId: ref=$refCfgId target=$tgtCfgId"
    $md += "- WatAccounts: ref=$refWat target=$tgtWat"

    $md += "", "## Runtime / ports", ""
    $md += "- Reference transport: $($Reference.Runtime.Transport)"
    $md += "- Target transport: $($Target.Runtime.Transport)"
    if ($Reference.Runtime.Tcp31100) { $md += "- .90 TCP 31100: $($Reference.Runtime.Tcp31100 -join ', ')" }
    if ($Reference.Runtime.Tcp31150) { $md += "- .90 TCP 31150: $($Reference.Runtime.Tcp31150 -join ', ')" }
    if ($Reference.Runtime.Tcp40000) { $md += "- .90 TCP 40000: $($Reference.Runtime.Tcp40000 -join ', ')" }
    if ($Target.Runtime.Tcp31100) { $md += "- .171 TCP 31100: $($Target.Runtime.Tcp31100 -join ', ')" }
    if ($Target.Runtime.Tcp31150) { $md += "- .171 TCP 31150: $($Target.Runtime.Tcp31150 -join ', ')" }
    if ($Target.Runtime.Tcp40000) { $md += "- .171 TCP 40000: $($Target.Runtime.Tcp40000 -join ', ')" }

    $md += "", "## Poll / log signals", ""
    if ($Reference.PollSignals.LastPollLine) { $md += "- .90 last poll: $($Reference.PollSignals.LastPollLine)" }
    else { $md += "- .90 last poll: (none today)" }
    if ($Target.PollSignals.LastPollLine) { $md += "- .171 last poll: $($Target.PollSignals.LastPollLine)" }
    else { $md += "- .171 last poll: (none today)" }

    $commRef = @($Reference.LogSummary | Where-Object { $_.Path -like '*CommCtrlSAS*' } | Select-Object -First 1)
    $commTgt = @($Target.LogSummary | Where-Object { $_.Path -like '*CommCtrlSAS*' } | Select-Object -First 1)
    if ($commRef.LastLine) { $md += "- .90 CommCtrlSAS: $($commRef.LastLine)" }
    if ($commTgt.LastLine) { $md += "- .171 CommCtrlSAS: $($commTgt.LastLine)" }

    $slotRef = @($Reference.LogSummary | Where-Object { $_.Path -like '*SlotLog*' } | Select-Object -First 1)
    $slotTgt = @($Target.LogSummary | Where-Object { $_.Path -like '*SlotLog*' } | Select-Object -First 1)
    if ($slotRef.LastLine) { $md += "- .90 SlotLog tail: $($slotRef.LastLine)" }
    if ($slotTgt.LastLine) { $md += "- .171 SlotLog tail: $($slotTgt.LastLine)" }

    $md += "", "## Machine-id expected diffs (ignore for Apply)", ""
    if ($expected.Count -gt 0) {
        foreach ($e in $expected) { $md += "- $($e.RelativePath): different SHA (GST identity / game pack)" }
    }
    else { $md += "- (none beyond GstId in AurumSetup ServiceURI)" }

    $jsonPath = Join-Path $OutDir '90-vs-171-egm-comparison.json'
    $mdPath = Join-Path $OutDir '90-vs-171-egm-comparison.md'
    Write-AsciiJson -Object $jsonOut -Path $jsonPath -Depth 10
    ($md -join "`n") | Out-File -LiteralPath $mdPath -Encoding ascii -Force
    Write-Host "[+] Comparison JSON: $jsonPath" -ForegroundColor Green
    Write-Host "[+] Comparison MD:   $mdPath" -ForegroundColor Green
    return [pscustomobject]@{ Json = $jsonPath; Markdown = $mdPath; Comparison = $jsonOut }
}

function Invoke-CompareTwoCabinets {
    param(
        [string] $ReferenceIP,
        [string] $TargetIP,
        [string] $OutDir
    )
    if (-not (Test-Path -LiteralPath $OutDir)) {
        New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
    }
    Initialize-LabSmbCredential -Ip @($ReferenceIP, $TargetIP)

    Write-Host "[*] Reference capture $ReferenceIP ..." -ForegroundColor Cyan
    $refResult = Save-Capture -Ip $ReferenceIP -Label 'enabled' -BaseName '90-egm-enabled-state'
    Write-Host "[*] Target capture $TargetIP ..." -ForegroundColor Cyan
    $tgtResult = Save-Capture -Ip $TargetIP -Label 'current' -BaseName '171-egm-current-state'

    $refJson = Get-Content -LiteralPath $refResult.Json -Raw | ConvertFrom-Json
    $tgtJson = Get-Content -LiteralPath $tgtResult.Json -Raw | ConvertFrom-Json
    $cmp = Compare-EgmCaptures -Reference $refJson -Target $tgtJson -OutDir $OutDir
    return [pscustomobject]@{
        ReferenceJson = $refResult.Json
        TargetJson    = $tgtResult.Json
        Comparison    = $cmp
    }
}

if ($PSCmdlet.ParameterSetName -eq 'CompareTwo') {
    $dir = if ($CompareOutDir) { $CompareOutDir } elseif ($OutDir) { $OutDir } else { Join-Path $RepoRoot 'aft\investigations' }
    $bundle = Invoke-CompareTwoCabinets -ReferenceIP $ReferenceIP -TargetIP $TargetIP -OutDir $dir
    Write-Host "[*] Done. Apply=$($bundle.Comparison.Comparison.TargetApplyAssessment.State)" -ForegroundColor Cyan
    exit 0
}

if ($PSCmdlet.ParameterSetName -eq 'Compare') {
    if ($Compare.Count -lt 2) { throw 'Compare requires two JSON paths.' }
    $outCompare = Join-Path $OutDir ("compare-{0}.md" -f (Get-CaptureStamp))
    Compare-CaptureFiles -BaselinePath $Compare[0] -AfterPath $Compare[1] -OutPath $outCompare
    exit 0
}

Write-Host "[*] Capturing SAS state for $IP label=$Label ..." -ForegroundColor Cyan
$result = Save-Capture -Ip $IP -Label $Label -BaseName $OutBaseName
Write-Host "[*] sasmsgr=$($result.Payload.Signals.SasmsgrLogDirExists) polls=$($result.Payload.Signals.HasQGMIDPollsToday) bridge311=$($result.Payload.Signals.CommCtrlSasBridge311)" -ForegroundColor Cyan
