# Stop the Ruleta/Godot crash loop: Kill-All (unlocks SAS DeviceManagerData),
# set active combo to paytable_double_zero (10.1 knows that name), relaunch.
# Does not touch licences.
$ErrorActionPreference = "Continue"
$log = "D:\ConfigScanner\fix-error30-loop.log"
function L([string]$m) {
    $line = "[{0}] {1}" -f (Get-Date -Format o), $m
    Write-Host $line
    try { [IO.File]::AppendAllText($log, $line + [Environment]::NewLine, [Text.UTF8Encoding]::new($false)) } catch {}
}
L ("start whoami={0}" -f (whoami))
$kill = "D:\ConfigScanner\scripts\roulette\Kill-All.ps1"
$run = "D:\ConfigScanner\scripts\roulette\Run-FullStack.ps1"
if (Test-Path $kill) {
    L "Kill-All"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $kill -AlreadyElevated
    L ("Kill-All exit={0}" -f $LASTEXITCODE)
} else { L "missing Kill-All" }
Start-Sleep -Seconds 2
L "Kill-All done - restoring signed DeviceManagerData (do not patch MAC headers)"
$bak = "D:\ConfigScanner\backup-devicemgr-20260818"
$pairs = @(
    @("gm2au_DeviceManagerData.xml_1", "C:\goldclub\ruleta\var\gm2au\DeviceManagerData.xml_1"),
    @("gm2au_DeviceManagerData.xml_2", "C:\goldclub\ruleta\var\gm2au\DeviceManagerData.xml_2"),
    @("SASControler1_DeviceManagerData.xml_1", "C:\goldclub\ruleta\var\SASControler1\DeviceManagerData.xml_1"),
    @("SASControler1_DeviceManagerData.xml_2", "C:\goldclub\ruleta\var\SASControler1\DeviceManagerData.xml_2")
)
foreach ($pair in $pairs) {
    $src = Join-Path $bak $pair[0]
    $dst = $pair[1]
    if (-not (Test-Path -LiteralPath $src)) { L ("missing backup {0}" -f $src); continue }
    attrib.exe -R $dst 2>$null | Out-Null
    Copy-Item -LiteralPath $src -Destination $dst -Force
    L ("restored {0}" -f $dst)
}
if (Test-Path $run) {
    L "Run-FullStack"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $run -AlreadyElevated
    L ("Run-FullStack exit={0}" -f $LASTEXITCODE)
}
L "done"