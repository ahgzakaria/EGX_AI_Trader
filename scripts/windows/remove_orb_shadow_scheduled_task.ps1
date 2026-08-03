<#
.SYNOPSIS
    Remove the ORB Shadow orchestrator scheduled task. Research Only.

.DESCRIPTION
    Unregisters the task. It does not stop a running orchestrator, does not
    touch the Rubix collector, and does not delete any research database, log
    or report — removing a schedule should never destroy evidence.

    To stop a run in progress, use Ctrl+C in its console (the orchestrator
    shuts down gracefully and commits its cursor) or end that process from
    Task Manager. Never kill the collector.

.EXAMPLE
    .\remove_orb_shadow_scheduled_task.ps1
#>

[CmdletBinding()]
param(
    [string]$TaskName = "ORB_Shadow_Orchestrator",
    [switch]$WhatIfOnly
)

$ErrorActionPreference = "Stop"

Write-Host "=== ORB Shadow Orchestrator - scheduled task removal ===" -ForegroundColor Cyan

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "Task '$TaskName' is not registered; nothing to do." -ForegroundColor Yellow
    return
}

$info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
Write-Host "  State          : $($existing.State)"
if ($info) {
    Write-Host "  Last run       : $($info.LastRunTime)"
    Write-Host "  Last result    : $($info.LastTaskResult)"
}

if ($WhatIfOnly) {
    Write-Host "-WhatIfOnly supplied; nothing was removed." -ForegroundColor Green
    return
}

if ($existing.State -eq "Running") {
    Write-Host "  Task is currently running." -ForegroundColor Yellow
    Write-Host "  Unregistering the schedule does not stop the running process." -ForegroundColor Yellow
    Write-Host "  Stop it with Ctrl+C in its console so the cursor commits cleanly." -ForegroundColor Yellow
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Removed scheduled task '$TaskName'." -ForegroundColor Green
Write-Host "Research databases, logs and reports were left untouched." -ForegroundColor Cyan
