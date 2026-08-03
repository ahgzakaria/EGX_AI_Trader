<#
.SYNOPSIS
    Remove the EGX Rubix collector autostart scheduled task.

.DESCRIPTION
    Unregisters the schedule only. It does not stop a running collector, does
    not touch the supervisor lock or PID file, and deletes no database, log or
    report.

    To stop a running collector, use the official launcher's Stop button, or
    send SIGTERM to the supervisor - it shuts down cleanly, writes a final
    health record and runs database maintenance. Never hard-kill it.

.EXAMPLE
    .\remove_rubix_collector_scheduled_task.ps1 -WhatIfOnly
#>

[CmdletBinding()]
param(
    [string]$TaskName = "EGX Rubix Collector Auto Start",
    [switch]$WhatIfOnly
)

$ErrorActionPreference = "Stop"

Write-Host "=== EGX Rubix Collector - scheduled task removal ===" -ForegroundColor Cyan

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "Task '$TaskName' is not registered; nothing to do." -ForegroundColor Yellow
    return
}

# Never remove a task that is not ours, even if the name matches.
$action = $existing.Actions | Select-Object -First 1
if ($action.Arguments -notmatch [regex]::Escape("rubix_collector_supervisor.py")) {
    throw ("A task named '$TaskName' exists but does NOT invoke " +
           "rubix_collector_supervisor.py. Refusing to remove an unrelated task.")
}

$info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
Write-Host "  State       : $($existing.State)"
if ($info) {
    Write-Host "  Last run    : $($info.LastRunTime)"
    Write-Host "  Last result : $($info.LastTaskResult)"
}

if ($WhatIfOnly) {
    Write-Host "-WhatIfOnly supplied; nothing was removed." -ForegroundColor Green
    return
}

if ($existing.State -eq "Running") {
    Write-Host "  Task is currently running." -ForegroundColor Yellow
    Write-Host "  Unregistering the schedule does not stop the collector." -ForegroundColor Yellow
    Write-Host "  Stop it from the official launcher so it shuts down cleanly." -ForegroundColor Yellow
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Removed scheduled task '$TaskName'." -ForegroundColor Green
Write-Host "The collector, its lock, databases and logs were left untouched." -ForegroundColor Cyan
