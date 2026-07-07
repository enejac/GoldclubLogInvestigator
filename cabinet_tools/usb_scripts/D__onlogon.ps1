Add-Type -AssemblyName 'System.Windows.Forms'

$form1 = New-Object ‘System.Windows.forms.form’
$label1 = New-Object ‘System.Windows.Forms.Label’
$InitialFormWindowState = New-Object ‘System.Windows.Forms.FormWindowState’

$Form_StateCorrection_Load=
{
#Correct the initial state of the form to prevent the .Net maximized form issue
$form1.WindowState = $InitialFormWindowState
}
$Form_Cleanup_FormClosed=
{
#Remove all event handlers from the controls
try
{
$form1.remove_Load($Form_StateCorrection_Load)
$form1.remove_FormClosed($Form_Cleanup_FormClosed)
}
catch [Exception]
{ }
}
$form1.SuspendLayout()

#
# form1
#
$form1.ControlBox = $false
$form1.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
$form1.StartPosition = “CenterScreen”
$form1.Size = New-Object System.Drawing.Size(650,90)
$form1.ShowIcon = $false
$form1.ForeColor = [System.Drawing.Color]::White
$form1.BackColor = [System.Drawing.Color]::Black
#
# label1
#
$label1.Text = “Initializing Machine…”
$label1.AutoSize = $True
$label1.Font = “Segoe UI,40”
#$label1.Location = “42,15”
$form1.Controls.Add($label1)

$form1.ResumeLayout()
#Save the initial state of the form
$InitialFormWindowState = $form1.WindowState
#Init the OnLoad event to correct the initial state of the form
$form1.add_Load($Form_StateCorrection_Load)
#Clean up the control events
$form1.add_FormClosed($Form_Cleanup_FormClosed)
#Show the Form
$form1.BringToFront()
$form1.Show()

$VerbosePreference = 'Continue'
$logDir='C:\goldclub\var\log\'
if ( -not (Test-Path $logDir -PathType Container)) {
    New-Item $logDir -ItemType 'Directory'
}

function Get-GoldClubUsbRoot {
    $marker = 'TeamViewer_LoginBackup\restore_tv_login.cmd'
    foreach ($psd in Get-PSDrive -PSProvider FileSystem) {
        $candidate = Join-Path $psd.Root $marker
        if (Test-Path -LiteralPath $candidate) {
            return $psd.Root.TrimEnd('\')
        }
    }
    $vol = Get-Volume -ErrorAction SilentlyContinue | Where-Object {
        $_.DriveLetter -and $_.FileSystemLabel -eq 'USB'
    } | Select-Object -First 1
    if ($vol) { return "$($vol.DriveLetter):" }
    throw 'GoldClub USB stick not found (TeamViewer_LoginBackup\restore_tv_login.cmd)'
}
$UsbRoot = Get-GoldClubUsbRoot

C:\goldclub\bin\RunManteinanceTasks.1.ps1 -path ($PSScriptRoot+"/"+[System.IO.Path]::GetFileNameWithoutExtension($PSCommandPath)) *>&1 | Tee-Object -FilePath "$logDir\onlogon.log"


$form1.close()
Start-Process C:\goldclub\bootstrap.exe
"[$(Get-Date -Format o)] before restore_tv_login" | Tee-Object -FilePath "$logDir\onlogon.log" -Append | Out-Null
try {
    $tvProc = Start-Process -FilePath "cmd.exe" -ArgumentList '/c', "`"$UsbRoot\TeamViewer_LoginBackup\restore_tv_login.cmd`"" -PassThru
    if ($tvProc) {
        Wait-Process -Id $tvProc.Id -Timeout 45 -ErrorAction Stop
    }
    $tvExitCode = if ($tvProc) { $tvProc.ExitCode } else { "N/A" }
    "[$(Get-Date -Format o)] after restore_tv_login (completed, exit=$tvExitCode)" | Tee-Object -FilePath "$logDir\onlogon.log" -Append | Out-Null
}
catch {
    "[$(Get-Date -Format o)] restore_tv_login timeout/error; continuing startup: $($_.Exception.Message)" | Tee-Object -FilePath "$logDir\onlogon.log" -Append | Out-Null
    try {
        if ($tvProc -and -not $tvProc.HasExited) {
            Stop-Process -Id $tvProc.Id -Force -ErrorAction SilentlyContinue
        }
    }
    catch {}
}

"[$(Get-Date -Format o)] before _share" | Tee-Object -FilePath "$logDir\onlogon.log" -Append | Out-Null
try {
    $shareProc = Start-Process -FilePath "cmd.exe" -ArgumentList '/c', "`"$UsbRoot\_share.bat`"" -PassThru
    if ($shareProc) {
        Wait-Process -Id $shareProc.Id -ErrorAction Stop
    }
    $shareExitCode = if ($shareProc) { $shareProc.ExitCode } else { "N/A" }
    "[$(Get-Date -Format o)] after _share (completed, exit=$shareExitCode)" | Tee-Object -FilePath "$logDir\onlogon.log" -Append | Out-Null
}
catch {
    "[$(Get-Date -Format o)] _share failed: $($_.Exception.Message)" | Tee-Object -FilePath "$logDir\onlogon.log" -Append | Out-Null
}
