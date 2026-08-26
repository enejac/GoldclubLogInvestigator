# Keyboard-panel stub for the QA rig.
#
# ruleta.exe's OpenPorts() (ARuleta.cpp:785) TCP-connects to CommCtrl for each player station's
# keyboard panel. Unlike OpenPort()/COM2Write() it has NO 'NotMainBoard >= 2' sim-mode guard, so a
# machine with no hardware still has to answer or the core throws CRuletaError(19): ERROR 19 appears
# on screen, the game drops into STOPDIALOG and tears down - taking the :8090 web server with it.
# From the outside that looks like a frontend stuck on a loading spinner with a flickering, jumping
# countdown, which points nowhere near the keyboard.
#
# CSocketConnection::Open() only does connect -> non-blocking -> TCP_NODELAY -> isConnected = true.
# There is no handshake, so accepting the connection and staying silent is enough; reads simply
# yield no keystrokes. Bots drive the game over the REST API instead.
#
# Ports come from C:\goldclub\ruleta\config\commconfig.cfg - '<keyboard> <device> <port>', station 1
# is 30300. One port per player station.
#
# Note this only holds while the watchdog ping is off. If 'hardware settings.additional.
# keyboardpanel.IsPing' is 1, or the bill dispenser is active (CHWBillDispenser::Init force-enables
# the ping), the core expects a reply to sCommands[31] and will lock the station as
# 'keyboard disconnected' regardless of this stub. Both must be off - see CLAUDE.md section 14.
#
# Every fault is swallowed and the listener rebuilt. An earlier version died on a single exception
# and left the process alive but not listening, which silently reintroduced ERROR 19 and cost hours
# of misdirected debugging - hence the paranoia, and the log.

param(
    [int[]] $Ports = @(30300),
    [string] $LogPath = "$PSScriptRoot\keyboard-stub.log"
)

function Write-Stub([string] $message) {
    $line = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $message
    Write-Host $line
    try { Add-Content -Path $LogPath -Value $line -Encoding utf8 } catch { }
}

$listeners = @{}
$clients = [Collections.ArrayList]::new()

function Confirm-Listener([int] $port) {
    if ($listeners.ContainsKey($port) -and $listeners[$port]) {
        try { $null = $listeners[$port].Server.LocalEndPoint; return } catch { }
    }
    try {
        $l = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $port)
        $l.Start()
        $listeners[$port] = $l
        Write-Stub "listening on 127.0.0.1:$port"
    } catch {
        $listeners[$port] = $null
        Write-Stub "FAILED to listen on $port : $($_.Exception.Message)"
    }
}

foreach ($p in $Ports) { Confirm-Listener $p }
Write-Stub "keyboard stub ready (ports: $($Ports -join ', '))"

while ($true) {
    foreach ($p in $Ports) {
        try {
            Confirm-Listener $p
            $l = $listeners[$p]
            if ($l -and $l.Pending()) {
                $c = $l.AcceptTcpClient()
                $c.NoDelay = $true
                $null = $clients.Add($c)
                Write-Stub "accepted on $p (open connections: $($clients.Count))"
            }
        } catch {
            Write-Stub "error on $p : $($_.Exception.Message) - rebuilding listener"
            try { if ($listeners[$p]) { $listeners[$p].Stop() } } catch { }
            $listeners[$p] = $null
        }
    }
    Start-Sleep -Milliseconds 100
}
