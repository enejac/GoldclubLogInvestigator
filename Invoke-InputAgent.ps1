<#
.SYNOPSIS
    Compile and run the cabinet InputAgent to send key scripts.

.DESCRIPTION
    The InputAgent is a small C# console app that uses Win32 SendInput to send
    keystrokes (F11 debug, bet-step changes, spin) in the *interactive* session.

    This script compiles InputAgent.cs to a local exe (cached by content hash)
    and runs it *on this machine*. Remote orchestration (PsExec -i) is handled
    by the Python automation runner.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ScriptPath,
    [string]$FocusProcess = "OneHand"
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$src = Join-Path $PSScriptRoot "cabinet_tools\\InputAgent\\InputAgent.cs"
if (-not (Test-Path $src)) { throw "Missing $src" }

$hash = (Get-FileHash -Algorithm SHA256 $src).Hash.Substring(0, 12).ToLowerInvariant()
$outDir = Join-Path $env:TEMP ("investigator-inputagent-" + $hash)
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$exe = Join-Path $outDir "InputAgent.exe"

if (-not (Test-Path $exe)) {
    $csc = Join-Path $env:WINDIR "Microsoft.NET\\Framework64\\v4.0.30319\\csc.exe"
    if (-not (Test-Path $csc)) {
        throw "csc.exe not found at $csc"
    }
    & $csc /nologo /optimize+ /target:exe /out:$exe $src | Out-Null
}

& $exe --scriptPath $ScriptPath --focusProcess $FocusProcess
