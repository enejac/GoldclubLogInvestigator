# Quarantine USB .zip files that trigger Windows "Multi-Volume set" shell prompts.
# Typical cause: truncated TeamViewer Update_*.zip downloads (PK header, no EOCD)
# or GoldClub SystemUpdate payloads saved with a .zip extension but custom encryption.
param(
    [string]$UsbRoot = '',
    [switch]$Apply,
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'

function Write-Info([string]$Message) {
    if (-not $Quiet) { Write-Host $Message }
}

function Resolve-UsbRoot([string]$Hint) {
    if ($Hint -and (Test-Path -LiteralPath $Hint)) {
        return $Hint.TrimEnd('\')
    }
    $marker = 'TeamViewer_LoginBackup\restore_tv_login.cmd'
    foreach ($psd in Get-PSDrive -PSProvider FileSystem) {
        $candidate = Join-Path $psd.Root $marker
        if (Test-Path -LiteralPath $candidate) {
            return $psd.Root.TrimEnd('\')
        }
    }
    $vol = Get-Volume -ErrorAction SilentlyContinue | Where-Object {
        $_.DriveLetter -and $_.FileSystemLabel -eq 'USB'
    } | Select-Object -First 1
    if ($vol) { return "$($vol.DriveLetter):" }
    throw 'USB root not found (pass -UsbRoot or connect the GoldClub USB stick).'
}

function Test-ZipHasEocd([byte[]]$Bytes) {
    if ($Bytes.Length -lt 22) { return $false }
    $scanFrom = [Math]::Max(0, $Bytes.Length - 65557)
    for ($i = $Bytes.Length - 22; $i -ge $scanFrom; $i--) {
        if ($Bytes[$i] -eq 0x50 -and $Bytes[$i + 1] -eq 0x4B -and $Bytes[$i + 2] -eq 0x05 -and $Bytes[$i + 3] -eq 0x06) {
            return $true
        }
    }
    return $false
}

function Get-ZipShellIssue([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    $item = Get-Item -LiteralPath $Path
    if ($item.Extension -notin '.zip', '.ZIP') { return $null }

    $maxRead = [Math]::Min([int64]$item.Length, 256MB)
    if ($maxRead -lt 4) {
        return [PSCustomObject]@{
            Path   = $Path
            Reason = 'empty zip extension'
            Action = 'quarantine'
        }
    }

    $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try {
        $buf = New-Object byte[] $maxRead
        [void]$fs.Read($buf, 0, $maxRead)
    }
    finally {
        $fs.Dispose()
    }

    $isPk = ($buf[0] -eq 0x50 -and $buf[1] -eq 0x4B)
    if (-not $isPk) {
        $ws = [System.IO.Path]::ChangeExtension($Path, '.ws')
        if (Test-Path -LiteralPath $ws) {
            return [PSCustomObject]@{
                Path   = $Path
                Reason = 'non-ZIP payload with duplicate .ws sibling'
                Action = 'rename_gspack'
            }
        }
        return [PSCustomObject]@{
            Path   = $Path
            Reason = 'non-ZIP payload with .zip extension'
            Action = 'rename_gspack'
        }
    }

    if (-not (Test-ZipHasEocd $buf)) {
        return [PSCustomObject]@{
            Path   = $Path
            Reason = 'truncated/incomplete ZIP (missing end-of-central-directory)'
            Action = 'quarantine'
        }
    }

    return $null
}

$root = Resolve-UsbRoot $UsbRoot
$quarantine = Join-Path $root '_quarantine_bad_zip'
$issues = @()

Get-ChildItem -LiteralPath $root -Recurse -File -Filter '*.zip' -ErrorAction SilentlyContinue | ForEach-Object {
    $issue = Get-ZipShellIssue $_.FullName
    if ($issue) { $issues += $issue }
}

if (-not $issues) {
    Write-Info "No shell-problematic .zip files under $root"
    return
}

foreach ($issue in $issues) {
    Write-Info ("FOUND: {0}`n       {1}" -f $issue.Path, $issue.Reason)
    if (-not $Apply) { continue }

    switch ($issue.Action) {
        'rename_gspack' {
            $dest = [System.IO.Path]::ChangeExtension($issue.Path, '.gspack')
            if (Test-Path -LiteralPath $dest) {
                $dest = "$dest.bak"
            }
            Rename-Item -LiteralPath $issue.Path -NewName ([System.IO.Path]::GetFileName($dest))
            Write-Info "RENAMED -> $dest"
        }
        default {
            if (-not (Test-Path -LiteralPath $quarantine)) {
                New-Item -ItemType Directory -Path $quarantine | Out-Null
            }
            $leaf = Split-Path -Leaf $issue.Path
            $dest = Join-Path $quarantine $leaf
            if (Test-Path -LiteralPath $dest) {
                $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
                $dest = Join-Path $quarantine ($stamp + '_' + $leaf)
            }
            Move-Item -LiteralPath $issue.Path -Destination $dest
            Write-Info "QUARANTINED -> $dest"
        }
    }
}

if (-not $Apply) {
    Write-Info ''
    Write-Info 'Dry run only. Re-run with -Apply to quarantine/rename the files above.'
}
