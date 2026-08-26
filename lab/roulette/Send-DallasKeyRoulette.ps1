<#
.SYNOPSIS
    ROULETTE fork of Send-DallasKey -- non-interactive CommCtrl :30800 stub.

.DESCRIPTION
    Slot path uses WinDivert splice into an EXISTING OneHand/BiOS <-> :30800
    connection (lab\Invoke-DallasSpliceRemote.ps1). Do NOT modify that.

    On roulette (ruleta/godot) there is typically NO subscriber on :30800 while
    the game is running (gateway STATUS shows only 30300/30600/30700 clients).
    This fork replicates Send-DallasKey: become the :30800 server, wait for
    clients, publish insert/eject ASCII frames (700/701 + ROM).

    Designed to run ON the cabinet (usually via Invoke-SendDallasKeyRouletteRemote).
#>
[CmdletBinding()]
param(
    [int] $ListenPort = 30800,
    [ValidateSet('Loopback','Any')]
    [string] $BindAddress = 'Loopback',
    [ValidatePattern('^[0-9A-Fa-f]{16}$')]
    [string] $Rom = '01D68A721B000019',
    [string[]] $InsertContext = @('700','701'),
    [string[]] $EjectContext = @('700','701','655'),
    [ValidateSet('insert','eject','roundtrip')]
    [string] $Action = 'roundtrip',
    [int] $WaitForClientSeconds = 45,
    [int] $HoldSecondsAfterInject = 8,
    [string] $StatusFile = 'C:\Windows\Temp\dallas_roulette_status.txt',
    [string] $LogFile = 'C:\Windows\Temp\dallas_roulette_send.log'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Rom = $Rom.ToUpperInvariant()
$bindIp = if ($BindAddress -eq 'Any') { [Net.IPAddress]::Any } else { [Net.IPAddress]::Loopback }
$clients = New-Object 'System.Collections.Generic.List[object]'
$utf8 = New-Object Text.UTF8Encoding $false

function Write-RLog([string]$Message) {
    $line = '[{0}] {1}' -f (Get-Date -Format o), $Message
    Write-Host $line
    try { [IO.File]::AppendAllText($LogFile, $line + [Environment]::NewLine, $utf8) } catch {}
}

function Set-Status([string]$Message) {
    try { [IO.File]::WriteAllText($StatusFile, $Message + [Environment]::NewLine, $utf8) } catch {}
    Write-RLog $Message
}

function Send-Line([string]$Line, [string]$Tag = '') {
    $bytes = [Text.Encoding]::ASCII.GetBytes($Line + "`r`n")
    $dead = New-Object 'System.Collections.Generic.List[object]'
    foreach ($c in $clients) {
        try { $c.Stream.Write($bytes, 0, $bytes.Length); $c.Stream.Flush() }
        catch { [void]$dead.Add($c) }
    }
    Write-RLog ('TX [{0}] {1} -> {2} client(s)' -f $Tag, $Line, $clients.Count)
    foreach ($d in $dead) {
        try { $d.Stream.Dispose() } catch {}
        try { $d.Client.Close() } catch {}
        [void]$clients.Remove($d)
    }
}

function Send-Insert {
    foreach ($code in $InsertContext) { Send-Line $code 'STATE' }
    Send-Line $Rom 'DALLAS'
}

function Send-Eject {
    foreach ($code in $EjectContext) { Send-Line $code 'STATE' }
}

try { Remove-Item -LiteralPath $LogFile, $StatusFile -Force -ErrorAction SilentlyContinue } catch {}
Set-Status ('START action={0} rom={1} port={2}' -f $Action, $Rom, $ListenPort)

$listener = $null
try {
    $listener = New-Object Net.Sockets.TcpListener $bindIp, $ListenPort
    $listener.Server.SetSocketOption([Net.Sockets.SocketOptionLevel]::Socket, [Net.Sockets.SocketOptionName]::ReuseAddress, $true)
    $listener.Start()
    Set-Status ('LISTEN OK {0}:{1}' -f $bindIp, $ListenPort)
} catch {
    Set-Status ('LISTEN FAIL: {0}' -f $_.Exception.Message)
    exit 2
}

$deadline = [datetime]::UtcNow.AddSeconds($WaitForClientSeconds)
try {
    while ([datetime]::UtcNow -lt $deadline -and $clients.Count -lt 1) {
        if ($listener.Pending()) {
            $tcp = $listener.AcceptTcpClient()
            $tcp.NoDelay = $true
            $remote = $tcp.Client.RemoteEndPoint.ToString()
            $clients.Add([pscustomobject]@{ Client = $tcp; Stream = $tcp.GetStream(); Remote = $remote }) | Out-Null
            Set-Status ('CLIENT CONNECTED {0} count={1}' -f $remote, $clients.Count)
        } else {
            Start-Sleep -Milliseconds 200
        }
    }

    # Keep accepting briefly for late subscribers
    $extra = [datetime]::UtcNow.AddSeconds(2)
    while ([datetime]::UtcNow -lt $extra) {
        if ($listener.Pending()) {
            $tcp = $listener.AcceptTcpClient()
            $tcp.NoDelay = $true
            $remote = $tcp.Client.RemoteEndPoint.ToString()
            $clients.Add([pscustomobject]@{ Client = $tcp; Stream = $tcp.GetStream(); Remote = $remote }) | Out-Null
            Set-Status ('CLIENT CONNECTED {0} count={1}' -f $remote, $clients.Count)
        } else { Start-Sleep -Milliseconds 100 }
    }

    if ($clients.Count -lt 1) {
        Set-Status 'NO_CLIENT timeout - nothing subscribed to :30800'
        exit 3
    }

    switch ($Action) {
        'insert' { Send-Insert }
        'eject' { Send-Eject }
        'roundtrip' {
            Send-Eject
            Start-Sleep -Milliseconds 250
            Send-Insert
        }
    }
    Set-Status ('INJECTED action={0} clients={1}' -f $Action, $clients.Count)
    Start-Sleep -Seconds $HoldSecondsAfterInject
    Set-Status 'DONE'
    exit 0
}
finally {
    foreach ($c in @($clients)) {
        try { $c.Stream.Dispose() } catch {}
        try { $c.Client.Close() } catch {}
    }
    if ($listener) { try { $listener.Stop() } catch {} }
}