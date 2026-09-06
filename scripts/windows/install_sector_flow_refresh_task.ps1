<#
.SYNOPSIS
    Install a scheduled task that keeps the sector liquidity history current.

.DESCRIPTION
    Targets scripts/refresh_sector_flow.py, which rebuilds the sector history
    when -- and only when -- an EGX session has completed since the last build.

    WHY THIS EXISTS. The refresh script was written for Task Scheduler and says
    so in its own docstring: "Run daily after the EGX close (Sun-Thu) via Task
    Scheduler." It was never registered. Its opening line describes the very
    failure that then happened to it -- "build_sector_flow.py is a manual,
    ~20-minute full rebuild, and nothing ran it. A feature that needs a manual
    rebuild to stay current goes stale silently."

    On 2026-08-30 the store's newest complete session was 2026-08-26 and its
    last build was three days old. The Sector Liquidity page had been warning
    about it correctly the whole time; nothing was listening.

    WHY POLLING AND NOT A FIXED TIME. The completed daily bar does not appear at
    a known hour. It appears when the EGX Rubix Daily Finalizer builds it --
    15:45 Cairo -- and that job's duration varies with the universe and can fail
    outright. Chaining this task to a guessed minute after it would make the
    sector history depend on that guess holding. The same mistake was made twice
    on the forward-testing task before it was replaced with polling.

    So this asks hourly from 16:00, after the finalizer has normally finished,
    and takes the session whenever it turns up.

    WHY THAT IS AFFORDABLE. Skipping, not splitting, is what makes a daily run
    cheap here, and the script already does it. When no session has completed
    since the last build it logs UP_TO_DATE and exits in under a second; when a
    session is missing but the provider has not published it yet it exits
    WAITING_ON_PROVIDER, so the twenty-minute rebuild is not paid once an hour
    while EODHD catches up. Only a genuinely new session costs the full pass.

    The rebuild is whole rather than incremental by design: every sector's share
    is a fraction of that session's market total, and RVOL scores a session
    against its own trailing median, so appending sessions measured on a
    different basis would put a discontinuity inside the comparison those
    numbers exist to make.

    Records only. It places no order, produces no signal, and writes to
    data/sector_flow.db and reports/sector_flow_coverage.csv.

    Nothing is registered unless the Python executable and the script exist.

.EXAMPLE
    .\install_sector_flow_refresh_task.ps1 -ProjectRoot "F:\EGX_AI_Trader" -WhatIfOnly

.EXAMPLE
    .\install_sector_flow_refresh_task.ps1 -ProjectRoot "F:\EGX_AI_Trader"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [string]$PythonExe,
    [string]$TaskName = "EGX Sector Flow Refresh",
    # 16:00 rather than 15:30: the candle this reads is built by the EGX Rubix
    # Daily Finalizer at 15:45, and a poll before that is guaranteed to find
    # nothing. It is a starting point, not a prediction -- the repetition below
    # is what actually finds the session.
    [string]$StartTime = "16:00",
    [int]$PollHours = 6,
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
$script = Join-Path $root "scripts\refresh_sector_flow.py"

# Checked before registering rather than discovered at 16:00 on a Sunday. A
# task that fails silently is the exact failure mode this task exists to end.
foreach ($pair in @(
    @{ Path = $PythonExe; What = "Python executable" },
    @{ Path = $script;    What = "refresh script" }
)) {
    if (-not (Test-Path -LiteralPath $pair.Path -PathType Leaf)) {
        throw "$($pair.What) not found: $($pair.Path)"
    }
}

$logDir = Join-Path $root "logs"
if (-not (Test-Path -LiteralPath $logDir -PathType Container)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

# Appended, never truncated, and written as UTF-8: PowerShell's default
# redirection produces UTF-16 that ordinary tools render as spaced-out
# gibberish. A log nobody can read is not a log.
#
# PYTHONIOENCODING is the other half of the same problem. A Python process
# launched by Task Scheduler gets no console, so its stdout falls back to the
# ANSI code page and any non-ASCII character it prints is mangled on the way
# out -- an em dash in a log line arrived as a stray letter. Pinning the
# interpreter's output encoding fixes it at the source, for every message,
# including ones that come from an exception nobody wrote by hand.
$command = ('$env:PYTHONIOENCODING = "utf-8"; & "{0}" "{1}" 2>&1 | ' +
            'Out-File -FilePath "{2}" -Append -Encoding utf8') -f
           $PythonExe, $script,
           (Join-Path $logDir "sector_flow_refresh_task.log")
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
# "every hour, but only on trading days" out of a single trigger.
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

# Ninety minutes. Forty was the first guess, from a rebuild that took nine
# minutes on an idle machine, and it was wrong the first day it ran: on
# 2026-08-31 the 16:00 rebuild was still going at 16:40 because a full test
# suite was running beside it, so the task was killed -- and the Python child
# survived and finished the build at 16:48 anyway. The store advanced, the task
# reported SCHED_S_TASK_TERMINATED, and from outside that is indistinguishable
# from a build that was actually lost.
#
# The limit exists to stop a hung run, not to decide the result of a slow one,
# so it is set well past any duration this has plausibly needed.
#
# IgnoreNew is what makes hourly polling safe -- a rebuild in progress must
# never be overlapped by the next poll, which would have two processes writing
# one store. StartWhenAvailable catches up a session missed because the machine
# was off.
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 90) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 15)

Write-Host "Task        : $TaskName"
Write-Host "Runs        : Sunday-Thursday, hourly from $StartTime for $PollHours hours"
Write-Host "              (starts after the 15:45 daily finalizer builds the candle)"
Write-Host "Command     : $PythonExe $script"
Write-Host "Working dir : $root"
Write-Host "Log         : $(Join-Path $logDir 'sector_flow_refresh_task.log') (appended)"
Write-Host "              plus the script's own logs\sector_flow_refresh.log"
Write-Host "Writes      : data\sector_flow.db and reports\sector_flow_coverage.csv."
Write-Host "              No orders, no signals."

if ($WhatIfOnly) {
    Write-Host ""
    Write-Host "WhatIfOnly: nothing was registered." -ForegroundColor Yellow
    return
}

$principalNote = Register-TaskPreferringS4U `
    -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Identity $identity `
    -Description ("Rebuilds the sector liquidity history when an EGX session " +
                  "has completed since the last build. Read-only over market " +
                  "data; writes a research store. Places no orders.")
Write-Host "  Principal        : $principalNote"

Write-Host ""
Write-Host "Registered '$TaskName'." -ForegroundColor Green
Write-Host "  Needs a logged-in interactive session; no password is stored."
Write-Host "  Idempotent: a poll with nothing to do exits UP_TO_DATE in under a"
Write-Host "  second, and a session the provider has not published yet is left"
Write-Host "  for the next poll rather than rebuilt against."
Write-Host "  Remove with: .\remove_sector_flow_refresh_task.ps1"
