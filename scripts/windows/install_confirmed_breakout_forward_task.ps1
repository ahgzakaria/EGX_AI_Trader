<#
.SYNOPSIS
    Install a scheduled task that records the daily CONFIRMED_VOLUME_BREAKOUT
    forward-testing session.

.DESCRIPTION
    Targets scripts/record_confirmed_breakout_forward.py, which scans the
    completed session, writes down any signals before the outcome is known, and
    resolves signals recorded weeks earlier whose holding window has now
    finished. Both halves are idempotent, so a task that fires twice, or catches
    up after a missed day, changes nothing it should not.

    This creates no collector, no dashboard and no trading of any kind. It reads
    daily history through the research router and appends to
    data/confirmed_breakout_forward.db.

    WHY THIS EXISTS. Every number this strategy has is in-sample: it was built
    on a decade of history over symbols that still exist, and its thresholds
    were swept with both eras visible. The only evidence that can never be that
    is a signal written down before anyone knows the answer -- and that exists
    only if something writes it down EVERY session, including the many that
    produce nothing. A record assembled when somebody remembers is a record of
    the days somebody remembered.

    Fires Sunday-Thursday, the EGX trading week, TWICE: 15:30 and 17:30 Cairo.

    WHY 15:30 AND NOT 14:40. The 14:30 figure everyone quotes is when trading
    closes, not when the session is complete. In this project's own calendar
    (core/egx_session.py) continuous trading ends 14:15, the closing auction
    ends 14:25, and `egx_settlement_grace_minutes` is 60 -- so a session only
    becomes authoritatively complete at 15:25. Checked rather than assumed:
    `authoritative_completed_session` asked at 14:40 still names the PREVIOUS
    session.

    That matters because the recorder refuses any session the exchange has not
    completed and writes nothing. A run at 14:40 would therefore record
    NOTHING, every day, silently -- and the count of sessions scanned is half
    the evidence this whole exercise produces. 15:30 is five minutes of slack
    past the grace, not a guess.

    WHY A SECOND RUN AT 17:30. One failure mode loses a session permanently:
    the provider has not published the completed bar by 15:30, so the scan sees
    an older session, finds it already recorded, and today never enters the
    record. `StartWhenAvailable` does not help -- the task ran, it just found
    nothing new. Both halves are idempotent and a run with nothing to do exits
    in about a second, so a catch-up costs nothing and covers the only gap that
    cannot be repaired later.

    Nothing is registered unless the Python executable and the script exist.

.EXAMPLE
    .\install_confirmed_breakout_forward_task.ps1 -ProjectRoot "F:\EGX_AI_Trader" -WhatIfOnly

.EXAMPLE
    .\install_confirmed_breakout_forward_task.ps1 -ProjectRoot "F:\EGX_AI_Trader"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [string]$PythonExe,
    [string]$TaskName = "EGX Confirmed Breakout Forward Test",
    # 15:25 is when a session becomes authoritatively complete (auction ends
    # 14:25 + 60 minutes of settlement grace). Five minutes past that.
    [string]$StartTime = "15:30",
    # Catch-up, for the day the provider publishes late. Idempotent, so a run
    # with nothing to do costs a second.
    [string]$CatchUpTime = "17:30",
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
$script = Join-Path $root "scripts\record_confirmed_breakout_forward.py"

# Checked before registering rather than discovered at 15:30 on a Sunday. A
# task that fails silently every evening is worse than no task, because a
# missing session looks exactly like a market with no signals -- which is the
# normal outcome for this strategy.
foreach ($pair in @(
    @{ Path = $PythonExe; What = "Python executable" },
    @{ Path = $script;    What = "recorder script" }
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
$command = ('& "{0}" "{1}" 2>&1 | ' +
            'Out-File -FilePath "{2}" -Append -Encoding utf8') -f
           $PythonExe, $script,
           (Join-Path $logDir "confirmed_breakout_forward.log")
$encoded = [Convert]::ToBase64String(
    [System.Text.Encoding]::Unicode.GetBytes($command))

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -EncodedCommand $encoded" `
    -WorkingDirectory $root

$days = @("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday")
$triggers = @(
    New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $StartTime
    New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $CatchUpTime
)

$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive -RunLevel Limited

# Twenty minutes: the scan itself is about two, and the resolve pass reads
# history for every unresolved signal. StartWhenAvailable catches up a session
# missed because the machine was off, which matters because the denominator --
# how many sessions were scanned -- is part of the evidence.
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 10)

Write-Host "Task        : $TaskName"
Write-Host "Runs        : Sunday-Thursday at $StartTime, again at $CatchUpTime"
Write-Host "              (session completes 15:25: auction 14:25 + 60m grace)"
Write-Host "Command     : $PythonExe $script"
Write-Host "Working dir : $root"
Write-Host "Log         : $(Join-Path $logDir 'confirmed_breakout_forward.log') (appended)"
Write-Host "Status      : data\automation_status\confirmed_breakout_forward_<date>.json"
Write-Host "Writes      : data\confirmed_breakout_forward.db only. No trading."

if ($WhatIfOnly) {
    Write-Host ""
    Write-Host "WhatIfOnly: nothing was registered." -ForegroundColor Yellow
    return
}

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
    -Principal $principal -Settings $settings -Force `
    -Description ("Records CONFIRMED_VOLUME_BREAKOUT signals before their " +
                  "outcome is known and resolves matured ones. Read-only over " +
                  "market data; appends to a research store. Places no orders.") | Out-Null

Write-Host ""
Write-Host "Registered '$TaskName'." -ForegroundColor Green
Write-Host "  Needs a logged-in interactive session; no password is stored."
Write-Host "  Idempotent: the second fire is a no-op when the first one worked,"
Write-Host "  and an unfinished session is refused rather than recorded."
Write-Host "  Remove with: .\remove_confirmed_breakout_forward_task.ps1"
