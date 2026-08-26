<#
.SYNOPSIS
    Copy 10.2 roulette software onto pre-mounted goldclub drives (no admin required).

.DESCRIPTION
    Designed for lab PCs where UAC elevation is blocked. Assumes source and dest
    goldclub trees are already mounted (VHD interior on R:, BIWIN on G:, etc.).

    Auto-detects drives when -SourceGoldclubRoot / -DestGoldclubRoot are omitted.
    Also checks USB staging: <tool>\staging\10.2\

.PARAMETER SourceGoldclubRoot
    Mounted 10.2 goldclub root (must contain ruleta\BuildVersion.txt with 10.2).

.PARAMETER DestGoldclubRoot
    Writable destination goldclub root (typically 10.1 cabinet on G:).

.PARAMETER IncludePlatform
    Also copy services\aurum\ and bin\.

.PARAMETER WhatIf
    Preflight + list copies only.

.PARAMETER Force
    Copy even if source BuildVersion does not contain 10.2.

.PARAMETER NoAutoDetect
    Fail if explicit source/dest paths are not provided.

.EXAMPLE
    .\Fetch-Roulette102Software.ps1

.EXAMPLE
    .\Fetch-Roulette102Software.ps1 -SourceGoldclubRoot R:\ -DestGoldclubRoot G:\ -WhatIf
#>
param(
    [string] $SourceGoldclubRoot,
    [string] $DestGoldclubRoot,
    [switch] $IncludePlatform,
    [switch] $WhatIf,
    [switch] $Force,
    [switch] $NoAutoDetect
)

$ErrorActionPreference = 'Stop'
$ToolRoot = $PSScriptRoot
$LogPath = Join-Path $ToolRoot 'fetch-roulette102-software.log'
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$Common = Join-Path $ToolRoot 'Fetch-Roulette102Software.Common.ps1'
if (-not (Test-Path -LiteralPath $Common)) {
    throw "Missing helper: $Common"
}
. $Common

function Write-Log([string]$Message) {
    Write-CopyLog -Message $Message -LogPath $LogPath -Utf8 $Utf8
}

[IO.File]::WriteAllText($LogPath, '', $Utf8)
Write-Log '=== Copy 10.2 software (pre-mounted drives, no admin) ==='
Write-Log 'Copies: ruleta binaries, godot, apps, paytables JSON, godot.xml'
Write-Log 'Does NOT copy: setup.xml, aurum options, auth, HW config'

$extraPaths = @(
    (Join-Path $ToolRoot 'staging\10.2'),
    (Join-Path $ToolRoot 'staging\102')
)

$all = Find-GoldclubRoots -Letters ($script:SourceSearchLetters + $script:DestSearchLetters | Select-Object -Unique) `
    -ExtraPaths $extraPaths -LogPath $LogPath -Utf8 $Utf8

if ($all.Count -gt 0) {
    Write-Log '--- Detected goldclub roots ---'
    foreach ($c in $all) {
        Write-Log ("  {0}  ver={1}  10.2={2}  writable={3}  free={4}GB  markers={5}/3" -f `
            $c.Path, $c.BuildVersion, $c.Is102, $c.Writable, $c.FreeGb, $c.RequiredMarkers)
    }
}
else {
    Write-Log 'No goldclub roots auto-detected on mounted drives'
}

if ($SourceGoldclubRoot) {
    $srcRoot = Resolve-GoldclubRoot -Root $SourceGoldclubRoot -LogPath $LogPath -Utf8 $Utf8
}
elseif ($NoAutoDetect) {
    throw 'Required: -SourceGoldclubRoot (or omit -NoAutoDetect for auto-find)'
}
else {
    $srcRoot = Select-SourceRoot -Candidates $all -LogPath $LogPath -Utf8 $Utf8
    if (-not $srcRoot) {
        throw 'No 10.2 source found. Mount 10.2 goldclub (e.g. R:\) or stage to H:\ConfigScanner\staging\10.2\'
    }
}

if ($DestGoldclubRoot) {
    $dstRoot = Resolve-GoldclubRoot -Root $DestGoldclubRoot -LogPath $LogPath -Utf8 $Utf8
}
elseif ($NoAutoDetect) {
    throw 'Required: -DestGoldclubRoot (or omit -NoAutoDetect for auto-find)'
}
else {
    $dstRoot = Select-DestRoot -Candidates $all -SourcePath $srcRoot -LogPath $LogPath -Utf8 $Utf8
    if (-not $dstRoot) {
        throw 'No writable destination found. Mount target goldclub on G:\ (or specify -DestGoldclubRoot)'
    }
}

Write-Log "Source: $srcRoot"
Write-Log "Dest:   $dstRoot"

if ($srcRoot.ToLowerInvariant() -eq $dstRoot.ToLowerInvariant()) {
    throw 'Source and destination are the same path'
}

Invoke-Preflight -SrcRoot $srcRoot -DstRoot $dstRoot -LogPath $LogPath -Utf8 $Utf8 -Force:$Force

$paths = [System.Collections.Generic.List[string]]::new()
$paths.AddRange($script:SoftwarePaths)
if ($IncludePlatform) {
    $paths.AddRange($script:PlatformPaths)
    Write-Log 'IncludePlatform: services\aurum, bin'
}

$ok = 0
$missing = 0
$failures = @()
foreach ($rel in $paths) {
    try {
        $r = Copy-SoftwarePath -SrcRoot $srcRoot -DstRoot $dstRoot -Rel $rel -LogPath $LogPath -WhatIf:$WhatIf
        if ($r -eq 'ok') { $ok++ } else { $missing++ }
    }
    catch {
        $failures += $rel
        Write-Log "FAIL $rel : $($_.Exception.Message)"
    }
}

Write-Log "Copied/queued: $ok, missing on source: $missing, failures: $($failures.Count)"
if ($failures.Count -gt 0) {
    throw ("Copy failed for: {0}" -f ($failures -join ', '))
}

if (-not $WhatIf) {
    $found = Test-PostCopyMarkers -DstRoot $dstRoot -LogPath $LogPath
    Write-Log "Post-check markers: $found / $($script:Markers102.Count)"
    if ($found -lt 5) {
        Write-Log 'WARN: fewer than 5 post-copy markers present - review log'
    }
}
else {
    Write-Log 'WhatIf: no files written'
}

Write-Log '=== DONE ==='
exit 0
