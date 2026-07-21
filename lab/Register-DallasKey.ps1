<#
.SYNOPSIS
    Register (or remove) a Dallas key ROM in the EGM's HardwareConfig.xml so the
    cabinet authorises it -- mirrors the cabinet's own 21-DallasSetup.ps1 pattern
    (<Groups><string>Service</string></Groups>, <Unlock>true</Unlock>), but
    APPENDS alongside the existing keys instead of wiping them.

.DESCRIPTION
    BiOS2 validates Dallas keys against:
        HardwareConfig.xml -> /HardwareSettings/DallasKeySettings/Permissions
    If the exact <Code> is absent, the injected key resolves to no security group
    and is rejected. This adds a <DallasKey> block before the first </Permissions>
    (the key list), leaving the rest of the file byte-for-byte intact, and makes a
    timestamped backup first.

    NOTE: BiOS2 reads HardwareConfig.xml at startup. After registering, BiOS2 must
    re-read it (restart / respawn by Bootstrap) before the new key is honoured.
    Use -RestartBios2 to trigger that, or do it manually.

.PARAMETER Rom        The 16-hex-char key ROM to register. Default = our inject key.
.PARAMETER Group      Security group. Default 'Service' (full access).
.PARAMETER Unlock     Unlock-on-insert. Default $true (mirrors master key).
.PARAMETER Revert     Remove the entry for -Rom instead of adding it.
.PARAMETER RestartBios2  Kill BiOS2 so Bootstrap respawns it and re-reads the config.

.EXAMPLE
    .\Register-DallasKey.ps1 -ComputerName 10.0.0.90
.EXAMPLE
    .\Register-DallasKey.ps1 -ComputerName 10.0.0.90 -Revert
#>
[CmdletBinding()]
param(
    [string] $ComputerName = '10.0.0.90',
    [string] $Rom          = '01D68A721B000019',
    [string] $Group        = 'Service',
    [bool]   $Unlock       = $true,
    [string] $ConfigRelPath= 'Goldclub\Slot\themes\HardwareConfig.xml',
    [switch] $Revert,
    [switch] $RestartBios2,
    [string] $PsExecPath   = 'C:\Tools\PSTools\PsExec.exe'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Rom = $Rom.Trim().ToUpperInvariant()
$unc = "\\$ComputerName\c`$\$ConfigRelPath"

Write-Host ''
Write-Host '==========================================================' -ForegroundColor Cyan
Write-Host (' Register Dallas key  ({0})' -f $(if ($Revert) { 'REMOVE' } else { 'ADD' })) -ForegroundColor Cyan
Write-Host '==========================================================' -ForegroundColor Cyan
Write-Host (' Config : {0}' -f $unc) -ForegroundColor Gray
Write-Host (' ROM    : {0}' -f $Rom) -ForegroundColor Gray
if (-not $Revert) { Write-Host (' Group  : {0}   Unlock: {1}' -f $Group, $Unlock) -ForegroundColor Gray }
Write-Host '----------------------------------------------------------' -ForegroundColor Cyan

if (-not (Test-Path -LiteralPath $unc)) { throw "HardwareConfig.xml not found at $unc" }

# Read raw bytes; detect + preserve BOM and exact line endings.
$bytes  = [System.IO.File]::ReadAllBytes($unc)
$hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
$enc    = New-Object System.Text.UTF8Encoding($hasBom)
$raw    = $enc.GetString($bytes)
if ($hasBom -and $raw.Length -gt 0 -and $raw[0] -eq [char]0xFEFF) { $raw = $raw.Substring(1) }
$nl     = if ($raw -match "`r`n") { "`r`n" } else { "`n" }

$codeMarker = "<Code>$Rom</Code>"
$present = $raw -match [regex]::Escape($codeMarker)

# Sanity-parse before touching anything.
try { [xml]$doc = $raw } catch { throw "Existing config does not parse as XML: $($_.Exception.Message)" }
$permNode = $doc.SelectSingleNode('/HardwareSettings/DallasKeySettings/Permissions')
if (-not $permNode) { throw "Permissions node (/HardwareSettings/DallasKeySettings/Permissions) not found." }

Write-Host ' Current registered keys:' -ForegroundColor Gray
foreach ($k in $permNode.SelectNodes('DallasKey')) {
    $c = ($k.Code | Out-String).Trim()
    $g = ($k.Groups.InnerText | Out-String).Trim()
    $u = ($k.Unlock | Out-String).Trim()
    $mark = if ($c.ToUpperInvariant() -eq $Rom) { '  <== target' } else { '' }
    Write-Host ('   {0,-20} group={1,-10} unlock={2}{3}' -f $c, $g, $u, $mark) -ForegroundColor DarkGray
}

# Backup first.
$stamp     = Get-Date -Format 'yyyyMMdd_HHmmss'
$backupUnc = "$unc.keybak_$stamp"
Copy-Item -LiteralPath $unc -Destination $backupUnc -Force
Write-Host (' Backup : {0}' -f $backupUnc) -ForegroundColor Cyan

if ($Revert) {
    if (-not $present) { Write-Host ' Key not present; nothing to remove.' -ForegroundColor Yellow; return }
    # Remove the whole <DallasKey>...<Code>ROM</Code>...</DallasKey> block (+ leading whitespace).
    $pattern = '(?s)\s*<DallasKey>\s*<Code>' + [regex]::Escape($Rom) + '</Code>.*?</DallasKey>'
    $new = [regex]::Replace($raw, $pattern, '', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    if ($new -eq $raw) { throw "Could not locate the <DallasKey> block to remove." }
}
else {
    if ($present) { Write-Host ' Key already registered; no change needed (idempotent).' -ForegroundColor Green; return }
    $unlockText = ($Unlock.ToString().ToLowerInvariant())
    # Build block with the file's indentation style (6-space indent for DallasKey).
    $block =
        "      <DallasKey>$nl" +
        "        <Code>$Rom</Code>$nl" +
        "        <Groups>$nl" +
        "          <string>$Group</string>$nl" +
        "        </Groups>$nl" +
        "        <Unlock>$unlockText</Unlock>$nl" +
        "      </DallasKey>$nl"
    # Insert before the FIRST </Permissions> (the key list closes before DallasSecurityGroups).
    $idx = $raw.IndexOf('</Permissions>')
    if ($idx -lt 0) { throw "Could not find </Permissions> to insert before." }
    # Preserve the existing indentation that precedes that closing tag.
    $lineStart = $raw.LastIndexOf($nl, $idx) + $nl.Length
    $indent = $raw.Substring($lineStart, $idx - $lineStart)
    $new = $raw.Substring(0, $lineStart) + $block + $indent + $raw.Substring($idx)
}

# Validate the new content parses and reflects the intended change BEFORE writing.
try { [xml]$check = $new } catch { throw "Edited content failed XML parse; aborting (original untouched)." }
$checkPerm = $check.SelectSingleNode('/HardwareSettings/DallasKeySettings/Permissions')
$nowPresent = $false
foreach ($k in $checkPerm.SelectNodes('DallasKey')) { if ((($k.Code | Out-String).Trim().ToUpperInvariant()) -eq $Rom) { $nowPresent = $true } }
if (-not $Revert -and -not $nowPresent) { throw "Validation failed: key not present after edit." }
if ($Revert -and $nowPresent) { throw "Validation failed: key still present after removal." }

# Write atomically: temp file on the share, then move over the original.
$tmpUnc = "$unc.new_$stamp"
[System.IO.File]::WriteAllText($tmpUnc, $new, $enc)
Move-Item -LiteralPath $tmpUnc -Destination $unc -Force

# Confirm on disk.
[xml]$after = [System.IO.File]::ReadAllText($unc)
$afterPerm = $after.SelectSingleNode('/HardwareSettings/DallasKeySettings/Permissions')
Write-Host ''
Write-Host ' Keys after change:' -ForegroundColor Cyan
foreach ($k in $afterPerm.SelectNodes('DallasKey')) {
    $c = ($k.Code | Out-String).Trim()
    $g = ($k.Groups.InnerText | Out-String).Trim()
    $u = ($k.Unlock | Out-String).Trim()
    $mark = if ($c.ToUpperInvariant() -eq $Rom) { '  <== ours' } else { '' }
    Write-Host ('   {0,-20} group={1,-10} unlock={2}{3}' -f $c, $g, $u, $mark) -ForegroundColor Green
}

Write-Host ''
if ($Revert) { Write-Host ' Key removed.' -ForegroundColor Green }
else { Write-Host (' Key {0} registered as {1} (unlock={2}).' -f $Rom, $Group, $Unlock) -ForegroundColor Green }
Write-Host ' NOTE: BiOS2 caches this file at startup -- restart BiOS2 to load the change.' -ForegroundColor Yellow

if ($RestartBios2) {
    Write-Host ' Restarting BiOS2 so Bootstrap respawns it with the new key...' -ForegroundColor Cyan
    $enc2 = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes(@'
$ErrorActionPreference="SilentlyContinue"
$b = Get-Process BiOS2 -ErrorAction SilentlyContinue
if ($b) { $b | Stop-Process -Force; "KILLED BiOS2 pid=$($b.Id) (Bootstrap will respawn it)" } else { "BiOS2 not running" }
'@))
    & $PsExecPath "\\$ComputerName" -accepteula -s -n 30 powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand $enc2
}
