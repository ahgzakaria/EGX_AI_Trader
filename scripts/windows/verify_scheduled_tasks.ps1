<#
.SYNOPSIS
    Compare the scheduled tasks this repository declares against the ones
    actually registered on this machine. Reports only; changes nothing.

.DESCRIPTION
    The daily finalizer fired at 14:42 against a 14:45 boundary for weeks. It
    returned 0, its log said "completed ok", and it built nothing -- the candle
    appeared the next afternoon, made by the backfill. Nothing caught it,
    because the boundary lived in Python, the trigger lived in Task Scheduler,
    and no file held both.

    Checking that same set afterwards found two more of the same shape:

      * install_rubix_assisted_start_task.ps1 said 09:10 while the registered
        task said 09:45. 09:10 is the start of a timed human procedure -- the
        runbook gives the export until 09:25 and expects the feed confirmed
        before 09:30 -- so the registered task was leaving fifteen minutes for
        twenty minutes of work;
      * the only ORB installer registered 'ORB_Shadow_Orchestrator' running the
        orchestrator alone, while what actually runs is
        'EGX ORB Full Shadow Automation' running the three-step wrapper -- an
        automation the repository described nowhere.

    Both were found by reading the two sides next to each other, which is all
    this script does, on demand, in a second.

    It answers three questions per task: is it registered, does it invoke what
    the installer says it should, and does it start when the installer says.
    A row it cannot determine is reported as UNKNOWN, never as a pass -- a
    check that quietly succeeds when it failed to look is the thing being
    fixed here.

    Exit code is 0 when every task matches, 1 when anything drifted or could
    not be determined, so it can be a scheduled check of the scheduler.

.EXAMPLE
    .\verify_scheduled_tasks.ps1
#>

[CmdletBinding()]
param(
    [string]$ProjectRoot = "",
    [string]$PythonExe   = ""
)

# $PSScriptRoot is not populated inside a param block under Windows
# PowerShell 5.1, so a default that used it bound an empty string and the script
# died on its own first line with "Cannot bind argument to parameter 'Path'".
# It works under pwsh 7, which is how it passed every time I ran it. Resolved
# here instead, where both hosts agree.
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ProjectRoot) { $ProjectRoot = (Split-Path -Parent (Split-Path -Parent $scriptDir)) }

$ErrorActionPreference = "Stop"
if (-not $PythonExe) { $PythonExe = Join-Path $ProjectRoot "venv\Scripts\python.exe" }

# What this repository claims should exist. `Expect` is the Cairo start time;
# 'derived' means the owning script is asked for it, the way the finalizer's
# installer does, so this table cannot become a second copy of that boundary.
# `Limit` is ExecutionTimeLimit and `Repeat` is Interval/Duration, both as Task
# Scheduler writes them. They are here because the trigger time is not the only
# setting a fix can depend on: the finalizer's PT30M/PT2H repetition is half of
# why its candle now gets built the same day, and losing it would restore the
# one-shot failure while the start time still read 14:50 and looked correct.
# The assisted start's limit went two hours -> one -> two again inside a week,
# which is exactly the kind of edit that lands on the machine and never reaches
# the repository.
$declared = @(
    @{ Task = "EGX Rubix Daily Finalizer"
       Installer = "install_rubix_daily_finalizer_task.ps1"
       Invokes = "run_rubix_daily_finalizer.py"
       Expect = "derived"; Margin = 5
       Limit = "PT2H"; Repeat = "PT30M/PT2H"; Logon = "S4U" }

    @{ Task = "EGX Sector Flow Refresh"
       Installer = "install_sector_flow_refresh_task.ps1"
       Invokes = "refresh_sector_flow.py"
       Expect = "16:00"; Limit = "PT1H30M"; Repeat = "PT1H/PT6H"; Logon = "S4U" }

    @{ Task = "EGX Confirmed Breakout Forward Test"
       Installer = "install_confirmed_breakout_forward_task.ps1"
       Invokes = "record_confirmed_breakout_forward.py"
       Expect = "15:00"; Limit = "PT20M"; Repeat = "PT1H/PT7H"; Logon = "S4U" }

    @{ Task = "EGX Gap Forward Recorder"
       Installer = "install_gap_forward_task.ps1"
       Invokes = "record_gap_forward.py"
       Expect = "14:40"; Limit = "PT20M"; Repeat = "-"; Logon = "S4U" }

    @{ Task = "EGX Rubix Assisted Start"
       Installer = "install_rubix_assisted_start_task.ps1"
       Invokes = "run_rubix_assisted_start.py"
       Expect = "09:10"; Limit = "PT2H"; Repeat = "-"; Logon = "Interactive" }

    @{ Task = "EGX ORB Full Shadow Automation"
       Installer = "install_orb_full_shadow_task.ps1"
       Invokes = "run_daily_orb_automation.ps1"
       Expect = "09:45"; Limit = "PT7H"; Repeat = "-"; Logon = "S4U" }

    # Interactive, and deliberately so: this is the one task whose entire output
    # is a sound and a dialog on the operator's own desktop. Run as S4U it would
    # poll all session and be seen by nobody, which is the failure it exists to
    # prevent. PT5H covers 10:00-14:30 with room for the close.
    @{ Task = "EGX Rubix Supervisor Watchdog"
       Installer = "install_rubix_supervisor_watchdog_task.ps1"
       Invokes = "watch_rubix_supervisor.py"
       Expect = "10:00"; Limit = "PT5H"; Repeat = "-"; Logon = "Interactive" }

    # This script, run daily at 09:00 ahead of the 09:10 assisted start. It is
    # in its own table for two reasons: an undeclared task is reported, so a
    # checker missing from its own list would flag itself every morning until
    # people stopped reading it; and a check that silently stops running looks
    # exactly like a check that keeps passing.
    @{ Task = "EGX Scheduled Tasks Verification"
       Installer = "install_scheduled_tasks_verification_task.ps1"
       Invokes = "verify_scheduled_tasks.ps1"
       Expect = "09:00"; Limit = "PT10M"; Repeat = "-"; Logon = "S4U" }
)

function Get-TaskAction {
    param($Task)
    $a = $Task.Actions | Select-Object -First 1
    $arg = "$($a.Arguments)"
    if ($arg -match '-EncodedCommand\s+(\S+)') {
        try { $arg = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($matches[1])) } catch { }
    }
    return "$($a.Execute) $arg"
}

$rows = @()
$drift = 0

foreach ($d in $declared) {
    $status = @()
    $expect = $d.Expect
    $actual = ""

    # A derived time is asked for, never assumed.
    if ($expect -eq "derived") {
        Push-Location $ProjectRoot
        try {
            $raw = (& $PythonExe "scripts\run_rubix_daily_finalizer.py" --print-ready-time 2>&1) |
                   Select-Object -Last 1
        } catch { $raw = "" } finally { Pop-Location }
        if ("$raw".Trim() -match '^([01]\d|2[0-3]):[0-5]\d$') {
            $expect = ([datetime]::ParseExact("$raw".Trim(), "HH:mm", $null)
                       ).AddMinutes($d.Margin).ToString("HH:mm")
        } else {
            $expect = "UNKNOWN"
            $status += "cannot derive the boundary"
        }
    }

    $installer = Join-Path $ProjectRoot (Join-Path "scripts\windows" $d.Installer)
    if (-not (Test-Path -LiteralPath $installer)) { $status += "installer missing" }

    $task = Get-ScheduledTask -TaskName $d.Task -ErrorAction SilentlyContinue
    if (-not $task) {
        $status += "NOT REGISTERED"
    } else {
        $trigger = $task.Triggers | Select-Object -First 1
        $actual = "$(($trigger.StartBoundary -split 'T')[1] -replace '\+.*','')"
        if ($actual) { $actual = $actual.Substring(0, 5) }

        if ($expect -ne "UNKNOWN" -and $actual -and $actual -ne $expect) {
            $status += "starts $actual, declared $expect"
        }
        if ((Get-TaskAction $task) -notmatch [regex]::Escape($d.Invokes)) {
            $status += "does not invoke $($d.Invokes)"
        }

        $limit = "$($task.Settings.ExecutionTimeLimit)"
        if ($d.Limit -and $limit -ne $d.Limit) {
            $status += "time limit $limit, declared $($d.Limit)"
        }

        $repeat = if ($trigger.Repetition.Interval) {
            "$($trigger.Repetition.Interval)/$($trigger.Repetition.Duration)"
        } else { "-" }
        if ($d.Repeat -and $repeat -ne $d.Repeat) {
            $status += "repeats $repeat, declared $($d.Repeat)"
        }

        # S4U is what keeps these off the desktop. Registering without an
        # elevated shell silently falls back to Interactive, which is a black
        # console on every fire -- about twenty-three a day across the set --
        # and nothing else would report the difference.
        $logon = "$($task.Principal.LogonType)"
        if ($d.Logon -and $logon -ne $d.Logon) {
            $status += "runs as $logon, declared $($d.Logon)"
        }
    }

    $verdict = if ($status.Count -eq 0) { "OK" }
               elseif ($status -contains "NOT REGISTERED") { "MISSING" }
               elseif ($expect -eq "UNKNOWN") { "UNKNOWN" }
               else { "DRIFT" }
    if ($verdict -ne "OK") { $drift++ }

    $rows += [PSCustomObject]@{
        Task     = $d.Task
        Declared = $expect
        Actual   = if ($actual) { $actual } else { "-" }
        Verdict  = $verdict
        Detail   = ($status -join "; ")
    }
}

Write-Host "=== Declared vs registered ===" -ForegroundColor Cyan
$rows | Format-Table -AutoSize -Wrap

# Anything EGX-named that this repository does not declare. The ORB wrapper was
# invisible exactly this way: registered, working, described nowhere.
$known = $declared.Task
$stray = Get-ScheduledTask | Where-Object {
    $_.TaskPath -eq "\" -and $_.TaskName -like "EGX*" -and $known -notcontains $_.TaskName
}
$strayLive = @($stray | Where-Object { $_.State -ne "Disabled" })
if ($stray) {
    Write-Host "Registered but not declared by this repository:" -ForegroundColor Yellow
    foreach ($s in $stray) { Write-Host "  $($s.TaskName)  [$($s.State)]" }
    Write-Host "  (disabled ones are listed, not counted -- they run nothing)"
    Write-Host ""
}

# --- can a failure even be investigated afterwards? -------------------------
#
# On 2026-09-02 three consecutive fires of two different tasks produced no
# output at all and returned 3221225786 (STATUS_CONTROL_C_EXIT -- killed). Run
# by hand twenty minutes later the same task succeeded. Why they died is
# permanently unknown: this log was disabled at the time, so LastTaskResult --
# one number, with no context -- was the only evidence there was, and reading a
# single number that way is what produced a wrong diagnosis the same week.
#
# It is enabled now, and what it records is the part that was missing: the
# launch reason (event 107), the process id (129), the action started (200) and
# the return code (201), each with a timestamp. That was enough to confirm the
# finalizer's own fix from the outside -- a 14:42 fire returning 0 in ten
# seconds having built nothing, then 15:50, 16:20 and 16:50 each taking thirty
# to fifty seconds of real work.
#
# So the check stays: a machine where it is off again is a machine where the
# next such failure is unexplainable.
#
# Reported here rather than fixed here: enabling a Windows log channel needs
# elevation, and this script deliberately changes nothing.
$logEnabled = $null
try {
    $logEnabled = (Get-WinEvent -ListLog 'Microsoft-Windows-TaskScheduler/Operational' `
                   -ErrorAction Stop).IsEnabled
} catch { }

if ($logEnabled -eq $false) {
    Write-Host "Task Scheduler operational log is DISABLED." -ForegroundColor Yellow
    Write-Host "  A task that dies leaves only LastTaskResult, with no reason attached."
    Write-Host "  Enable it once, from an elevated PowerShell:"
    Write-Host "    wevtutil set-log Microsoft-Windows-TaskScheduler/Operational /enabled:true /maxsize:20971520"
    Write-Host ""
} elseif ($null -eq $logEnabled) {
    Write-Host "Could not read whether the Task Scheduler operational log is enabled." -ForegroundColor Yellow
    Write-Host ""
}

# Report each finding as itself. A summary that says "0 drifted" while exiting
# non-zero is the same kind of lie as a run that says "completed ok" while
# building nothing.
#
# The log being off does not fail the run: nothing has drifted, and a check that
# cries wolf about a machine setting every day is a check people stop reading.
$problems = $drift + $strayLive.Count
if ($problems -eq 0) {
    Write-Host "Every declared task matches what is registered." -ForegroundColor Green
    exit 0
}
if ($drift -gt 0) {
    Write-Host "$drift declared task(s) drifted or could not be determined." -ForegroundColor Red
}
if ($strayLive.Count -gt 0) {
    Write-Host "$($strayLive.Count) enabled task(s) run without being declared here." -ForegroundColor Red
}
exit 1
