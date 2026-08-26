# Shared helpers for 10.2 software copy (mounted drives).
# Dot-source from Fetch-Roulette102Software.ps1

$script:SoftwarePaths = @(
    'ruleta\Ruleta.exe',
    'ruleta\ruleta.exe',
    'ruleta\BuildVersion.txt',
    'ruleta\licence.dll',
    'ruleta\ruleta.exe.config',
    'ruleta\lib',
    'ruleta\lib-common',
    'ruleta\godot',
    'ruleta\apps',
    'ruleta\bin',
    'apps\godot\3.3.2.0-gc',
    'config\etc\application\ruleta\paytables',
    'config\etc\application\ruleta\godot.xml'
)

$script:PlatformPaths = @('services\aurum', 'bin')

$script:Markers102 = @(
    'ruleta\godot\RouletteGui.pck',
    'ruleta\godot\.mono\assemblies\RouletteGui2.dll',
    'ruleta\lib\GoldClub.ManagedRendererWebApiServer.dll',
    'ruleta\lib\GoldClub.ManagedRendererWebApiProxy.dll',
    'ruleta\apps\RouletteHistoryWebApi\RouletteHistoryWebApi.exe',
    'config\etc\application\ruleta\godot.xml',
    'config\etc\application\ruleta\paytables\paytable_single_zero.json',
    'config\etc\application\ruleta\paytables\paytable_premium.json'
)

$script:RequiredSourceMarkers = @(
    'ruleta\BuildVersion.txt',
    'ruleta\Ruleta.exe',
    'ruleta\godot\RouletteGui.pck'
)

$script:SourceSearchLetters = @('R', 'P', 'W', 'S', 'T', 'U', 'V', 'X', 'Y')
$script:DestSearchLetters = @('G', 'W', 'R', 'S', 'T', 'U', 'V', 'X', 'Y')

function Write-CopyLog {
    param([string]$Message, [string]$LogPath, [System.Text.UTF8Encoding]$Utf8)
    $line = "[$(Get-Date -Format o)] $Message"
    Write-Host $line
    if ($LogPath) {
        [IO.File]::AppendAllText($LogPath, $line + [Environment]::NewLine, $Utf8)
    }
}

function Get-DriveRootCandidates {
    param([string]$Letter)
    if (-not $Letter) { return @() }
    $root = "${Letter}:\"
    if (-not (Test-Path -LiteralPath $root)) { return @() }
    return @($root.TrimEnd('\'), (Join-Path $root 'goldclub'))
}

function Test-GoldclubRootCandidate {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $false }
    foreach ($marker in @('ruleta\Ruleta.exe', 'ruleta\ruleta.exe', 'ruleta\BuildVersion.txt', 'config')) {
        if (Test-Path -LiteralPath (Join-Path $Path $marker)) { return $true }
    }
    return $false
}

function Resolve-GoldclubRoot {
    param(
        [string]$Root,
        [string]$LogPath,
        [System.Text.UTF8Encoding]$Utf8
    )
    $base = $Root.TrimEnd('\')
    foreach ($candidate in @($base, (Join-Path $base 'goldclub'))) {
        if (Test-GoldclubRootCandidate -Path $candidate) {
            Write-CopyLog "Resolved goldclub root: $candidate" $LogPath $Utf8
            return $candidate
        }
    }
    throw "Not a goldclub root (need ruleta\ or config\): $Root"
}

function Read-BuildVersion {
    param([string]$GoldclubRoot)
    $bv = Join-Path $GoldclubRoot 'ruleta\BuildVersion.txt'
    if (-not (Test-Path -LiteralPath $bv)) { return $null }
    try {
        return (Get-Content -LiteralPath $bv -Raw -ErrorAction Stop).Trim()
    }
    catch {
        return $null
    }
}

function Test-VersionIs102 {
    param([string]$VersionText)
    if (-not $VersionText) { return $false }
    return ($VersionText -match '10\.2')
}

function Test-WriteAccess {
    param(
        [string]$Root,
        [System.Text.UTF8Encoding]$Utf8
    )
    $probe = Join-Path $Root ('_write_probe_{0}.tmp' -f [guid]::NewGuid().ToString('N'))
    try {
        [IO.File]::WriteAllText($probe, 'ok', $Utf8)
        Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
        return $true
    }
    catch {
        return $false
    }
}

function Get-GoldclubCandidateInfo {
    param(
        [string]$Path,
        [System.Text.UTF8Encoding]$Utf8
    )
    if (-not (Test-GoldclubRootCandidate -Path $Path)) { return $null }
    $bv = Read-BuildVersion -GoldclubRoot $Path
    $is102 = Test-VersionIs102 -VersionText $bv
    $writable = Test-WriteAccess -Root $Path -Utf8 $Utf8
    $freeGb = $null
    $letter = $Path.Substring(0, 1)
    $vol = Get-Volume -DriveLetter $letter -ErrorAction SilentlyContinue
    if ($vol) { $freeGb = [math]::Round($vol.SizeRemaining / 1GB, 1) }
    $markers = 0
    foreach ($m in $script:RequiredSourceMarkers) {
        if (Test-Path -LiteralPath (Join-Path $Path $m)) { $markers++ }
    }
    return [pscustomobject]@{
        Path = $Path
        Letter = $letter
        BuildVersion = $bv
        Is102 = $is102
        Writable = $writable
        FreeGb = $freeGb
        RequiredMarkers = $markers
    }
}

function Find-GoldclubRoots {
    param(
        [string[]]$Letters,
        [string[]]$ExtraPaths,
        [string]$LogPath,
        [System.Text.UTF8Encoding]$Utf8
    )
    $found = @()
    $seen = @{}

    foreach ($extra in $ExtraPaths) {
        if (-not $extra) { continue }
        $p = $extra.TrimEnd('\')
        if ($seen.ContainsKey($p.ToLowerInvariant())) { continue }
        $info = Get-GoldclubCandidateInfo -Path $p -Utf8 $Utf8
        if ($info) {
            $seen[$p.ToLowerInvariant()] = $true
            $found += $info
        }
    }

    foreach ($letter in $Letters) {
        foreach ($candidate in Get-DriveRootCandidates -Letter $letter) {
            $key = $candidate.ToLowerInvariant()
            if ($seen.ContainsKey($key)) { continue }
            $info = Get-GoldclubCandidateInfo -Path $candidate -Utf8 $Utf8
            if ($info) {
                $seen[$key] = $true
                $found += $info
            }
        }
    }
    return $found
}

function Select-SourceRoot {
    param(
        [System.Object[]]$Candidates,
        [string]$LogPath,
        [System.Text.UTF8Encoding]$Utf8
    )
    $preferred = $Candidates | Where-Object { $_.Is102 } | Sort-Object {
        $script:SourceSearchLetters.IndexOf($_.Letter.ToUpperInvariant())
    }, -RequiredMarkers -Descending
    if ($preferred -and $preferred.Count -gt 0) {
        $pick = $preferred[0]
        Write-CopyLog ("Auto source: {0} (BuildVersion: {1})" -f $pick.Path, $pick.BuildVersion) $LogPath $Utf8
        return $pick.Path
    }
    $fallback = $Candidates | Sort-Object RequiredMarkers -Descending | Select-Object -First 1
    if ($fallback) {
        Write-CopyLog ("WARN: no 10.2 BuildVersion found; using best candidate: {0} ({1})" -f $fallback.Path, $fallback.BuildVersion) $LogPath $Utf8
        return $fallback.Path
    }
    return $null
}

function Select-DestRoot {
    param(
        [System.Object[]]$Candidates,
        [string]$SourcePath,
        [string]$LogPath,
        [System.Text.UTF8Encoding]$Utf8
    )
    $srcKey = $SourcePath.ToLowerInvariant()
    $filtered = $Candidates | Where-Object {
        $_.Path.ToLowerInvariant() -ne $srcKey -and $_.Writable
    } | Sort-Object {
        $script:DestSearchLetters.IndexOf($_.Letter.ToUpperInvariant())
    }, -RequiredMarkers -Descending
    if ($filtered -and $filtered.Count -gt 0) {
        $pick = $filtered[0]
        Write-CopyLog ("Auto dest: {0} (writable, BuildVersion: {1})" -f $pick.Path, $pick.BuildVersion) $LogPath $Utf8
        return $pick.Path
    }
    $readOnly = $Candidates | Where-Object { $_.Path.ToLowerInvariant() -ne $srcKey } | Select-Object -First 1
    if ($readOnly) {
        throw "Destination found but not writable: $($readOnly.Path). Need write access to goldclub tree."
    }
    return $null
}

function Invoke-Preflight {
    param(
        [string]$SrcRoot,
        [string]$DstRoot,
        [string]$LogPath,
        [System.Text.UTF8Encoding]$Utf8,
        [switch]$Force
    )
    $srcBv = Read-BuildVersion -GoldclubRoot $SrcRoot
    $dstBv = Read-BuildVersion -GoldclubRoot $DstRoot
    Write-CopyLog "Preflight source BuildVersion: $srcBv" $LogPath $Utf8
    Write-CopyLog "Preflight dest   BuildVersion: $dstBv" $LogPath $Utf8

    if (-not (Test-VersionIs102 -VersionText $srcBv) -and -not $Force) {
        throw "Source does not look like 10.2 ($srcBv). Use -Force to copy anyway."
    }

    foreach ($m in $script:RequiredSourceMarkers) {
        $p = Join-Path $SrcRoot $m
        if (-not (Test-Path -LiteralPath $p)) {
            throw "Source missing required file: $m"
        }
    }

    if (-not (Test-WriteAccess -Root $DstRoot -Utf8 $Utf8)) {
        throw "Cannot write to destination: $DstRoot"
    }

    $dstLetter = $DstRoot.Substring(0, 1)
    $vol = Get-Volume -DriveLetter $dstLetter -ErrorAction SilentlyContinue
    if ($vol) {
        $free = [math]::Round($vol.SizeRemaining / 1GB, 1)
        Write-CopyLog "Dest free space: ${free} GB" $LogPath $Utf8
        if ($free -lt 2) {
            Write-CopyLog 'WARN: less than 2 GB free on destination' $LogPath $Utf8
        }
    }
}

function Copy-SoftwarePath {
    param(
        [string]$SrcRoot,
        [string]$DstRoot,
        [string]$Rel,
        [string]$LogPath,
        [switch]$WhatIf
    )
    $src = Join-Path $SrcRoot $Rel
    $dst = Join-Path $DstRoot $Rel
    if (-not (Test-Path -LiteralPath $src)) {
        Write-CopyLog "SKIP missing on source: $Rel" $LogPath $null
        return 'missing'
    }
    $isDir = Test-Path -LiteralPath $src -PathType Container
    $kind = if ($isDir) { 'DIR' } else { 'FILE' }
    Write-CopyLog "COPY $kind $Rel" $LogPath $null
    if ($WhatIf) { return 'ok' }

    $parent = Split-Path $dst -Parent
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }

    if ($isDir) {
        & robocopy $src $dst /E /IS /IT /R:3 /W:5 /XJ /NFL /NDL /NP /LOG+:$LogPath | Out-Null
        $rc = $LASTEXITCODE
        if ($rc -ge 8) { throw "robocopy failed: $Rel (exit $rc)" }
    }
    else {
        Copy-Item -LiteralPath $src -Destination $dst -Force
    }
    return 'ok'
}

function Test-PostCopyMarkers {
    param(
        [string]$DstRoot,
        [string]$LogPath
    )
    $found = 0
    foreach ($m in $script:Markers102) {
        $p = Join-Path $DstRoot $m
        if (Test-Path -LiteralPath $p) {
            Write-CopyLog "OK marker: $m" $LogPath $null
            $found++
        }
        else {
            Write-CopyLog "WARN marker missing: $m" $LogPath $null
        }
    }
    $bv = Read-BuildVersion -GoldclubRoot $DstRoot
    Write-CopyLog "Post-copy dest BuildVersion: $bv" $LogPath $null
    return $found
}
