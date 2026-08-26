<#
.SYNOPSIS
    Restore Colombia (COP) roulette setup.xml on a lab cabinet.

.DESCRIPTION
    Re-encrypts the known-good plain reference at C:\Goldclub\tmp\setup.plain.xml
    (COP bill table, 2 player stations, currency format 128 + view format euro).

.EXAMPLE
    .\Set-RouletteCurrencyCOP.ps1 -ComputerName 10.0.0.90 -Apply -RestartStack
#>
[CmdletBinding()]
param(
    [string] $ComputerName = '10.0.0.90',
    [switch] $Apply,
    [switch] $RestartStack,
    [string] $ReferencePlain = 'C:\Goldclub\tmp\setup.plain.xml'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
. (Join-Path $repoRoot 'LabAccess.ps1')
$convertScript = Join-Path $repoRoot 'cabinet_tools\roulette\Convert-GcxmlSetup.ps1'

Copy-Item -LiteralPath $convertScript -Destination "\\$ComputerName\c$\Windows\Temp\Convert-GcxmlSetup.ps1" -Force

$result = Invoke-LabWinRmCommand -ComputerName $ComputerName -ScriptBlock {
    param($DoApply, $DoRestart, $ReferencePlain, $RemoteConvert)
    Set-StrictMode -Version Latest
    $ErrorActionPreference = 'Stop'
    if (-not (Test-Path -LiteralPath $ReferencePlain)) {
        throw "Missing reference plain setup: $ReferencePlain"
    }
    $enc = 'C:\Goldclub\config\etc\application\ruleta\setup.xml'
    $checks = Select-String -Path $ReferencePlain -Pattern 'view format|currency format|number of player stations|<node name="currency">COP</node>' |
        ForEach-Object { $_.Line.Trim() }
    if (-not $DoApply) {
        return [pscustomobject]@{ Applied = $false; Checks = $checks; Message = 'Dry run. Add -Apply to write setup.xml.' }
    }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    Copy-Item -LiteralPath $enc -Destination "$enc.bak-cop-$stamp" -Force
    & $RemoteConvert -Encrypt -EncryptedPath $enc -PlainPath $ReferencePlain -SettingsDll 'C:\Goldclub\bin\lib\GoldClub.Settings.dll' | Out-Null
    $restarted = $false
    if ($DoRestart) {
        $rf = 'C:\Goldclub\tools\stack\Run-FullStack.ps1'
        if (Test-Path -LiteralPath $rf) {
            & $rf -AlreadyElevated | Out-Null
            $restarted = $true
        }
    }
    return [pscustomobject]@{
        Applied = $true
        Checks = $checks
        Restarted = $restarted
        Backup = "$enc.bak-cop-$stamp"
        Message = 'Colombia reference setup.xml restored.'
    }
} -ArgumentList @([bool]$Apply, [bool]$RestartStack, $ReferencePlain, 'C:\Windows\Temp\Convert-GcxmlSetup.ps1')

Write-Host ''
Write-Host '=== Restore Colombia roulette setup ===' -ForegroundColor Cyan
$result | Format-List
if (-not $Apply) {
    Write-Host 'Dry run only. Re-run with -Apply [-RestartStack].' -ForegroundColor Yellow
}