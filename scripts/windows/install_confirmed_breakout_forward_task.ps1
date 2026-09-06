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

    Polls HOURLY, Sunday-Thursday, from 15:00 to 22:00 Cairo.

    WHY POLLING AND NOT A TIME. Two fixed times were tried and both were wrong:
    16:30 was arbitrary padding, and 15:30 was derived from
    `egx_settlement_grace_minutes`, whose own comment in core/egx_session.py
    calls it "a data-availability allowance", i.e. somebody else's padding. On
    2026-08-30 the 15:30 run fired into a router whose newest bar was still
    2026-08-26, four days old.

    The reason first given here for replacing them was wrong too, and is
    corrected rather than quietly deleted: this said EODHD publishes on no
    stated schedule and that the Rubix daily bridge is `enabled: false`. Both
    name the wrong systems. `rubix_daily_bridge` is the SUPERSEDED bridge,
    disabled on 2026-07-20 by whoever built it after measuring that Rubix
    covered <=100 of 270 session minutes; EODHD is not idle either, and serves
    the settled body of every covered symbol.

    What actually builds the candle is core/daily_bridge/, run by the EGX Rubix
    Daily Finalizer task at 15:45 Cairo. On 2026-08-30 the router's newest bar
    was 2026-08-26 at 15:32 and 2026-08-30 at 16:10 -- the 15:30 run missed it
    by thirteen minutes, racing a LOCAL job rather than waiting on a provider.

    So there is no publication time to derive a schedule from, because there is
    no publisher to wait for. The finalizer's duration varies and it can fail,
    and chaining one task to another's completion would make the record depend
    on that chain holding. Polling does not care: it asks once an hour until
    the answer changes.

    WHY THAT IS AFFORDABLE. The recorder has a fast path: a cheap probe of a
    few symbols answers "is there a session newer than the ones already
    recorded". When the answer is no -- which is most polls -- the run exits in
    about eight seconds without scanning 214 symbols, which is the hundred-
    second part. Only the first poll that sees a new session does real work.

    The window ends at 22:00 rather than running all night because a bar that
    has not appeared seven hours after the close is a collection problem, and
    the missing status file is the signal to go and look at it.

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
    # 15:00 is after the closing auction (14:25) by enough that a poll is not
    # wasted, and it is a starting point rather than a prediction: the
    # repetition below is what actually finds the bar.
    [string]$StartTime = "15:00",
    [int]$PollHours = 7,
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

# Checked before registering rather than discovered at 15:00 on a Sunday. A
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
#
# PYTHONIOENCODING is the other half of the same problem. A Python process
# launched by Task Scheduler gets no console, so its stdout falls back to the
# ANSI code page and any non-ASCII character it prints is mangled on the way
# out. Pinning the interpreter's output encoding fixes it at the source.
$command = ('$env:PYTHONIOENCODING = "utf-8"; & "{0}" "{1}" 2>&1 | ' +
            'Out-File -FilePath "{2}" -Append -Encoding utf8') -f
           $PythonExe, $script,
           (Join-Path $logDir "confirmed_breakout_forward.log")
$encoded = [Convert]::ToBase64String(
    [System.Text.Encoding]::Unicode.GetBytes($command))

# -WindowStyle Hidden: every fire of this task opened a console window on the
# desktop, because a scheduled powershell.exe under an Interactive logon gets a
# visible console. Across the whole task set that was about twenty-three windows
# a day, several of them stealing focus mid-session. Hidden reduces it to a brief
# flash; only a non-interactive principal removes it, and setting one needs
# elevation.
# Register with S4U, and fall back to Interactive if that is refused.
#
# Changing a principal terminates a running instance. On 2026-09-06 the elevated
# one-liner that moved six tasks to S4U was run while the sector refresh was
# fifty minutes into a full rebuild; Task Scheduler logged the update at 16:30:55
# and the kill at 16:32:53, return code 3221225786. The run left no OK and no
# FAILED, because nothing failed -- it was shot. Check State before changing a
# principal, or do it outside the schedule.
#
# The fallback has to wrap *this* call, not New-ScheduledTaskPrincipal: building
# an S4U principal object always succeeds, and only the registration is denied
# without elevation. My first attempt caught the wrong one, so a normal run got
# past the try and failed here.
#
# -Force replaces in one step. This script used to Unregister first and then
# register, which means any failure in between left the task deleted and
# nothing installed -- exactly what happened when the S4U registration was
# denied, twice.
function Register-TaskPreferringS4U {
    param($TaskName, $Action, $Trigger, $Settings, $Identity, $Description)

    $s4u = New-ScheduledTaskPrincipal -UserId $Identity -LogonType S4U -RunLevel Limited
    try {
        # -ErrorAction Stop, or the catch never fires: Register-ScheduledTask
        # reports "Access is denied" as a non-terminating error, so without this
        # the fallback is skipped and the S4U path is reported as a success it
        # did not achieve.
        Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
            -Principal $s4u -Settings $Settings -Description $Description -Force `
            -ErrorAction Stop | Out-Null
        return "S4U (no console window)"
    } catch {
        $interactive = New-ScheduledTaskPrincipal -UserId $Identity `
            -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
            -Principal $interactive -Settings $Settings -Description $Description -Force | Out-Null
        return "Interactive -- a console flashes on every fire; S4U needs an elevated shell"
    }
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-WindowStyle Hidden -NoProfile -NonInteractive -EncodedCommand $encoded" `
    -WorkingDirectory $root

$days = @("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday")
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $StartTime
# A weekly trigger has no repetition of its own, so one is borrowed from a
# throwaway -Once trigger. This is the documented idiom and the only way to get
# "every hour, but only on trading days" out of one trigger.
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At $StartTime `
    -RepetitionInterval (New-TimeSpan -Hours 1) `
    -RepetitionDuration (New-TimeSpan -Hours $PollHours)).Repetition
$triggers = @($trigger)

# S4U, so this runs without a desktop and opens no console window. Interactive
# is the fallback, not the intent: every fire under it put a black window on the
# screen, and across the whole task set that was roughly twenty-three a day.
#
# S4U needs elevation to set, so a normal run cannot have it and must say so
# rather than quietly registering the noisy version. -WindowStyle Hidden on the
# action keeps that fallback down to a flash.
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

# Twenty minutes: the scan itself is about two, and the resolve pass reads
# history for every unresolved signal. StartWhenAvailable catches up a session
# missed because the machine was off, which matters because the denominator --
# how many sessions were scanned -- is part of the evidence. IgnoreNew matters
# more now that polls are hourly: a slow scan must never be overlapped by the
# next poll.
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 10)

Write-Host "Task        : $TaskName"
Write-Host "Runs        : Sunday-Thursday, hourly from $StartTime for $PollHours hours"
Write-Host "              (polls because no provider publication time is known)"
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

$principalNote = Register-TaskPreferringS4U `
    -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Identity $identity `
    -Description ("Records CONFIRMED_VOLUME_BREAKOUT signals before their " +
                  "outcome is known and resolves matured ones. Read-only over " +
                  "market data; appends to a research store. Places no orders.")
Write-Host "  Principal        : $principalNote"

Write-Host ""
Write-Host "Registered '$TaskName'." -ForegroundColor Green
Write-Host "  Needs a logged-in interactive session; no password is stored."
Write-Host "  Idempotent: a poll with nothing new exits in ~8s without scanning,"
Write-Host "  and an unfinished session is refused rather than recorded."
Write-Host "  Remove with: .\remove_confirmed_breakout_forward_task.ps1"
