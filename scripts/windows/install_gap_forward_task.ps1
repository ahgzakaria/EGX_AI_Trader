<#
.SYNOPSIS
    Install a scheduled task that records the daily overnight-gap prediction.

.DESCRIPTION
    Targets scripts/research/record_gap_forward.py "daily", which records the
    finished session's prediction and grades the previous one against today's
    open. Both halves are idempotent, so a task that fires twice, or catches up
    after a missed day, changes nothing it should not.

    This creates no collector, no dashboard and no trading of any kind. It reads
    data/rubix_live_market.db and appends to data/research/gap_forward.db.

    WHY THIS EXISTS. The gap edge measured at t = +11.93 rests on sixteen
    sessions in one month, and it cannot be verified against history because the
    daily `open` field is fabricated in every source this project has. It can
    only be settled forward, which needs an unbroken record that nobody edits
    after the outcome is known. A missed evening is a missing observation, and a
    human remembering to run a script is not a record.

    Fires Sunday-Thursday, the EGX trading week, ten minutes after the 14:30
    Cairo close. The recorder refuses a session that looks incomplete -- fewer
    than 150 symbols, or no bar at or after 11:25 UTC -- and writes nothing, so
    an early or degraded run leaves the record clean for a later retry rather
    than poisoning it.

    Nothing is registered unless the Python executable, the script and the
    market database all exist.

.EXAMPLE
    .\install_gap_forward_task.ps1 -ProjectRoot "F:\EGX_AI_Trader" -WhatIfOnly

.EXAMPLE
    .\install_gap_forward_task.ps1 -ProjectRoot "F:\EGX_AI_Trader"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [string]$PythonExe,
    [string]$TaskName = "EGX Gap Forward Recorder",
    # 14:40 Cairo, ten minutes after the close. Measured across seven sessions,
    # the closing data lands within 0.1 minutes of 14:30 -- but collection does
    # fail, and 2026-08-20 arrived eight and a half hours late. Safety comes from
    # the recorder refusing an incomplete session, not from a generous clock:
    # a prediction is written once and never rewritten, so an early run against
    # half-collected data would be wrong permanently.
    [string]$StartTime = "14:40",
    [switch]$WhatIfOnly
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
    throw "ProjectRoot not found: $ProjectRoot"
}
$root = (Resolve-Path -LiteralPath $ProjectRoot).Path

if (-not $PythonExe) {
    $PythonExe = Join-Path $root "venv\Scripts\python.exe"
}
$script = Join-Path $root "scripts\research\record_gap_forward.py"
$marketDb = Join-Path $root "data\rubix_live_market.db"

# Checked before registering rather than discovered at 14:40 on a Sunday. A
# task that fails silently every evening is worse than no task, because the
# gap in the record looks like a market with no signals.
foreach ($pair in @(
    @{ Path = $PythonExe; What = "Python executable" },
    @{ Path = $script;    What = "recorder script" },
    @{ Path = $marketDb;  What = "market database" }
)) {
    if (-not (Test-Path -LiteralPath $pair.Path -PathType Leaf)) {
        throw "$($pair.What) not found: $($pair.Path)"
    }
}

$logDir = Join-Path $root "logs"
if (-not (Test-Path -LiteralPath $logDir -PathType Container)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

# Appended, never truncated, so a run that fails leaves evidence beside the
# runs that worked -- and written as UTF-8, because PowerShell's default
# redirection produces UTF-16 that ordinary tools render as spaced-out
# gibberish. A log nobody can read is not a log.
$command = ('& "{0}" "{1}" daily 2>&1 | ' +
            'Out-File -FilePath "{2}" -Append -Encoding utf8') -f
           $PythonExe, $script, (Join-Path $logDir "gap_forward.log")
$encoded = [Convert]::ToBase64String(
    [System.Text.Encoding]::Unicode.GetBytes($command))

# -WindowStyle Hidden: every fire of this task opened a console window on the
# desktop, because a scheduled powershell.exe under an Interactive logon gets a
# visible console. Across the whole task set that was about twenty-three windows
# a day, several of them stealing focus mid-session. Hidden reduces it to a brief
# flash; only a non-interactive principal removes it, and setting one needs
# elevation.
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-WindowStyle Hidden -NoProfile -NonInteractive -EncodedCommand $encoded" `
    -WorkingDirectory $root

$trigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Sunday, Monday, Tuesday, Wednesday, Thursday -At $StartTime

$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 10)

Write-Host "Task        : $TaskName"
Write-Host "Runs        : Sunday-Thursday at $StartTime (EGX trading week)"
Write-Host "Command     : $PythonExe $script daily"
Write-Host "Working dir : $root"
Write-Host "Log         : $(Join-Path $logDir 'gap_forward.log') (appended)"
Write-Host "Writes      : data\research\gap_forward.db only. No trading."

if ($WhatIfOnly) {
    Write-Host ""
    Write-Host "WhatIfOnly: nothing was registered." -ForegroundColor Yellow
    return
}

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force `
    -Description ("Records the daily EGX overnight-gap prediction and grades " +
                  "the previous session. Read-only over market data; appends " +
                  "to a research store. Places no orders.") | Out-Null

Write-Host ""
Write-Host "Registered '$TaskName'." -ForegroundColor Green
Write-Host "  Needs a logged-in interactive session; no password is stored."
Write-Host "  Both halves are idempotent, so a double fire or a catch-up run is safe."
Write-Host "  Remove with: Unregister-ScheduledTask -TaskName '$TaskName'"
