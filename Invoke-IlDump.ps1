<#
.SYNOPSIS
    Compile + run IlDump.exe on the cabinet to disassemble an Aurum method.

.EXAMPLE
    .\Invoke-IlDump.ps1 -TypeName GoldClub.Aurum.WATmanager -MethodName RequestTransferPosted
#>
[CmdletBinding()]
param(
    [string] $ComputerName = '10.0.0.90',
    [Parameter(Mandatory = $true)][string] $TypeName,
    [Parameter(Mandatory = $true)][string] $MethodName,
    [string] $PrimaryAssembly = 'GoldClub.Aurum.Engine.dll',
    [string] $SourceDirUnc,
    [string] $PsExecPath = 'C:\Tools\PSTools\PsExec.exe'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$here = $PSScriptRoot
$sourceCs = Join-Path $here 'IlDump.cs'
if (-not (Test-Path -LiteralPath $sourceCs)) { throw "Missing $sourceCs" }

$remoteDirUnc = "\\$ComputerName\c`$\Windows\Temp\ildump"
$engineUnc = "\\$ComputerName\c`$\Goldclub\services\aurum\bin\lib\GoldClub.Aurum.Engine.dll"
$outUnc = Join-Path $remoteDirUnc 'ildump.out'
$errUnc = Join-Path $remoteDirUnc 'ildump.err'

if (-not (Test-Path -LiteralPath $remoteDirUnc)) { New-Item -ItemType Directory -Force -Path $remoteDirUnc | Out-Null }
Copy-Item -LiteralPath $sourceCs -Destination (Join-Path $remoteDirUnc 'IlDump.cs') -Force
Copy-Item -LiteralPath $engineUnc -Destination (Join-Path $remoteDirUnc 'GoldClub.Aurum.Engine.dll') -Force
if ($SourceDirUnc) {
    Get-ChildItem -LiteralPath $SourceDirUnc -File | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $remoteDirUnc $_.Name) -Force
    }
}

$remote = @"
`$ErrorActionPreference = 'Stop'
`$dir = 'C:\Windows\Temp\ildump'
`$csc = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& `$csc /platform:x64 /nologo /out:`"`$dir\IlDump.exe`" /r:`"`$dir\GoldClub.Aurum.Engine.dll`" "`$dir\IlDump.cs" 2>&1 | Out-File "`$dir\build.log" -Encoding UTF8
if (`$LASTEXITCODE -ne 0) { exit 90 }
Remove-Item "`$dir\ildump.out","`$dir\ildump.err" -Force -ErrorAction SilentlyContinue
`$p = Start-Process -FilePath "`$dir\IlDump.exe" -ArgumentList @('$TypeName','$MethodName','$PrimaryAssembly') -WorkingDirectory `$dir -RedirectStandardOutput "`$dir\ildump.out" -RedirectStandardError "`$dir\ildump.err" -PassThru -Wait -WindowStyle Hidden
exit `$p.ExitCode
"@
$enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($remote))
& $PsExecPath "\\$ComputerName" -accepteula -s -n 60 powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand $enc | Out-Null
$code = $LASTEXITCODE

if ($code -eq 90) {
    Write-Host '--- build.log ---' -ForegroundColor Yellow
    $bl = Join-Path $remoteDirUnc 'build.log'
    if (Test-Path -LiteralPath $bl) { Get-Content -LiteralPath $bl }
    throw 'Remote compile failed.'
}

if (Test-Path -LiteralPath $outUnc) { Get-Content -LiteralPath $outUnc }
if (Test-Path -LiteralPath $errUnc) {
    $err = Get-Content -LiteralPath $errUnc -Raw
    if ($err) { Write-Host '--- stderr ---' -ForegroundColor Yellow; Write-Host $err }
}
