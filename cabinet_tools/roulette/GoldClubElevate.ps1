<#
.SYNOPSIS
    Elevate a GoldClub USB script when UAC RunAs is dead (seclogon/Appinfo disabled).
#>
function Get-GoldClubElevateStateDir {
    $candidates = @(
        'D:\usb_scripts\roulette\_elevate',
        'D:\usb_scripts\_elevate'
    )
    foreach ($dir in $candidates) {
        try {
            if (-not (Test-Path -LiteralPath $dir)) {
                New-Item -ItemType Directory -Path $dir -Force | Out-Null
            }
            $probe = Join-Path $dir '.write_probe'
            [System.IO.File]::WriteAllText($probe, 'x')
            Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
            return $dir
        } catch {}
    }
    return $env:PUBLIC
}

function Test-GoldClubRunAsAvailable {
    foreach ($name in @('Appinfo', 'seclogon')) {
        try {
            $svc = Get-Service -Name $name -ErrorAction Stop
        } catch {
            return $false
        }
        if ($svc.StartType -eq 'Disabled') {
            return $false
        }
        if ($svc.Status -ne 'Running') {
            return $false
        }
    }
    return $true
}

function Invoke-GoldClubSelfElevate {
    param(
        [Parameter(Mandatory = $true)][string] $ScriptPath,
        [string[]] $ArgumentList = @()
    )
    $psArgs = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', $ScriptPath
    ) + @($ArgumentList)

    if (Test-GoldClubRunAsAvailable) {
        Write-Host 'Not elevated - trying UAC RunAs...' -ForegroundColor Yellow
        try {
            $p = Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $psArgs -PassThru -Wait -ErrorAction Stop
            return $(if ($null -ne $p) { [int]$p.ExitCode } else { 1 })
        } catch {
            Write-Host (
                "RunAs failed ({0}) - using SYSTEM scheduled task." -f
                $_.Exception.Message
            ) -ForegroundColor Yellow
        }
    } else {
        Write-Host 'Skipping UAC RunAs: Secondary Logon / Appinfo is disabled on this cabinet.' -ForegroundColor Yellow
        Write-Host 'Elevating via SYSTEM scheduled task...' -ForegroundColor Yellow
    }

    $stateDir = Get-GoldClubElevateStateDir
    $done = Join-Path $stateDir 'GoldClubElevate.exit'
    $runner = Join-Path $stateDir 'GoldClubElevate-run.cmd'
    try { Remove-Item -LiteralPath $done -Force -ErrorAction SilentlyContinue } catch {}
    $argLine = (
        $ArgumentList | ForEach-Object {
            if ($_ -match '[\s"]') {
                ' "{0}"' -f (($_ -replace '"', '""'))
            } else {
                ' {0}' -f $_
            }
        }
    ) -join ''
    $lines = @(
        '@echo off'
        "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`"$argLine"
        "echo %ERRORLEVEL%>`"$done`""
    )
    [System.IO.File]::WriteAllText($runner, ($lines -join "`r`n") + "`r`n")

    $taskName = 'GoldClub-ElevateOnce'
    $runOut = cmd.exe /c "schtasks /Run /TN `"$taskName`""
    if ($LASTEXITCODE -ne 0) {
        Write-Host (
            "Existing {0} /Run failed ({1}) - trying to create it." -f $taskName, $runOut
        ) -ForegroundColor Yellow
        $createOut = cmd.exe /c "schtasks /Create /TN `"$taskName`" /SC ONCE /ST 00:00 /SD 01/01/2099 /RL HIGHEST /RU SYSTEM /F /TR `"$runner`""
        if ($LASTEXITCODE -ne 0) {
            Write-Host ("ERROR: cannot create SYSTEM elevate task: {0}" -f $createOut) -ForegroundColor Red
            Write-Host 'Open GoldClub Admin Shell (already elevated) and run the script again.' -ForegroundColor Yellow
            Write-Host 'Or run D:\RESTART-STACK-NOW.cmd from that elevated console.' -ForegroundColor Yellow
            return 1
        }
        cmd.exe /c "schtasks /Run /TN `"$taskName`"" | Out-Null
    }
    $deadline = (Get-Date).AddSeconds(180)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path -LiteralPath $done) {
            $raw = (Get-Content -LiteralPath $done -ErrorAction SilentlyContinue | Select-Object -First 1)
            $code = 1
            [void][int]::TryParse(("$raw").Trim(), [ref]$code)
            try { Remove-Item -LiteralPath $done -Force -ErrorAction SilentlyContinue } catch {}
            return $code
        }
        Start-Sleep -Milliseconds 400
    }
    Write-Host 'ERROR: SYSTEM elevate task timed out (180s).' -ForegroundColor Red
    return 1
}
