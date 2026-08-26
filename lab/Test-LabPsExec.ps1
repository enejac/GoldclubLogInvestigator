<#
.SYNOPSIS
    Quick PsExec smoke test for a lab cabinet (especially slow 10.0.0.171).
#>
param(
    [string] $Computer = '10.0.0.171',
    [string] $PsExecPath = 'C:\Tools\PSTools\PsExec.exe',
    [switch] $Session1Start
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'
. (Join-Path (Split-Path $PSScriptRoot -Parent) 'LabAccess.ps1')
$auth = @(Get-LabPsExecArgs)
$smb = Test-LabSmbAccess -Ip $Computer
Write-Host "[SMB] $($smb.Status): $($smb.Detail)"
if (-not (Test-Path -LiteralPath $PsExecPath)) { throw "PsExec not found: $PsExecPath" }
$sw = [Diagnostics.Stopwatch]::StartNew()
if ($Session1Start) {
    & $PsExecPath "\\$Computer" -accepteula @auth -i 1 -d cmd /c start cmd
} else {
    & $PsExecPath "\\$Computer" -accepteula @auth -s cmd /c echo PSEXEC_OK
}
$sw.Stop()
Write-Host "[PsExec] exit=$LASTEXITCODE elapsed=$([math]::Round($sw.Elapsed.TotalSeconds,1))s"
