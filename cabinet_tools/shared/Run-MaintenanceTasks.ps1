# Run any USB maintenance task folder through GoldClub RunManteinanceTasks (same stack as onlogon D__ scripts).
param(
    [Parameter(Mandatory = $true)]
    [string] $TaskFolder,
    [switch] $SkipSetup
)

$ErrorActionPreference = 'Stop'
$Setup = 'C:\goldclub\bin\Setup.exe'
$Runner = 'C:\goldclub\bin\RunManteinanceTasks.1.ps1'

if (-not $SkipSetup) {
    if (-not (Test-Path -LiteralPath $Setup)) { throw "Setup.exe not found: $Setup" }
    Push-Location (Split-Path $Setup -Parent)
    try {
        & $Setup --setup application.ruleta.setup --user superadmin --mangler type0 --keyword 'keyword here'
        if ($LASTEXITCODE -ne 0) { throw "Setup.exe exit $LASTEXITCODE" }
    } finally { Pop-Location }
}

if (-not (Test-Path -LiteralPath $Runner)) { throw "RunManteinanceTasks not found: $Runner" }
if (-not (Test-Path -LiteralPath $TaskFolder)) { throw "Task folder not found: $TaskFolder" }

& powershell -NoProfile -ExecutionPolicy Bypass -File $Runner -path $TaskFolder
exit $LASTEXITCODE