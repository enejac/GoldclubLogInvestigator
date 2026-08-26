# Mount USB (retry) and copy Alegro 10.2 image to {USB}\_images\
param(
    [int]$MaxAttempts = 15,
    [int]$WaitSeconds = 12,
    [switch]$VerifyHash,
    [switch]$MacriumImages
)

$ErrorActionPreference = 'Continue'
$sourceDir = 'C:\WIN_SYSTEMS\Images'
$MacriumSpacedName = 'Alegro 10.2 128GB BIWIN.mrimg'

function Find-SourceFile {
    $files = @(Get-ChildItem -Path $sourceDir -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match 'alegro' -and $_.Name -match '10\.2' })
    if ($files.Count -eq 0) { return $null }
    $preferred = $files | Where-Object { $_.Name -match 'Alegro_10\.2' } | Sort-Object Length -Descending | Select-Object -First 1
    if ($preferred) { return $preferred }
    return $files | Sort-Object Length -Descending | Select-Object -First 1
}

function Test-FileSizeMatch {
    param([string]$Path, [long]$ExpectedBytes)
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    return ((Get-Item -LiteralPath $Path).Length -eq $ExpectedBytes)
}

function Ensure-MacriumSpacedAlias {
    param([string]$DestDir, [System.IO.FileInfo]$Source)
    $canonical = Join-Path $DestDir $Source.Name
    $alias = Join-Path $DestDir $MacriumSpacedName
    if ($Source.Name -eq $MacriumSpacedName) { return @{ Path = $alias; Skipped = $true } }
    if (-not (Test-Path -LiteralPath $canonical)) { return @{ Path = $alias; Success = $false } }
    if (Test-FileSizeMatch -Path $alias -ExpectedBytes $Source.Length) {
        $canonicalPath = Join-Path $DestDir $Source.Name
        if (Test-Path -LiteralPath $canonicalPath) {
            $cHash = (Get-FileHash -LiteralPath $canonicalPath -Algorithm SHA256).Hash
            $aHash = (Get-FileHash -LiteralPath $alias -Algorithm SHA256).Hash
            if ($cHash -eq $aHash) {
                return @{ Path = $alias; Skipped = $true; Success = $true }
            }
            Write-Host 'WARN: alias size OK but hash bad — recopying' -ForegroundColor Yellow
            Remove-Item -LiteralPath $alias -Force
        } else {
            return @{ Path = $alias; Skipped = $true; Success = $true }
        }
    }
    if (Test-Path -LiteralPath $alias) { Remove-Item -LiteralPath $alias -Force }
    Write-Host "Creating Macrium alias: $MacriumSpacedName"
    Copy-Item -LiteralPath $canonical -Destination $alias -Force
    if (-not (Test-FileSizeMatch -Path $alias -ExpectedBytes $Source.Length)) {
        return @{ Path = $alias; Skipped = $false; Success = $false }
    }
    $canonicalHash = (Get-FileHash -LiteralPath $canonical -Algorithm SHA256).Hash
    $aliasHash = (Get-FileHash -LiteralPath $alias -Algorithm SHA256).Hash
    $ok = ($canonicalHash -eq $aliasHash)
    if (-not $ok) {
        Write-Host 'WARN: alias hash mismatch after copy; removing bad alias' -ForegroundColor Yellow
        Remove-Item -LiteralPath $alias -Force -ErrorAction SilentlyContinue
    }
    return @{ Path = $alias; Skipped = $false; Success = $ok }
}

function Invoke-UsbMountFix {
    $fixScript = Join-Path $PSScriptRoot 'Fix-UsbMount.ps1'
    if (-not (Test-Path $fixScript)) { return $false }
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
    if ($isAdmin) {
        & $fixScript
        return $true
    }
    $taskName = 'LogInvestigator-UsbMountFix'
    $arg = "-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File `"$fixScript`""
    schtasks /Create /TN $taskName /TR "powershell.exe $arg" /SC ONCE /ST 00:00 /RL HIGHEST /F 2>$null | Out-Null
    schtasks /Run /TN $taskName 2>$null | Out-Null
    Start-Sleep -Seconds 8
    schtasks /Delete /TN $taskName /F 2>$null | Out-Null
    return $true
}

function Find-UsbDrive {
    if ((Test-Path 'H:\') -and (Test-Path 'H:\ConfigScanner')) {
        return @{ Letter = 'H'; Path = 'H:\'; Reason = 'H:\ConfigScanner' }
    }
    if (Test-Path 'H:\') {
        $vol = Get-Volume -DriveLetter H -ErrorAction SilentlyContinue
        if ($vol -and $vol.DriveType -eq 'Removable') {
            return @{ Letter = 'H'; Path = 'H:\'; Reason = 'H: removable' }
        }
    }
    foreach ($vol in Get-Volume -ErrorAction SilentlyContinue | Where-Object { $_.DriveLetter -and $_.DriveType -eq 'Removable' }) {
        $letter = [string]$vol.DriveLetter
        $root = "${letter}:\"
        $reason = 'removable volume'
        if (Test-Path (Join-Path $root 'ConfigScanner')) { $reason = 'ConfigScanner folder' }
        return @{ Letter = $letter; Path = $root; Reason = $reason }
    }
    return $null
}

function Copy-ImageToUsb {
    param([string]$UsbRoot, [System.IO.FileInfo]$Source)
    $destDir = Join-Path $UsbRoot '_images'
    if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir -Force | Out-Null }
    $destFile = Join-Path $destDir $Source.Name
    if (Test-FileSizeMatch -Path $destFile -ExpectedBytes $Source.Length) {
        return @{ Success = $true; Skipped = $true; Dest = $destFile; Bytes = $Source.Length; Seconds = 0 }
    }
    if (Test-Path -LiteralPath $destFile) { Remove-Item -LiteralPath $destFile -Force }
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    & robocopy $Source.DirectoryName $destDir $Source.Name /Z /R:2 /W:3 /NP
    $sw.Stop()
    $exit = $LASTEXITCODE
    $ok = ($exit -ge 0 -and $exit -le 7)
    if (-not $ok -or -not (Test-Path -LiteralPath $destFile)) {
        return @{ Success = $false; Dest = $destFile; Bytes = 0; Seconds = $sw.Elapsed.TotalSeconds }
    }
    $finalLen = (Get-Item -LiteralPath $destFile).Length
    return @{ Success = ($finalLen -eq $Source.Length); Skipped = $false; Dest = $destFile; Bytes = $finalLen; Seconds = [math]::Round($sw.Elapsed.TotalSeconds,1) }
}

function Copy-ToMacriumImages {
    param([string]$UsbRoot, [System.IO.FileInfo]$Source, [string]$CanonicalDest)
    if (-not $MacriumImages) { return @{ Skipped = $true } }
    $destDir = Join-Path $UsbRoot 'MacriumImages'
    if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir -Force | Out-Null }
    $destFile = Join-Path $destDir $Source.Name
    if (Test-FileSizeMatch -Path $destFile -ExpectedBytes $Source.Length) {
        return @{ Success = $true; Skipped = $true; Dest = $destFile }
    }
    Write-Host "Copying to $destFile ..."
    Copy-Item -LiteralPath $CanonicalDest -Destination $destFile -Force
    $ok = Test-FileSizeMatch -Path $destFile -ExpectedBytes $Source.Length
    return @{ Success = $ok; Skipped = $false; Dest = $destFile }
}

Write-Host '=== Alegro 10.2 -> USB _images ===' -ForegroundColor Cyan
$source = Find-SourceFile
if (-not $source) { Write-Host "FAIL: no source in $sourceDir"; exit 2 }
Write-Host "Source: $($source.FullName) ($([math]::Round($source.Length/1GB,2)) GB)"

$usb = $null
for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
    Write-Host "--- Attempt $attempt / $MaxAttempts ---"
    $usb = Find-UsbDrive
    if ($usb -and $usb.Path) {
        Write-Host "USB found: $($usb.Letter): ($($usb.Reason))" -ForegroundColor Green
        break
    }
    $pnpError = Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object { $_.FriendlyName -match 'Samsung|Mass Storage' -and $_.Status -eq 'Error' }
    if ($pnpError) {
        Write-Host 'PnP Error on USB Mass Storage. Running mount fix...'
        if ($attempt -eq 3 -or $attempt -eq 7) {
            Write-Host 'TIP: Unplug USB, wait 5s, try USB 2.0 port, replug. Device Manager -> Disable/Enable Samsung.' -ForegroundColor Yellow
        }
    } else {
        Write-Host 'No removable USB yet. Running mount fix...'
    }
    Invoke-UsbMountFix | Out-Null
    if ($attempt -lt $MaxAttempts) { Start-Sleep -Seconds $WaitSeconds }
}

if (-not $usb -or -not $usb.Path) {
    Write-Host "FAIL: USB not mounted after $MaxAttempts attempts."
    exit 3
}

$result = Copy-ImageToUsb -UsbRoot $usb.Path -Source $source
if (-not $result.Success) {
    Write-Host 'FAIL: primary copy failed' -ForegroundColor Red
    exit 4
}

$destDir = Join-Path $usb.Path '_images'
$alias = Ensure-MacriumSpacedAlias -DestDir $destDir -Source $source
$macrium = Copy-ToMacriumImages -UsbRoot $usb.Path -Source $source -CanonicalDest $result.Dest

$integrityOk = Test-FileSizeMatch -Path $result.Dest -ExpectedBytes $source.Length
if ($VerifyHash -and $integrityOk) {
    Write-Host 'Verifying SHA256 (source vs USB)...'
    $srcHash = (Get-FileHash -LiteralPath $source.FullName -Algorithm SHA256).Hash
    $dstHash = (Get-FileHash -LiteralPath $result.Dest -Algorithm SHA256).Hash
    $integrityOk = ($srcHash -eq $dstHash)
    Write-Host "Hash match: $integrityOk"
}

Write-Host '=== SUMMARY ===' -ForegroundColor Cyan
Write-Host "USB: $($usb.Letter):"
Write-Host "Source: $($source.FullName)"
Write-Host "Dest: $($result.Dest)"
Write-Host "Macrium alias: $($alias.Path) (ok=$($alias.Success))"
if ($MacriumImages) { Write-Host "MacriumImages: $($macrium.Dest) (ok=$($macrium.Success))" }
Write-Host "Bytes: $($result.Bytes) / $($source.Length)"
Write-Host "Time: $($result.Seconds)s"
Write-Host "Retries: $($attempt - 1)"
if ($result.Success -and $alias.Success -and $integrityOk) {
    Write-Host 'SUCCESS' -ForegroundColor Green
    Write-Host "In Macrium PE browse: $($usb.Letter)\_images\$MacriumSpacedName (NOT D:\_images)" -ForegroundColor Cyan
    exit 0
}
Write-Host 'FAIL' -ForegroundColor Red
exit 4