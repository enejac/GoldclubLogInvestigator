<#
.SYNOPSIS
    Diagnose missing cabinet qGMID1:80/81 SAS polls (service down vs MUX vs SMB).
    Dot-source from Invoke-WinDivertAft.ps1 and Invoke-SasMuxPollKeeper.ps1.
#>
#requires -Version 5.1

if (-not (Get-Command Test-LabSmbAccess -ErrorAction SilentlyContinue)) {
    . "$PSScriptRoot\LabAccess.ps1"
}
if (-not (Get-Command Test-LabWinRmReachable -ErrorAction SilentlyContinue)) {
    . "$PSScriptRoot\LabRemoteTransport.ps1"
}

function Resolve-LabPsExecAuthArgsForComputer {
    param(
        [string]   $Computer,
        [string[]] $PsExecAuthArgs = @()
    )
    if (@($PsExecAuthArgs).Count -gt 0) { return @($PsExecAuthArgs) }
    if (Get-Command Get-LabPsExecArgs -ErrorAction SilentlyContinue) {
        $fleet = if ($script:LabFleetIps) { @($script:LabFleetIps) } else { @() }
        if ($Computer -in $fleet) {
            return @(Get-LabPsExecArgs)
        }
    }
    return @()
}

function Initialize-CabinetInjectPrerequisites {
    <#
    Idempotent pre-inject bootstrap: SMB cmdkey, fleet PsExec auth, WinRM TrustedHosts,
    and PsExec health probe with retries. Call before poll keeper / AutoWake.
    #>
    param(
        [Parameter(Mandatory)]
        [string]         $Computer,
        [pscredential]   $Credential,
        [string]         $PsExecPath = 'C:\Tools\PSTools\PsExec.exe',
        [string[]]       $PsExecAuthArgs = @(),
        [switch]         $NoAutoBootstrap
    )

    $steps = New-Object System.Collections.Generic.List[string]
    $authArgs = @(Resolve-LabPsExecAuthArgsForComputer -Computer $Computer -PsExecAuthArgs $PsExecAuthArgs)
    $fleet = if ($script:LabFleetIps) { @($script:LabFleetIps) } else { @() }
    $isFleet = $Computer -in $fleet

    $report = [ordered]@{
        Attempted = $false
        SmbOk     = $false
        PsExecOk  = $false
        Health    = $null
        Steps     = $steps
        AuthArgs  = $authArgs
    }

    $smb = Test-LabSmbAccess -Ip $Computer
    if ($smb.Ok) {
        $report.SmbOk = $true
        $steps.Add('SMB admin share: OK') | Out-Null
    }

    if ($NoAutoBootstrap) {
        $report.Health = Get-CabinetSasHealthSnapshot -Computer $Computer -Credential $Credential `
            -PsExecPath $PsExecPath -PsExecAuthArgs $authArgs
        $report.PsExecOk = ($report.Health.Transport -eq 'PsExec')
        return [pscustomobject]$report
    }

    $report.Attempted = $true
    Write-Host "[*] Auto-bootstrap: verifying lab access to $Computer ..." -ForegroundColor Cyan

    if ($isFleet -and (Get-Command Initialize-LabSmbCredential -ErrorAction SilentlyContinue)) {
        $fleetIps = if ($script:LabFleetIps) { @($script:LabFleetIps) } else { @($Computer) }
        Initialize-LabSmbCredential -Ip $fleetIps
        $steps.Add("SMB: refreshed cmdkey for full lab fleet ($($fleetIps.Count) IP(s))") | Out-Null
        Start-Sleep -Milliseconds 500
        $smb = Test-LabSmbAccess -Ip $Computer
        if ($smb.Ok) {
            $report.SmbOk = $true
            $steps.Add('SMB admin share: OK after fleet cmdkey') | Out-Null
        }
    }

    if (-not $smb.Ok) {
        if ($smb.Tcp445) {
            Write-Host '[*] Auto-bootstrap: SMB denied  -  registering cmdkey (GOLD-CLUB\test) ...' -ForegroundColor Cyan
            Initialize-LabSmbCredential -Ip @($Computer)
            $steps.Add("SMB: registered cmdkey GOLD-CLUB\test for $Computer") | Out-Null
            Start-Sleep -Milliseconds 800
            $smb = Test-LabSmbAccess -Ip $Computer
        }
        if ($smb.Ok) {
            $report.SmbOk = $true
            $steps.Add('SMB admin share: OK after cmdkey') | Out-Null
            Write-Host '[+] Auto-bootstrap: SMB access OK.' -ForegroundColor Green
        }
        else {
            $steps.Add("SMB admin share: FAIL ($($smb.Detail))") | Out-Null
            Write-Host "[!] Auto-bootstrap: SMB unavailable ($($smb.Detail))" -ForegroundColor Yellow
        }
    }

    if ($isFleet -and (Get-Command Get-LabPsExecArgs -ErrorAction SilentlyContinue)) {
        $authArgs = @(Get-LabPsExecArgs)
        $report.AuthArgs = $authArgs
        $steps.Add('PsExec auth: applied GOLD-CLUB\test (-u/-p) for lab fleet') | Out-Null
    }

    $trustedHostsFailed = $false
    if ($isFleet -and (Get-Command Add-LabTrustedHostIfNeeded -ErrorAction SilentlyContinue)) {
        if (Add-LabTrustedHostIfNeeded -Computer $Computer -Quiet) {
            $steps.Add('WinRM TrustedHosts: configured') | Out-Null
        }
        else {
            $trustedHostsFailed = $true
        }
    }
    elseif ($isFleet -and (Get-Command Initialize-LabWinRmTrustedHosts -ErrorAction SilentlyContinue)) {
        $th = Initialize-LabWinRmTrustedHosts -Ip @($Computer)
        if ($th.Added.Count) {
            $steps.Add("WinRM TrustedHosts: added $Computer") | Out-Null
        }
        elseif ($th.AlreadyTrusted.Count) {
            $steps.Add('WinRM TrustedHosts: already configured') | Out-Null
        }
        if ($th.Failed.Count) {
            $trustedHostsFailed = $true
        }
    }

    $slowCabinet = ($Computer -eq '10.0.0.171')
    $maxAttempts = if ($slowCabinet) { 3 } else { 2 }
    $psExecTimeoutSec = if ($slowCabinet) { 180 } else { 90 }
    $retrySpacingSec = if ($slowCabinet) { 60 } else { 8 }
    $health = $null
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        if ($attempt -gt 1) {
            Write-Host "[*] Auto-bootstrap: PsExec retry $attempt of $maxAttempts (timeout ${psExecTimeoutSec}s; ${retrySpacingSec}s spacing) ..." -ForegroundColor Cyan
            Start-Sleep -Seconds $retrySpacingSec
        }
        else {
            Write-Host "[*] Auto-bootstrap: PsExec remote health check (timeout ${psExecTimeoutSec}s) ..." -ForegroundColor Cyan
        }

        $health = Get-CabinetSasHealthSnapshot -Computer $Computer -Credential $Credential `
            -PsExecPath $PsExecPath -PsExecAuthArgs $authArgs -PsExecTimeoutSec $psExecTimeoutSec
        if ($health.Transport -eq 'PsExec') {
            $report.PsExecOk = $true
            $report.Health = $health
            $steps.Add("PsExec health check: OK (attempt $attempt)") | Out-Null
            Write-Host '[+] Auto-bootstrap: remote health check OK.' -ForegroundColor Green
            break
        }

        $errText = if ($health.TransportError) { $health.TransportError } else { 'unavailable' }
        $steps.Add("PsExec health check: FAIL attempt $attempt ($errText)") | Out-Null

        if ($attempt -lt $maxAttempts) {
            Initialize-LabSmbCredential -Ip @($Computer)
            $steps.Add('SMB: refreshed cmdkey before PsExec retry') | Out-Null
            if ($isFleet -and (Get-Command Get-LabPsExecArgs -ErrorAction SilentlyContinue)) {
                $authArgs = @(Get-LabPsExecArgs)
                $report.AuthArgs = $authArgs
                $steps.Add('PsExec auth: re-applied GOLD-CLUB\test before retry') | Out-Null
            }
        }
    }

    if (-not $report.PsExecOk) {
        $report.Health = $health
        if ($trustedHostsFailed) {
            $steps.Add('WinRM TrustedHosts: could not add (elevated shell required); PsExec is primary on lab cabinets') | Out-Null
        }
        Write-Host "[!] Auto-bootstrap: PsExec health check failed after $maxAttempts attempt(s)." -ForegroundColor Yellow
        if ($health -and $health.TransportError) {
            Write-Host "    $($health.TransportError)" -ForegroundColor DarkGray
        }
    }

    return [pscustomobject]$report
}

function Add-CabinetInjectBootstrapChecks {
    param(
        [System.Collections.Generic.List[string]] $Checks,
        [pscustomobject] $BootstrapReport
    )
    if (-not $BootstrapReport -or -not $BootstrapReport.Attempted) { return }
    foreach ($step in $BootstrapReport.Steps) {
        $Checks.Add("Auto-bootstrap: $step") | Out-Null
    }
}

function Get-SasPollDiagLogLineUtc {
    param([string] $TimestampText)
    $t = $TimestampText.Trim()
    if ($t -match 'T') {
        return ([datetimeoffset]$t).UtcDateTime
    }
    $parsed = [datetime]::ParseExact($t, @('yyyy-MM-dd HH:mm:ss', 'yyyy-MM-dd HH:mm:ss.fff'), $null, [Globalization.DateTimeStyles]::AllowWhiteSpaces)
    return [datetimeoffset]::new($parsed, [timespan]::FromHours(1)).UtcDateTime
}

function Get-SasPollDiagSasmsgrLogPath {
    param(
        [string]   $Computer,
        [datetime] $Date = (Get-Date)
    )
    $name = $Date.ToString('yyyy-MM-dd')
    $subs = @(
        'GoldClub.Aurum.Services sasmsgr of SASControler1',
        'GoldClub.Aurum.Services SASControler1'
    )
    foreach ($sub in $subs) {
        $path = "\\$Computer\c`$\Goldclub\var\log\$sub\$name.log"
        if (Test-Path -LiteralPath $path) {
            return $path
        }
    }
    return "\\$Computer\c`$\Goldclub\var\log\$($subs[0])\$name.log"
}

function Get-CabinetSasmsgrLogState {
    param(
        [string] $Computer,
        [int]    $RecentPollWithinSec = 15,
        [int]    $TailLines = 120
    )
    $logPath = Get-SasPollDiagSasmsgrLogPath -Computer $Computer
    $state = [ordered]@{
        LogPath          = $logPath
        Exists           = $false
        Readable         = $false
        LastLine         = $null
        LastLineUtc      = $null
        LastPollLine     = $null
        LastPollUtc      = $null
        RecentPolls      = $false
        LogActiveRecent  = $false
    }

    if (-not (Test-Path -LiteralPath $logPath)) {
        return [pscustomobject]$state
    }
    $state.Exists = $true
    $lines = @(Get-Content -LiteralPath $logPath -Tail $TailLines -ErrorAction SilentlyContinue)
    if ($lines.Count -eq 0) {
        return [pscustomobject]$state
    }
    $state.Readable = $true
    $pollCutoff = (Get-Date).ToUniversalTime().AddSeconds(-[math]::Abs($RecentPollWithinSec))
    $activityCutoff = (Get-Date).ToUniversalTime().AddSeconds(-300)

    foreach ($line in $lines) {
        if ($line -notmatch '^(\S+)') { continue }
        try {
            $lineUtc = Get-SasPollDiagLogLineUtc $Matches[1]
        }
        catch {
            continue
        }
        if (-not $state.LastLineUtc -or $lineUtc -gt $state.LastLineUtc) {
            $state.LastLineUtc = $lineUtc
            $state.LastLine = $line.Trim()
        }
        if ($lineUtc -ge $activityCutoff) {
            $state.LogActiveRecent = $true
        }
        if ($line -match 'qGMID1:8[01]\s*$') {
            if (-not $state.LastPollUtc -or $lineUtc -gt $state.LastPollUtc) {
                $state.LastPollUtc = $lineUtc
                $state.LastPollLine = $line.Trim()
            }
            if ($lineUtc -ge $pollCutoff) {
                $state.RecentPolls = $true
            }
        }
    }
    return [pscustomobject]$state
}

function Get-PsExecHealthProbeError {
    param(
        [string] $RawOutput,
        [int]    $ExitCode = 0,
        [switch] $HealthParsed
    )
    if ($HealthParsed) { return $null }

    $noisePattern = '^(Connecting to|Starting PSEXESVC|PsExec v|Copyright|Sysinternals|Connecting with|cmd started on)'
    $lines = @($RawOutput -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    foreach ($ln in ($lines | Select-Object -Last 12)) {
        if ($ln -match $noisePattern) { continue }
        if ($ln -match 'error|denied|failed|Could not|Logon failure|timed out|not found') {
            return $ln
        }
    }
    $tailLines = @($lines | Where-Object { $_ -notmatch $noisePattern } | Select-Object -Last 3)
    $tail = ($tailLines -join '; ')
    if ($tail) { return $tail }
    if ($ExitCode -ne 0) { return "PsExec exited with code $ExitCode" }
    return 'no HEALTH line in output'
}

function Invoke-CabinetSasHealthViaPsExec {
    param(
        [string]   $Computer,
        [string]   $GatewaySvc,
        [string]   $AurumSvc,
        [string]   $PsExecPath,
        [string[]] $PsExecAuthArgs,
        [int]      $TimeoutSec = 90
    )
    if (-not ($PsExecPath -and (Test-Path -LiteralPath $PsExecPath))) {
        return $null
    }

    $gwEsc = $GatewaySvc -replace "'", "''"
    $auEsc = $AurumSvc -replace "'", "''"
    $psBody = @"
function S([string]`$n) {
  `$svc = Get-CimInstance Win32_Service -Filter ("Name='" + `$n.Replace("'", "''") + "'") -ErrorAction SilentlyContinue
  if (-not `$svc) { return 'Missing' }
  if (`$svc.State -eq 'Running') { return 'Running' }
  return 'Stopped'
}
`$comm = [bool](Get-Process -Name CommCtrlSAS -ErrorAction SilentlyContinue)
`$gw = S '$gwEsc'
`$au = S '$auEsc'
`$tcp = @(Get-NetTCPConnection -LocalPort 31150 -ErrorAction SilentlyContinue | Select-Object State, RemotePort)
Write-Output ("HEALTH comm=`$comm gw=`$gw aurum=`$au")
foreach (`$t in `$tcp) { Write-Output ('TCP=' + `$t.State + ':' + `$t.RemotePort) }
"@
    $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($psBody))
    $psArgs = @("\\$Computer", '-accepteula') + @($PsExecAuthArgs) + @('-s', '-n', ([string]$TimeoutSec),
        'powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $enc)
    try {
        $raw = (& $PsExecPath @psArgs 2>&1 | Out-String)
        $exitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
        $comm = $null; $gwSt = 'Unknown'; $auSt = 'Unknown'; $tcpRows = @()
        foreach ($ln in ($raw -split "`r?`n")) {
            $ln = $ln.Trim()
            if ($ln -match '^HEALTH comm=(True|False) gw=(\S+) aurum=(\S+)$') {
                $comm = ($Matches[1] -eq 'True')
                $gwSt = $Matches[2]
                $auSt = $Matches[3]
            }
            elseif ($ln -match '^TCP=(.+)$') { $tcpRows += $Matches[1].Trim() }
        }
        if ($null -ne $comm) {
            return [pscustomobject]@{
                CommCtrlSasRunning   = $comm
                CommCtrlSasPid       = $null
                GatewayServiceStatus = $gwSt
                AurumServiceStatus   = $auSt
                Tcp31150             = $tcpRows
                Transport            = 'PsExec'
                TransportError       = $null
            }
        }
        $probeErr = Get-PsExecHealthProbeError -RawOutput $raw -ExitCode $exitCode
        return [pscustomobject]@{
            CommCtrlSasRunning   = $null
            CommCtrlSasPid       = $null
            GatewayServiceStatus = 'Unknown'
            AurumServiceStatus   = 'Unknown'
            Tcp31150             = @()
            Transport            = 'Unavailable'
            TransportError       = "PsExec failed: $probeErr"
        }
    }
    catch {
        return [pscustomobject]@{
            CommCtrlSasRunning   = $null
            CommCtrlSasPid       = $null
            GatewayServiceStatus = 'Unknown'
            AurumServiceStatus   = 'Unknown'
            Tcp31150             = @()
            Transport            = 'Unavailable'
            TransportError       = "PsExec failed: $($_.Exception.Message)"
        }
    }
}

function Test-CabinetSasmsgrStale {
    param(
        [string] $Computer,
        [int]    $StaleMinutes = 5,
        [int]    $RecentPollWithinSec = 15
    )
    $logState = Get-CabinetSasmsgrLogState -Computer $Computer -RecentPollWithinSec $RecentPollWithinSec
    if (-not $logState.Exists -or -not $logState.Readable) {
        return [pscustomobject]@{ Stale = $false; LogState = $logState; AgeMinutes = $null }
    }
    if ($logState.RecentPolls) {
        return [pscustomobject]@{ Stale = $false; LogState = $logState; AgeMinutes = 0 }
    }
    $refUtc = if ($logState.LastLineUtc) { $logState.LastLineUtc } else { $logState.LastPollUtc }
    if (-not $refUtc) {
        return [pscustomobject]@{ Stale = $true; LogState = $logState; AgeMinutes = $null }
    }
    $ageMin = ((Get-Date).ToUniversalTime() - $refUtc).TotalMinutes
    return [pscustomobject]@{
        Stale       = ($ageMin -gt [math]::Abs($StaleMinutes))
        LogState    = $logState
        AgeMinutes  = [math]::Round($ageMin, 1)
    }
}

function Get-CabinetSasHealthSnapshot {
    param(
        [string]         $Computer,
        [pscredential]   $Credential,
        [string]         $PsExecPath = 'C:\Tools\PSTools\PsExec.exe',
        [string[]]       $PsExecAuthArgs = @(),
        [int]            $PsExecTimeoutSec = 90
    )
    $gatewaySvc = 'GoldClub Serial Communication Gateway SAS'
    $aurumSvc = 'GoldClub.Aurum.Services'
    $authArgs = Resolve-LabPsExecAuthArgsForComputer -Computer $Computer -PsExecAuthArgs $PsExecAuthArgs

    # PsExec-first for diagnostics: avoids TrustedHosts admin noise; required for .171.
    $psExecResult = Invoke-CabinetSasHealthViaPsExec -Computer $Computer `
        -GatewaySvc $gatewaySvc -AurumSvc $aurumSvc -PsExecPath $PsExecPath -PsExecAuthArgs $authArgs `
        -TimeoutSec $PsExecTimeoutSec
    if ($psExecResult -and $psExecResult.Transport -eq 'PsExec') {
        return $psExecResult
    }

    $transportError = if ($psExecResult) { $psExecResult.TransportError } else { 'PsExec not available' }
    $remoteScript = {
        param($GatewaySvcName, $AurumSvcName)
        function Get-CimServiceState {
            param([string] $Name)
            $filter = "Name='" + $Name.Replace("'", "''") + "'"
            $svc = Get-CimInstance Win32_Service -Filter $filter -ErrorAction SilentlyContinue
            if (-not $svc) { return 'Missing' }
            if ($svc.State -eq 'Running') { return 'Running' }
            return 'Stopped'
        }
        $procComm = Get-Process -Name CommCtrlSAS -ErrorAction SilentlyContinue
        $tcp31150 = @(Get-NetTCPConnection -LocalPort 31150 -ErrorAction SilentlyContinue |
            Select-Object State, RemotePort)
        [pscustomobject]@{
            CommCtrlSasRunning   = [bool]$procComm
            CommCtrlSasPid       = if ($procComm) { [int]$procComm.Id } else { $null }
            GatewayServiceStatus = Get-CimServiceState -Name $GatewaySvcName
            AurumServiceStatus   = Get-CimServiceState -Name $AurumSvcName
            Tcp31150             = $tcp31150
            Transport            = 'WinRM'
            TransportError       = $null
        }
    }

    if ($Credential -and (Test-LabWinRmReachable -Computer $Computer) -and (Test-LabTrustedHostConfigured -Computer $Computer)) {
        try {
            return Invoke-Command -ComputerName $Computer -Credential $Credential -ScriptBlock $remoteScript `
                -ArgumentList $gatewaySvc, $aurumSvc -ErrorAction Stop
        }
        catch {
            if ($transportError -eq 'PsExec not available') {
                $transportError = $_.Exception.Message
            }
            else {
                $transportError = "$transportError; WinRM: $($_.Exception.Message)"
            }
        }
    }
    elseif (-not $Credential -and $transportError -eq 'PsExec not available') {
        $transportError = 'No remote credential (lab fleet uses GOLD-CLUB\test via LabAccess.ps1)'
    }

    return [pscustomobject]@{
        CommCtrlSasRunning   = $null
        CommCtrlSasPid       = $null
        GatewayServiceStatus = 'Unknown'
        AurumServiceStatus   = 'Unknown'
        Tcp31150             = @()
        Transport            = 'Unavailable'
        TransportError       = $transportError
    }
}

function Test-SasPollDiagCabinetPrefersMuxCom {
    param([string] $Computer)
    return $Computer -eq '10.0.0.171'
}

function Test-CabinetSasServicesConfirmedDown {
    param([pscustomobject] $Health)
    if (-not $Health -or $Health.Transport -eq 'Unavailable') { return $false }
    $commDown = ($null -ne $Health.CommCtrlSasRunning) -and (-not $Health.CommCtrlSasRunning)
    $gatewayDown = $Health.GatewayServiceStatus -in @('Stopped', 'Missing')
    $aurumDown = $Health.AurumServiceStatus -in @('Stopped', 'Missing')
    return ($commDown -or $gatewayDown -or $aurumDown)
}

function Test-CabinetSasServicesConfirmedUp {
    param([pscustomobject] $Health)
    if (-not $Health -or $Health.Transport -eq 'Unavailable') { return $false }
    return ($Health.CommCtrlSasRunning -eq $true) -and
        ($Health.GatewayServiceStatus -eq 'Running') -and
        ($Health.AurumServiceStatus -eq 'Running')
}

function Invoke-CabinetSasPollAutoWakeIfNeeded {
    param(
        [string]       $Computer,
        [pscredential] $Credential,
        [switch]       $NoAutoWake,
        [string]       $PsExecPath = 'C:\Tools\PSTools\PsExec.exe',
        [string[]]     $PsExecAuthArgs = @(),
        [int]          $WaitSec = 90,
        [int]          $StaleWakeMin = 5,
        [int]          $PsExecTimeoutSec = 90
    )
    $outcome = [ordered]@{
        Attempted    = $false
        WakeOk       = $false
        ServicesUp   = $false
        HealthBefore = $null
        HealthAfter  = $null
        StaleWake    = $false
        StaleAgeMin  = $null
    }

    $psExecTimeout = if ($Computer -eq '10.0.0.171') { [math]::Max($PsExecTimeoutSec, 180) } else { $PsExecTimeoutSec }
    $healthBefore = Get-CabinetSasHealthSnapshot -Computer $Computer -Credential $Credential `
        -PsExecPath $PsExecPath -PsExecAuthArgs $PsExecAuthArgs -PsExecTimeoutSec $psExecTimeout
    $outcome.HealthBefore = $healthBefore

    $servicesDown = Test-CabinetSasServicesConfirmedDown -Health $healthBefore
    $staleInfo = Test-CabinetSasmsgrStale -Computer $Computer -StaleMinutes $StaleWakeMin
    $outcome.StaleAgeMin = $staleInfo.AgeMinutes
    $sasmsgrStale = $staleInfo.Stale
    $outcome.StaleWake = $sasmsgrStale

    if (-not $servicesDown -and -not $sasmsgrStale) {
        $outcome.ServicesUp = (Test-CabinetSasServicesConfirmedUp -Health $healthBefore)
        return [pscustomobject]$outcome
    }

    if ($NoAutoWake) {
        return [pscustomobject]$outcome
    }

    if ($sasmsgrStale -and -not $servicesDown) {
        $ageText = if ($null -ne $staleInfo.AgeMinutes) { "$($staleInfo.AgeMinutes) min" } else { 'unknown age' }
        Write-Host "[*] sasmsgr stale ($ageText, no recent 80/81) on $Computer - running Invoke-WakeSasBridge (PsExec inconclusive OK)..." -ForegroundColor Cyan
    }
    elseif ($servicesDown) {
        Write-Host "[*] SAS services down on $Computer - running Invoke-WakeSasBridge..." -ForegroundColor Cyan
    }

    $wakeScript = Join-Path $PSScriptRoot 'Invoke-WakeSasBridge.ps1'
    if (-not (Test-Path -LiteralPath $wakeScript)) {
        Write-Host "[!] Wake needed on $Computer but missing $wakeScript" -ForegroundColor Yellow
        return [pscustomobject]$outcome
    }

    $outcome.Attempted = $true
    try {
        $wakeParams = @{
            ComputerName = $Computer
            WaitSec      = $WaitSec
        }
        if ($Credential) { $wakeParams.Credential = $Credential }
        & $wakeScript @wakeParams
        $outcome.WakeOk = ($LASTEXITCODE -eq 0 -or $null -eq $LASTEXITCODE)
    }
    catch {
        Write-Host "[!] Invoke-WakeSasBridge failed: $($_.Exception.Message)" -ForegroundColor Yellow
        $outcome.WakeOk = $false
        return [pscustomobject]$outcome
    }

    Start-Sleep -Seconds 5
    $healthAfter = Get-CabinetSasHealthSnapshot -Computer $Computer -Credential $Credential `
        -PsExecPath $PsExecPath -PsExecAuthArgs $PsExecAuthArgs -PsExecTimeoutSec $psExecTimeout
    $outcome.HealthAfter = $healthAfter
    $outcome.ServicesUp = (Test-CabinetSasServicesConfirmedUp -Health $healthAfter)

    if ($outcome.ServicesUp) {
        Write-Host '[+] SAS bridge services restored after Invoke-WakeSasBridge.' -ForegroundColor Green
    }
    elseif ($sasmsgrStale -and (Test-CabinetSasPollsRecentViaLog -Computer $Computer -WithinSeconds 20)) {
        Write-Host '[+] sasmsgr shows fresh 80/81 after Invoke-WakeSasBridge (service state inconclusive).' -ForegroundColor Green
        $outcome.ServicesUp = $true
    }
    else {
        Write-Host '[!] SAS services still down or unverified after Invoke-WakeSasBridge.' -ForegroundColor Yellow
    }

    return [pscustomobject]$outcome
}

function Test-CabinetSasPollsRecentViaLog {
    param(
        [string] $Computer,
        [int]    $WithinSeconds = 15
    )
    $logState = Get-CabinetSasmsgrLogState -Computer $Computer -RecentPollWithinSec $WithinSeconds
    return [bool]$logState.RecentPolls
}

function Get-CabinetSasPollFailureDiagnosis {
    param(
        [string]       $Computer,
        [pscredential] $Credential,
        [string]       $PsExecPath = 'C:\Tools\PSTools\PsExec.exe',
        [string[]]     $PsExecAuthArgs = @(),
        [string]       $Port = 'COM4',
        [hashtable]    $ComProbeResult = $null,
        [switch]       $PollKeeperStarted,
        [int]          $PollWaitSec = 15,
        [switch]       $AutoWakeAttempted,
        [switch]       $AutoWakeFailed,
        [pscustomobject] $BootstrapReport = $null
    )

    $checks = New-Object System.Collections.Generic.List[string]
    $requiresMux = Test-SasPollDiagCabinetPrefersMuxCom -Computer $Computer
    if (-not $BootstrapReport -and $script:InjectBootstrapReport) {
        $BootstrapReport = $script:InjectBootstrapReport
    }
    Add-CabinetInjectBootstrapChecks -Checks $checks -BootstrapReport $BootstrapReport

    $smb = Test-LabSmbAccess -Ip $Computer
    if (-not $smb.Ok) {
        $checks.Add("SMB admin share / logs: FAIL ($($smb.Detail))") | Out-Null
        $smbAction = if ($BootstrapReport -and $BootstrapReport.Attempted) {
            "Auto-bootstrap already registered cmdkey for GOLD-CLUB\test on $Computer; confirm cabinet is online (TCP 445) and reachable, then retry."
        }
        else {
            'Run .\Initialize-LabAccess.ps1 -Verify, confirm cabinet is online, then retry.'
        }
        return [pscustomobject]@{
            Category       = 'smb_failure'
            PrimaryReason  = "Cannot read cabinet logs on $Computer (SMB share unreachable or access denied)."
            Action         = $smbAction
            Checks         = $checks
            RequiresMuxCom = $requiresMux
        }
    }
    $checks.Add('SMB admin share / logs: OK') | Out-Null

    $logState = Get-CabinetSasmsgrLogState -Computer $Computer
    if (-not $logState.Exists) {
        $checks.Add("sasmsgr log today: missing ($($logState.LogPath))") | Out-Null
    }
    elseif (-not $logState.Readable) {
        $checks.Add('sasmsgr log today: empty or unreadable') | Out-Null
    }
    else {
        if ($logState.LastPollLine) {
            $checks.Add("sasmsgr last qGMID1:80/81: $($logState.LastPollLine)") | Out-Null
        }
        else {
            $checks.Add('sasmsgr last qGMID1:80/81: none in log tail') | Out-Null
        }
        if ($logState.LastLine) {
            $checks.Add("sasmsgr last line: $($logState.LastLine)") | Out-Null
        }
    }

    $health = Get-CabinetSasHealthSnapshot -Computer $Computer -Credential $Credential `
        -PsExecPath $PsExecPath -PsExecAuthArgs $PsExecAuthArgs

    $remoteCheckOk = $health.Transport -ne 'Unavailable'
    if (-not $remoteCheckOk) {
        $checks.Add("Remote process/service check: unavailable ($($health.TransportError))") | Out-Null
    }
    else {
        $commState = if ($health.CommCtrlSasRunning) { 'running' } else { 'NOT running' }
        $checks.Add("Remote health via $($health.Transport): CommCtrlSAS=$commState, gateway=$($health.GatewayServiceStatus), aurum=$($health.AurumServiceStatus)") | Out-Null
        if ($health.Tcp31150 -and @($health.Tcp31150).Count -gt 0) {
            $tcpText = (@($health.Tcp31150) | ForEach-Object {
                if ($_ -is [string]) { $_ } else { "$($_.State):$($_.RemotePort)" }
            }) -join ', '
            $checks.Add("TCP 31150 (CommCtrlSAS bridge): $tcpText") | Out-Null
        }
        else {
            $checks.Add('TCP 31150 (CommCtrlSAS bridge): no Established connection') | Out-Null
        }
    }

    if ($ComProbeResult -and -not $ComProbeResult.Ok) {
        $checks.Add("Host COM probe ($Port): FAIL - $($ComProbeResult.Message)") | Out-Null
        $hint = if ($ComProbeResult.Message -match 'in use|SASTest|busy|denied') {
            "Close the IGT SAS tester (or any app holding $Port), then retry."
        }
        elseif ($ComProbeResult.Message -match 'not find|FileNotFound|does not exist') {
            "Check USB serial cable and Device Manager COM port assignment for $Port."
        }
        else {
            "Verify $Port is the MUX host cable and pyserial is installed (pip install pyserial)."
        }
        return [pscustomobject]@{
            Category       = 'com_port_blocked'
            PrimaryReason  = "Host serial port $Port is not available for MUX upstream polling."
            Action         = $hint
            Checks         = $checks
            RequiresMuxCom = $requiresMux
        }
    }
    if ($ComProbeResult -and $ComProbeResult.Ok) {
        $checks.Add("Host COM probe ($Port): OK - $($ComProbeResult.Message)") | Out-Null
    }

    if ($PollKeeperStarted) {
        $checks.Add("Built-in poll keeper on $Port @19200: ran ${PollWaitSec}s, no fresh qGMID1:80/81 in sasmsgr") | Out-Null
    }

    $commDown = $remoteCheckOk -and ($null -ne $health.CommCtrlSasRunning) -and (-not $health.CommCtrlSasRunning)
    $gatewayDown = $remoteCheckOk -and ($health.GatewayServiceStatus -in @('Stopped', 'Missing'))
    $aurumDown = $remoteCheckOk -and ($health.AurumServiceStatus -in @('Stopped', 'Missing'))
    $servicesConfirmedDown = $commDown -or $gatewayDown -or $aurumDown

    $servicesConfirmedUp = (Test-CabinetSasServicesConfirmedUp -Health $health)

    if ($servicesConfirmedDown) {
        if ($commDown) {
            $checks.Add('Signal: CommCtrlSAS.exe is not running on cabinet') | Out-Null
        }
        if ($gatewayDown) {
            $checks.Add("Signal: gateway SAS service is $($health.GatewayServiceStatus)") | Out-Null
        }
        if ($aurumDown) {
            $checks.Add("Signal: GoldClub.Aurum.Services is $($health.AurumServiceStatus)") | Out-Null
        }
        if ($AutoWakeAttempted) {
            $checks.Add('AutoWake: Invoke-WakeSasBridge was run automatically before abort') | Out-Null
        }
        $wakeAction = if ($AutoWakeAttempted) {
            if ($AutoWakeFailed) {
                "Invoke-WakeSasBridge did not restore SAS services on $Computer; check cabinet power/RDP, restart services manually, then retry."
            }
            else {
                "SAS services were restarted but polls are still missing; check MUX wiring and host COM port, then retry."
            }
        }
        elseif ($requiresMux) {
            "Start SAS/Aurum services on cabinet first (e.g. .\Invoke-WakeSasBridge.ps1 -IP $Computer), then retry."
        }
        else {
            "Start CommCtrlSAS and GoldClub.Aurum.Services on $Computer (e.g. .\Invoke-WakeSasBridge.ps1 -IP $Computer), then retry."
        }
        return [pscustomobject]@{
            Category       = 'sas_service_down'
            PrimaryReason  = 'SAS bridge services on the cabinet are not running (confirmed via remote check).'
            Action         = $wakeAction
            Checks         = $checks
            RequiresMuxCom = $requiresMux
        }
    }

    if (-not $remoteCheckOk) {
        if ($logState.LastPollLine -and -not $logState.RecentPolls) {
            $checks.Add('Signal: stale 80/81 in log (cannot confirm current service state)') | Out-Null
        }
        $inconclusiveAction = if ($BootstrapReport -and $BootstrapReport.Attempted) {
            @(
                "Auto-bootstrap already tried cmdkey, PsExec GOLD-CLUB\test auth, and $($BootstrapReport.Steps.Count) probe step(s) on $Computer.",
                "If still failing: confirm cabinet online, run elevated .\Initialize-LabAccess.ps1 -Verify, or RDP to cabinet.",
                'If services are up but polls remain missing: check MUX wiring and host COM port.'
            ) -join ' '
        }
        else {
            @(
                "Fix remote access first: .\Initialize-LabAccess.ps1 -Verify (PsExec uses GOLD-CLUB\test on lab cabinets).",
                "Then confirm services: .\Invoke-WakeSasBridge.ps1 -IP $Computer if sasmsgr stays stale.",
                'If services are up but polls remain missing: check MUX wiring and host COM port.'
            ) -join ' '
        }
        return [pscustomobject]@{
            Category       = 'remote_check_failed'
            PrimaryReason  = 'Could not verify cabinet SAS services (remote health check failed).'
            Action         = $inconclusiveAction
            Checks         = $checks
            RequiresMuxCom = $requiresMux
        }
    }

    if ($logState.LastPollLine -and -not $logState.RecentPolls) {
        $checks.Add('Signal: stale 80/81 in log (SAS was active earlier but not now)') | Out-Null
    }

    $muxAction = if ($requiresMux) {
        'Check MUX wiring (host COM4 -> MUX upstream -> cabinet COM11), cabinet power, and correct COM port; probe with .\Invoke-SasMuxPollKeeper.ps1 -IP ' + $Computer
    }
    else {
        'Establish steady SAS general polls (qGMID1:80/81) on the cabinet link, then retry inject.'
    }

    $muxReason = if ($servicesConfirmedUp) {
        'SAS services are running but cabinet sasmsgr shows no fresh qGMID1:80/81 general polls.'
    }
    else {
        'SAS services appear running but cabinet sasmsgr shows no fresh qGMID1:80/81 general polls.'
    }

    return [pscustomobject]@{
        Category       = 'mux_wiring'
        PrimaryReason  = $muxReason
        Action         = $muxAction
        Checks         = $checks
        RequiresMuxCom = $requiresMux
    }
}

function Write-CabinetRemediationAbort {
    param(
        [System.Collections.Generic.List[object]] $CycleLog,
        [int] $MaxCycles,
        [string] $Computer,
        [pscustomobject] $LastDiagnosis
    )
    Write-Host ''
    Write-Host ('=' * 72) -ForegroundColor Red
    Write-Host "[!] Aborting inject: prerequisites not met after $MaxCycles remediation cycle(s) on $Computer." -ForegroundColor Red
    Write-Host ''
    Write-Host 'Remediation cycles attempted:' -ForegroundColor Cyan
    foreach ($entry in $CycleLog) {
        $cycleNum = $entry.Cycle
        $summary = if ($entry.Summary) { $entry.Summary } else { 'incomplete' }
        Write-Host "  Cycle $cycleNum/$MaxCycles : $summary" -ForegroundColor Yellow
        foreach ($step in @($entry.Steps)) {
            Write-Host "    - $step" -ForegroundColor DarkGray
        }
    }
    if ($LastDiagnosis) {
        Write-Host ''
        Write-Host "Last diagnosis: $($LastDiagnosis.PrimaryReason)" -ForegroundColor Yellow
        Write-Host 'Checked:' -ForegroundColor Cyan
        foreach ($item in $LastDiagnosis.Checks) {
            Write-Host "  - $item" -ForegroundColor DarkGray
        }
        Write-Host "Action: $($LastDiagnosis.Action)" -ForegroundColor Green
        if ($LastDiagnosis.RequiresMuxCom) {
            Write-Host 'Note: MUX/COM4 host cable may be physically disconnected if wake + poll keeper both failed all cycles.' -ForegroundColor DarkGray
        }
    }
    Write-Host ('=' * 72) -ForegroundColor Red
    Write-Host ''
}

function Write-CabinetSasPollAbort {
    param(
        [pscustomobject] $Diagnosis,
        [string]         $Context = 'inject'
    )
    $title = switch ($Context) {
        'mux_keeper' { 'MUX poll keeper failed: no live SAS general polls (qGMID1:80/81).' }
        default      { 'Aborting inject: live SAS general polls (qGMID1:80/81) are required before AFT credit can post.' }
    }
    Write-Host ''
    Write-Host ('=' * 72) -ForegroundColor Red
    Write-Host "[!] $title" -ForegroundColor Red
    Write-Host "Reason: $($Diagnosis.PrimaryReason)" -ForegroundColor Yellow
    Write-Host 'Checked:' -ForegroundColor Cyan
    foreach ($item in $Diagnosis.Checks) {
        Write-Host "  - $item" -ForegroundColor DarkGray
    }
    Write-Host "Action: $($Diagnosis.Action)" -ForegroundColor Green
    switch ($Diagnosis.Category) {
        'remote_check_failed' {
            Write-Host 'Note: SMB log read succeeded; service state is inconclusive until PsExec/WinRM health check works.' -ForegroundColor DarkGray
            if (@($Diagnosis.Checks | Where-Object { $_ -match 'Auto-bootstrap:' }).Count -gt 0) {
                Write-Host 'Note: Auto-bootstrap already attempted lab credential + PsExec retries before abort.' -ForegroundColor DarkGray
            }
        }
        'sas_service_down' {
            if (@($Diagnosis.Checks | Where-Object { $_ -match 'AutoWake:' }).Count -gt 0) {
                Write-Host 'Note: AutoWake already attempted; manual cabinet intervention may be required.' -ForegroundColor DarkGray
            }
            elseif ($Diagnosis.RequiresMuxCom) {
                Write-Host 'Note: On .171 fix SAS/Aurum services before MUX cable troubleshooting.' -ForegroundColor DarkGray
            }
        }
        'mux_wiring' {
            if ($Diagnosis.RequiresMuxCom) {
                Write-Host 'Note: On .171 services look up; focus on MUX/COM4 host cable and cabinet COM11 path.' -ForegroundColor DarkGray
            }
        }
    }
    Write-Host ('=' * 72) -ForegroundColor Red
    Write-Host ''
}
