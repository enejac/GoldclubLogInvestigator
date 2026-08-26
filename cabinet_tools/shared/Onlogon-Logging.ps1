# Shared onlogon run log - USB first (for black-screen debug), then script dir, then cabinet.
$script:OnlogonLogInitialized = $false
$script:OnlogonLogPaths = @()

function Find-OnlogonUsbRoots {
    $roots = New-Object System.Collections.Generic.List[string]
    $marker = 'TeamViewer_LoginBackup\restore_tv_login.cmd'
    foreach ($psd in Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue) {
        $root = $psd.Root.TrimEnd('\')
        if (-not $root) { continue }
        if (Test-Path -LiteralPath (Join-Path $root $marker)) {
            [void]$roots.Add($root)
        }
    }
    try {
        Get-Volume -ErrorAction SilentlyContinue | Where-Object {
            $_.DriveLetter -and $_.FileSystemLabel -eq 'USB'
        } | ForEach-Object {
            $root = "$($_.DriveLetter):"
            if ((Test-Path -LiteralPath (Join-Path $root $marker)) -and ($roots -notcontains $root)) {
                [void]$roots.Add($root)
            }
        }
    }
    catch {}
    return ,$roots.ToArray()
}

function Initialize-OnlogonLog {
    param([string]$ScriptRoot = $PSScriptRoot)
    if ($script:OnlogonLogInitialized) { return @($script:OnlogonLogPaths) }
    $paths = New-Object System.Collections.Generic.List[string]

    # 1) USB stick logs (primary when debugging black screen)
    foreach ($usbRoot in (Find-OnlogonUsbRoots)) {
        [void]$paths.Add((Join-Path $usbRoot 'onlogon-run.log'))
        $usbScripts = Join-Path $usbRoot 'usb_scripts'
        if (Test-Path -LiteralPath $usbScripts) {
            [void]$paths.Add((Join-Path $usbScripts 'onlogon-run.log'))
        }
    }

    # 2) Same folder as this onlogon.ps1 (wherever it was launched from)
    if ($ScriptRoot) {
        [void]$paths.Add((Join-Path $ScriptRoot 'onlogon-run.log'))
    }

    # 3) Cabinet log dir (optional)
    [void]$paths.Add('C:\goldclub\var\log\onlogon.log')

    $script:OnlogonLogPaths = $paths | Select-Object -Unique
    foreach ($logPath in $script:OnlogonLogPaths) {
        try {
            $dir = Split-Path -Parent $logPath
            if ($dir -and -not (Test-Path -LiteralPath $dir)) {
                New-Item -ItemType Directory -Path $dir -Force | Out-Null
            }
            if (-not (Test-Path -LiteralPath $logPath)) {
                $utf8 = [System.Text.UTF8Encoding]::new($false)
                [IO.File]::WriteAllText($logPath, '', $utf8)
            }
        }
        catch {}
    }
    $script:OnlogonLogInitialized = $true
    return @($script:OnlogonLogPaths)
}

function Write-OnlogonLog {
    param([string]$Message)
    if (-not $script:OnlogonLogInitialized) { Initialize-OnlogonLog | Out-Null }
    $line = "[$(Get-Date -Format o)] $Message"
    $utf8 = [System.Text.UTF8Encoding]::new($false)
    foreach ($logPath in $script:OnlogonLogPaths) {
        try {
            [IO.File]::AppendAllText($logPath, $line + [Environment]::NewLine, $utf8)
        }
        catch {}
    }
    Write-Host $line
}

    foreach ($usbRoot in (Find-OnlogonUsbRoots)) {
        [void]$targets.Add((Join-Path $usbRoot 'onlogon-run.log'))
        $usbScripts = Join-Path $usbRoot 'usb_scripts'
        if (Test-Path -LiteralPath $usbScripts) {
            [void]$targets.Add((Join-Path $usbScripts 'onlogon-run.log'))
        }
    }
    foreach ($logPath in ($targets | Select-Object -Unique)) {
        try {
            $dir = Split-Path -Parent $logPath
            if ($dir -and -not (Test-Path -LiteralPath $dir)) {
                New-Item -ItemType Directory -Path $dir -Force | Out-Null
            }
            [IO.File]::AppendAllText($logPath, $line, $utf8)
        }
        catch {}
    }
    Write-Host $line.TrimEnd()
}