<#
.SYNOPSIS
    Install the daily check that compares this repository's declared scheduled
    tasks against the ones actually registered. Reports only; changes nothing.

.DESCRIPTION
    verify_scheduled_tasks.ps1 exists because three settings drifted without
    anyone noticing: a trigger three minutes before the boundary it had to
    clear, an installer thirty-five minutes away from its registered task, and
    an ORB automation the repository described nowhere. Each was found by
    reading the two sides next to each other, and each had been wrong for
    weeks. A check that only runs when someone remembers to run it is the same
    situation with an extra step.

    It runs at 09:00, ahead of the first task of the trading day (the assisted
    start at 09:10), so the day's automation is verified before anything
    depends on it. Sunday to Thursday, matching the tasks it checks: drift
    introduced on a Friday is reported at 09:00 on Sunday, still before that
    session.

    The check itself appears in the verifier's own declared table. It has to:
    the verifier reports every EGX-named task the repository does not declare,
    and a checker that flags itself every morning is a checker people learn to
    ignore.

    Exit code 1 on drift, which reaches LastTaskResult and the Task Scheduler
    operational log. The readable account is the log file, appended each run so
    the history is there rather than only the latest verdict.

.EXAMPLE
    .\install_scheduled_tasks_verification_task.ps1 -WhatIfOnly
#>

[CmdletBinding()]
param(
    [string]$ProjectRoot = "",
    [string]$TaskName    = "EGX Scheduled Tasks Verification",

    # Before the assisted start at 09:10, so a drifted day is known before it
    # begins rather than after it has already run wrong.
    [string]$StartTime = "09:00",

    [int]$MaxRuntimeMinutes = 10,
    [switch]$WhatIfOnly
)

# $PSScriptRoot is not populated inside a param block under Windows
# PowerShell 5.1, so a default that used it bound an empty string and the script
# died on its own first line with "Cannot bind argument to parameter 'Path'".
# It works under pwsh 7, which is how it passed every time I ran it. Resolved
# here instead, where both hosts agree.
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ProjectRoot) { $ProjectRoot = (Split-Path -Parent (Split-Path -Parent $scriptDir)) }

$ErrorActionPreference = "Stop"

Write-Host "=== EGX Scheduled Tasks Verification - install ===" -ForegroundColor Cyan
Write-Host "  Reports drift. Registers, changes and removes nothing." -ForegroundColor Yellow
Write-Host ""

if (-not (Test-Path -LiteralPath $ProjectRoot)) { throw "Project root not found: $ProjectRoot" }

$verifierRelative = "scripts\windows\verify_scheduled_tasks.ps1"
$verifier = Join-Path $ProjectRoot $verifierRelative
if (-not (Test-Path -LiteralPath $verifier)) { throw "Verifier not found: $verifier" }

# --- Gate: the verifier must know about this task ----------------------------
# Otherwise its first scheduled run reports itself as an undeclared task and
# exits 1, every morning, for the rest of time.
$verifierText = Get-Content -LiteralPath $verifier -Raw
if ($verifierText -notmatch [regex]::Escape($TaskName)) {
    throw ("Refusing to register: $verifierRelative does not declare '$TaskName', " +
           "so it would report this very task as undeclared on every run.")
}
Write-Host "  Verifier declares itself : yes"

# --- Gate: it must still be read-only ----------------------------------------
# This is scheduled unattended against the machine's whole task set. A verifier
# that had grown a Register/Unregister/Set call would be applying changes at
# 09:00 with nobody watching.
foreach ($forbidden in @("Register-ScheduledTask", "Unregister-ScheduledTask",
                         "Set-ScheduledTask", "Start-ScheduledTask",
                         "Stop-ScheduledTask", "Disable-ScheduledTask")) {
    if ($verifierText -match [regex]::Escape($forbidden)) {
        throw ("Refusing to schedule: $verifierRelative contains $forbidden, " +
               "so it is no longer a read-only check.")
    }
}
Write-Host "  Verifier is read-only    : yes"

$logDir = Join-Path $ProjectRoot "logs"
$logFile = Join-Path $logDir "scheduled_tasks_verification.log"

Write-Host ""
Write-Host "Schedule:"
Write-Host "  Start            : $StartTime  (before the 09:10 assisted start)"
Write-Host "  Days             : Sunday-Thursday"
Write-Host "  Log              : logs/scheduled_tasks_verification.log"
Write-Host ""

if ($WhatIfOnly) {
    Write-Host "-WhatIfOnly supplied; nothing was registered." -ForegroundColor Green
    return
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    $existingAction = $existing.Actions | Select-Object -First 1
    $decoded = "$($existingAction.Arguments)"
    if ($decoded -match '-EncodedCommand\s+(\S+)') {
        try {
            $decoded = [Text.Encoding]::Unicode.GetString(
                [Convert]::FromBase64String($matches[1]))
        } catch { }
    }
    if ($decoded -notmatch [regex]::Escape("verify_scheduled_tasks.ps1")) {
        throw ("A task named '$TaskName' already exists and does NOT invoke " +
               "verify_scheduled_tasks.ps1. Refusing to overwrite an unrelated task.")
    }
    Write-Host "  Existing verification task found; it will be replaced." -ForegroundColor Yellow
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# A dated banner, then the run. Without it an appended log is a wall of tables
# with no way to tell which morning any of them came from -- and the exit code,
# which is the verdict, would not be in the file at all.
#
# `*>&1`, not `2>&1`. The verifier reports through Write-Host, which does not
# travel down a `2>&1` pipe: the first captured run logged the comparison table
# and nothing else -- no header, no undeclared-task block, no closing verdict.
# A daily check that silently drops its findings and keeps the part that always
# looks fine is worse than no check. Write-Host goes to the information stream
# in PowerShell 5.1+, and `*` is what merges every stream.
$command = ('$ErrorActionPreference = "Continue"; ' +
            '"" | Out-File -FilePath "{1}" -Append -Encoding utf8; ' +
            '"=== $(Get-Date -Format ''yyyy-MM-dd HH:mm:ss zzz'') ===" | ' +
            'Out-File -FilePath "{1}" -Append -Encoding utf8; ' +
            '& "{0}" *>&1 | Out-File -FilePath "{1}" -Append -Encoding utf8; ' +
            '$rc = $LASTEXITCODE; ' +
            '"exit $rc" | Out-File -FilePath "{1}" -Append -Encoding utf8; ' +
            'exit $rc') -f $verifier, $logFile
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

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-WindowStyle Hidden -NoProfile -NonInteractive -EncodedCommand $encoded" `
    -WorkingDirectory $ProjectRoot

$trigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Sunday, Monday, Tuesday, Wednesday, Thursday -At $StartTime

# S4U, so this runs without a desktop and opens no console window. Interactive
# is the fallback, not the intent: every fire under it put a black window on the
# screen, and across the whole task set that was roughly twenty-three a day.
#
# S4U needs elevation to set, so a normal run cannot have it and must say so
# rather than quietly registering the noisy version. -WindowStyle Hidden on the
# action keeps that fallback down to a flash.
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes $MaxRuntimeMinutes) `
    -StartWhenAvailable `
    -DontStopOnIdleEnd -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

$principalNote = Register-TaskPreferringS4U `
    -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Identity $identity `
    -Description ("Compares this repository's declared scheduled tasks against " +
                  "the registered ones. Read-only; exits 1 on drift.")
Write-Host "  Principal        : $principalNote"

Write-Host "Registered scheduled task '$TaskName'." -ForegroundColor Green
Write-Host "  Exit 1 on drift; the readable account is logs/scheduled_tasks_verification.log"
