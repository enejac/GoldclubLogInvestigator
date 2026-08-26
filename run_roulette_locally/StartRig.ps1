# Brings the QA rig up, and refuses to pretend it is up when it is not.
#
#   .\StartRig.ps1              bring everything up and report ready / not ready
#   .\StartRig.ps1 -Check       preflight and health only, start nothing
#   .\StartRig.ps1 -Restart     stop the game first, then bring it up again
#
# Exit code 0 means the rig is ready to be driven by ActionBot. Anything else means a named step
# failed, and the message says which - that is the whole point of this script.
#
# Every check here exists because its absence once produced a symptom that pointed somewhere else
# entirely. A dead keyboard stub looks like a frontend rendering bug. A missing NUlid.dll looks like
# the API silently returning nothing. An AurumSetup.xml naming the wrong host looks like the game
# choosing to shut itself down. Guessing from the symptom cost a day; this script front-loads the
# answers. See CLAUDE.md section 14 for the full story behind each one.

param(
    [switch] $Check,
    [switch] $Restart,
    [string] $GameDir     = "C:\goldclub\ruleta",
    [string] $Url         = "http://127.0.0.1:8090",
    [int[]]  $KeyboardPorts = @(30300),
    [int]    $ApiTimeoutSec = 120
)

$ErrorActionPreference = "Stop"
$problems = [Collections.ArrayList]::new()

function Report([string] $state, [string] $name, [string] $detail) {
    $colour = "Gray"
    if ($state -eq "OK")   { $colour = "Green" }
    if ($state -eq "FAIL") { $colour = "Red" }
    if ($state -eq "WARN") { $colour = "Yellow" }
    Write-Host ("  [{0,-4}] {1,-34} {2}" -f $state, $name, $detail) -ForegroundColor $colour
}

function Fail([string] $name, [string] $detail) {
    Report "FAIL" $name $detail
    $null = $problems.Add("$name - $detail")
}

function Test-Listening([int] $port) {
    return [bool](netstat -ano | Select-String "LISTENING" | Select-String ":$port\s")
}

Write-Host ""
Write-Host "=== preflight ===" -ForegroundColor Cyan

# --- the deployment folder -------------------------------------------------------------------
$exe = Join-Path $GameDir "ruleta.exe"
if (Test-Path $exe) { Report "OK" "game executable" $exe }
else { Fail "game executable" "$exe not found" }

if (Test-Path (Join-Path $GameDir "Ruleta.exe.config")) { Report "OK" "Ruleta.exe.config" "present" }
else { Fail "Ruleta.exe.config" "missing - assembly probing paths come from here" }

# Detect Debug vs Release from CRT import names. Release without a WIBU dongle exits at Licence.dll.
$isDebugExe = $false
if (Test-Path $exe) {
    $exeBytes = [IO.File]::ReadAllBytes($exe)
    $exeAscii = [Text.Encoding]::ASCII.GetString($exeBytes)
    $isDebugExe = ($exeAscii.IndexOf("VCRUNTIME140D.dll") -ge 0) -or ($exeAscii.IndexOf("MSVCP140D.dll") -ge 0)
    if ($isDebugExe) { Report "OK" "ruleta build" "Debug CRT (dongle check compiled out)" }
    else { Fail "ruleta build" "Release CRT - without a physical WIBU dongle this exits at Licence.dll (see README section 2)" }
}

# Debug boost, delay-loaded by a Debug build. Missing -> 0xC06D007E and a silent exit with no log.
# Release builds use the non-gd boost DLLs already shipped next to the exe.
$debugBoost = @("boost_date_time-vc141-mt-gd-x64-1_67.dll", "boost_serialization-vc141-mt-gd-x64-1_67.dll")
$missingBoost = $debugBoost | Where-Object { -not (Test-Path (Join-Path $GameDir $_)) }
if (-not $isDebugExe) {
    Report "OK" "debug boost DLLs" "skipped - Release build uses mt-x64 boost"
} elseif ($missingBoost) {
    Fail "debug boost DLLs" "missing: $($missingBoost -join ', ') - causes 0xC06D007E before logging starts"
} else {
    Report "OK" "debug boost DLLs" "present"
}

# Probing-path assemblies. Missing -> no web server, or /api/data throwing on every call.
# Cabinet images often keep NUlid / Newtonsoft under versioned subfolders (Ruleta.exe.config probing).
$libCommon = Join-Path $GameDir "lib-common"
function Resolve-LibCommon([string[]] $candidates) {
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    return $null
}
$assemblies = [ordered]@{
    "Newtonsoft.Json.9.dll"              = @(
        "$libCommon\Newtonsoft.Json.9.dll"
    )
    "Newtonsoft.Json.dll (strong-named)" = @(
        "$libCommon\Newtonsoft.Json.dll",
        "$libCommon\newtonsoft.json\9.0.0.0\Newtonsoft.Json.dll",
        "$libCommon\Newtonsoft.Json\9.0.0.0\Newtonsoft.Json.dll"
    )
    "NUlid.dll"                          = @(
        "$libCommon\NUlid.dll",
        "$libCommon\NUlid\NUlid.dll"
    )
    "EmbedIO.dll"                        = @(
        "$libCommon\EmbedIO\3.4.3.0\EmbedIO.dll"
    )
    "Swan.Lite.dll"                      = @(
        "$libCommon\Swan.Lite\3.0.0.0\Swan.Lite.dll"
    )
}
$missingAsm = @()
foreach ($k in $assemblies.Keys) {
    if (-not (Resolve-LibCommon $assemblies[$k])) { $missingAsm += $k }
}
if ($missingAsm) { Fail "middleware assemblies" "missing: $($missingAsm -join ', ')" }
else { Report "OK" "middleware assemblies" "all present in lib-common" }

# Hard-coded in PayTable.h:349. Missing -> PayTable.cpp bare-throws during init.
$paytables = "C:\goldclub\config\etc\application\ruleta\paytables"
if (Test-Path $paytables) { Report "OK" "paytables" $paytables }
else { Fail "paytables" "$paytables not found - PayTable.cpp throws on the first JSON it cannot load" }

# AurumSetup selects its config by network host name. Wrong host -> CONFIG FOR GM2AU NOT FOUND!
$aurum = "C:\services\aurum\config\AurumSetup.xml"
if (Test-Path $aurum) {
    $hosts = ([regex]::Matches((Get-Content $aurum -Raw), '<NetworkHostName>([^<]*)</NetworkHostName>') |
              ForEach-Object { $_.Groups[1].Value })
    if ($hosts -contains $env:COMPUTERNAME) { Report "OK" "AurumSetup host entry" $env:COMPUTERNAME }
    else { Fail "AurumSetup host entry" "no <NetworkHostName> for '$env:COMPUTERNAME' (found: $($hosts -join ', ')) - the game will WM_STOPRULETA" }
} else {
    Fail "AurumSetup.xml" "$aurum not found - GCMessenger.Init throws CONFIG FOR GM2AU NOT FOUND!"
}

if ($problems.Count -gt 0) {
    Write-Host ""
    Write-Host "PREFLIGHT FAILED - not starting anything:" -ForegroundColor Red
    $problems | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 2
}

# --- keyboard stub ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== keyboard stub ===" -ForegroundColor Cyan
foreach ($port in $KeyboardPorts) {
    if (Test-Listening $port) {
        Report "OK" "port $port" "already listening"
    } elseif ($Check) {
        Fail "port $port" "nothing listening - the game will throw ERROR 19 and tear down"
    } else {
        $stub = Join-Path $PSScriptRoot "KeyboardStub.ps1"
        Start-Process powershell -ArgumentList @("-ExecutionPolicy","Bypass","-NoProfile","-File","`"$stub`"","-Ports",$port) -WindowStyle Minimized
        $waited = 0
        while ((-not (Test-Listening $port)) -and $waited -lt 15) { Start-Sleep -Seconds 1; $waited++ }
        if (Test-Listening $port) { Report "OK" "port $port" "stub started" }
        else { Fail "port $port" "stub did not come up within 15s" }
    }
}

# --- the game --------------------------------------------------------------------------------
Write-Host ""
Write-Host "=== game ===" -ForegroundColor Cyan
if ($Restart) {
    Get-Process ruleta, godot -ErrorAction SilentlyContinue | ForEach-Object { try { $_.Kill() } catch { } }
    Start-Sleep -Seconds 4
    Report "OK" "restart" "stopped any running game"
}

$gameUp = $true
$running = Get-Process ruleta -ErrorAction SilentlyContinue
if ($running) {
    Report "OK" "ruleta.exe" "already running (pid $($running.Id))"
} elseif ($Check) {
    Fail "ruleta.exe" "not running"
    $gameUp = $false
} else {
    $proc = Start-Process $exe -WorkingDirectory $GameDir -PassThru
    Report "OK" "ruleta.exe" "started (pid $($proc.Id))"
}

# --- the API ---------------------------------------------------------------------------------
Write-Host ""
Write-Host "=== middleware API ===" -ForegroundColor Cyan
if (-not $gameUp) {
    # No sense burning the full timeout waiting for a server whose process is not running.
    Fail "middleware API" "skipped - ruleta.exe is not running"
    Write-Host ""
    Write-Host "RIG NOT READY:" -ForegroundColor Red
    $problems | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}
$up = $false
$waited = 0
while (-not $up -and $waited -lt $ApiTimeoutSec) {
    try { $null = Invoke-WebRequest "$Url/api/whoami" -UseBasicParsing -TimeoutSec 5; $up = $true }
    catch { Start-Sleep -Seconds 3; $waited += 3 }
}
if ($up) {
    Report "OK" "$Url/api/whoami" "answering after ${waited}s"
    try {
        $r = Invoke-WebRequest "$Url/api/data/0" -UseBasicParsing -TimeoutSec 25
        $j = ($r.Content.TrimStart([char]0xFEFF) | ConvertFrom-Json)
        $pd = $j | Where-Object { $_.'$type' -match 'Player.PlayerData,' } | Select-Object -First 1
        $rg = $j | Where-Object { $_.'$type' -match 'Core.RouletteGame' } | Select-Object -First 1
        Report "OK" "/api/data/0" "$($r.RawContentLength) bytes, status=$($rg.Status), credits=$($pd.Credits.Credit)"
    } catch {
        Fail "/api/data/0" $_.Exception.Message
    }
} else {
    Fail "middleware API" "no answer on $Url within ${ApiTimeoutSec}s - check var\log\ruleta Roulette\ for ERR: lines"
}

Write-Host ""
if ($problems.Count -gt 0) {
    Write-Host "RIG NOT READY:" -ForegroundColor Red
    $problems | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}
Write-Host "RIG READY - ActionBot can be run against $Url" -ForegroundColor Green
exit 0
