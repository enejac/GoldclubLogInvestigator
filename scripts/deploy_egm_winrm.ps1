param([string]$ComputerName = "10.0.0.90", [string]$LocalDrive = "D", [string]$RemoteFolder = "ConfigScanner")
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
. (Join-Path $root "LabAccess.ps1")
$srcExe = Join-Path $root "LogInvestigator.exe"
if (-not (Test-Path $srcExe)) { $srcExe = Join-Path $root "dist\LogInvestigator.exe" }
if (-not (Test-Path $srcExe)) { throw "Build first: LogInvestigator.exe missing (run .\build_exe.ps1)" }
Ensure-LabWinRmReady -ComputerName $ComputerName | Out-Null
$cred = Get-LabCredential
$null = Initialize-LabWinRmTrustedHosts -Ip @($ComputerName)
$opt = New-PSSessionOption -OperationTimeout 300000 -OpenTimeout 30000
$session = New-PSSession -ComputerName $ComputerName -Credential $cred -Authentication Negotiate -SessionOption $opt
try {
  $prep = { param($Drive,$Folder)
    $dest = Join-Path "${Drive}:" $Folder
    New-Item -ItemType Directory -Path $dest, (Join-Path $dest "snapshots"), (Join-Path $dest "reports"), (Join-Path $dest "templates") -Force | Out-Null
    return $dest
  }
  $destRoot = Invoke-Command -Session $session -ScriptBlock $prep -ArgumentList $LocalDrive, $RemoteFolder
  $remoteExe = Join-Path $destRoot "LogInvestigator.exe"
  Copy-Item -LiteralPath $srcExe -Destination $remoteExe -ToSession $session -Force
  $verify = Invoke-Command -Session $session -ScriptBlock { param($p) Get-Item -LiteralPath $p | Select-Object FullName, Length, LastWriteTime } -ArgumentList $remoteExe
  Write-Host "Deployed via WinRM to $ComputerName"
  Write-Host "  Path: $($verify.FullName)"
  Write-Host "  Size: $([math]::Round($verify.Length/1MB,1)) MB"
  Write-Host "  Time: $($verify.LastWriteTime)"
} finally { Remove-PSSession $session -ErrorAction SilentlyContinue }
