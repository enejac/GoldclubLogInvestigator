# Legacy alias - calls main mounted-drive script.
& (Join-Path $PSScriptRoot 'Fetch-Roulette102Software.ps1') @args
exit $LASTEXITCODE
