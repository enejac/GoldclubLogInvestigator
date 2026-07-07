param(
    [string] $IP = '10.0.0.90',
    [int64]  $Amount = 100000
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = 'C:\Users\Ezbogar\GoldclubLogInvestigator'
$injectScript = Join-Path $repoRoot 'Invoke-WinDivertAft.ps1'

& $injectScript -Send -ComputerName $IP -Amount $Amount
exit $LASTEXITCODE