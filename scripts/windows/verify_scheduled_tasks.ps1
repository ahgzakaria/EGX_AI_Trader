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
    [string]$ProjectRoot = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
    [string]$PythonExe   = ""
)

$ErrorActionPreference = "Stop"
if (-not $PythonExe) { $PythonExe = Join-Path $ProjectRoot "venv\Scripts\python.exe" }

# What this repository claims should exist. `Expect` is the Cairo start time;
# 'derived' means the owning script is asked for it, the way the finalizer's
# installer does, so this table cannot become a second copy of that boundary.
$declared = @(
    @{ Task = "EGX Rubix Daily Finalizer"
       Installer = "install_rubix_daily_finalizer_task.ps1"
       Invokes = "run_rubix_daily_finalizer.py"
       Expect = "derived"; Margin = 5 }

    @{ Task = "EGX Sector Flow Refresh"
       Installer = "install_sector_flow_refresh_task.ps1"
       Invokes = "refresh_sector_flow.py"
       Expect = "16:00" }

    @{ Task = "EGX Confirmed Breakout Forward Test"
       Installer = "install_confirmed_breakout_forward_task.ps1"
       Invokes = "record_confirmed_breakout_forward.py"
       Expect = "15:00" }

    @{ Task = "EGX Gap Forward Recorder"
       Installer = "install_gap_forward_task.ps1"
       Invokes = "record_gap_forward.py"
       Expect = "14:40" }

    @{ Task = "EGX Rubix Assisted Start"
       Installer = "install_rubix_assisted_start_task.ps1"
       Invokes = "run_rubix_assisted_start.py"
       Expect = "09:10" }

    @{ Task = "EGX ORB Full Shadow Automation"
       Installer = "install_orb_full_shadow_task.ps1"
       Invokes = "run_daily_orb_automation.ps1"
       Expect = "09:45" }
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

# Report each finding as itself. A summary that says "0 drifted" while exiting
# non-zero is the same kind of lie as a run that says "completed ok" while
# building nothing.
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
