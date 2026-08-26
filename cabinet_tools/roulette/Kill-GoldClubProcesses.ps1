# Forwarder: Kill-GoldClubProcesses is merged into Kill-All.ps1
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [object[]] $RemainingArgs,
    [switch] $WhatIf,
    [switch] $AlreadyElevated,
    [switch] $GameOnly
)
$ErrorActionPreference = 'Stop'
$target = Join-Path $PSScriptRoot 'Kill-All.ps1'
if (-not (Test-Path -LiteralPath $target)) { throw "Missing $target" }
Write-Host 'Kill-GoldClubProcesses.ps1 -> Kill-All.ps1 (merged)' -ForegroundColor Cyan
$fwd = @{}
if ($WhatIf) { $fwd.WhatIf = $true }
if ($AlreadyElevated) { $fwd.AlreadyElevated = $true }
if ($GameOnly) { $fwd.GameOnly = $true }
& $target @fwd @RemainingArgs
exit $LASTEXITCODE
