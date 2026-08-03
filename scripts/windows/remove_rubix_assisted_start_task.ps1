<#
.SYNOPSIS
    Remove the Rubix Assisted Start scheduled task.

.DESCRIPTION
    Unregisters the schedule only. It does not stop a running collector, does
    not touch the supervisor lock or PID file, and deletes no auth frame,
    database, log or report.

    Removing a schedule must never destroy evidence, and it must never take
    custody of credential-adjacent files: any frame in the inbox is left
    exactly where the user put it.

.EXAMPLE
    .\remove_rubix_assisted_start_task.ps1 -WhatIfOnly
#>

[CmdletBinding()]
param(
    [string]$TaskName = "EGX Rubix Assisted Start",
    [switch]$WhatIfOnly
)

$ErrorActionPreference = "Stop"

Write-Host "=== EGX Rubix Assisted Start - scheduled task removal ===" -ForegroundColor Cyan

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "Task '$TaskName' is not registered; nothing to do." -ForegroundColor Yellow
    return
}

$action = $existing.Actions | Select-Object -First 1
if ($action.Arguments -notmatch [regex]::Escape("run_rubix_assisted_start.py")) {
    throw ("A task named '$TaskName' exists but does NOT invoke " +
           "run_rubix_assisted_start.py. Refusing to remove an unrelated task.")
}

$info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
Write-Host "  State       : $($existing.State)"
if ($info) { Write-Host "  Last run    : $($info.LastRunTime)" }

if ($WhatIfOnly) {
    Write-Host "-WhatIfOnly supplied; nothing was removed." -ForegroundColor Green
    return
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Removed scheduled task '$TaskName'." -ForegroundColor Green
Write-Host "The collector, auth frames, databases and logs were left untouched." -ForegroundColor Cyan
