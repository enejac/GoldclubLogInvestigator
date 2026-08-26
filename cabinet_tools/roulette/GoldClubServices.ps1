# Shared GoldClub Windows service names (Kill-All stop / Run-FullStack start).
# Dot-source from sibling scripts: . "$PSScriptRoot\GoldClubServices.ps1"
# File MUST be UTF-8 (no BOM). UTF-16 breaks PowerShell parse (spaced tokens).
$script:GoldClubServiceNames = @(
    'GoldClub.Logging.LogDaemon',
    'GoldClub Hardware Subsystem',
    'GoldClub.Aurum.Services',
    'GoldClub Serial Communication Gateway',
    'GoldClub Serial Communication Gateway SAS',
    'GoldClub.BiOS.UserManagement.WebService',
    'GoldClub LocalizationWebService',
    'GoldClub RouletteHistoryWebApi',
    'GoldClub.NTP',
    'GoldClubWindowsTouchMapping'
)

function Resolve-GoldClubService {
    param([Parameter(Mandatory = $true)][string] $NameOrDisplay)
    $svc = Get-Service -Name $NameOrDisplay -ErrorAction SilentlyContinue
    if ($svc) { return $svc }
    $svc = Get-Service -DisplayName $NameOrDisplay -ErrorAction SilentlyContinue
    if ($svc) { return $svc }
    return @(Get-Service -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -eq $NameOrDisplay -or $_.DisplayName -eq $NameOrDisplay
    } | Select-Object -First 1)
}

function Get-AllGoldClubServices {
    @(Get-Service -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -like 'GoldClub*' -or $_.DisplayName -like 'GoldClub*'
    })
}