<#
.SYNOPSIS
    Remove the CONFIRMED_VOLUME_BREAKOUT forward-testing scheduled task.

.DESCRIPTION
    Unregisters the task. It does not delete data/confirmed_breakout_forward.db,
    the log, or any status file — removing a schedule should never destroy
    evidence, and this evidence in particular cannot be recreated: it is a
    record of what the rule said before anyone knew the answer.

    Stopping the schedule stops the record growing. The sessions already in it
    stay exactly as they were; the store refuses edits and deletions anyway.

.EXAMPLE
    .\remove_confirmed_breakout_forward_task.ps1
#>

[CmdletBinding()]
param(
    [string]$TaskName = "EGX Confirmed Breakout Forward Test",
    [switch]$WhatIfOnly
)

$ErrorActionPreference = "Stop"

Write-Host "=== Confirmed Breakout forward test - scheduled task removal ===" -ForegroundColor Cyan

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
    Write-Host "  Next run       : $($info.NextRunTime)"
}

if ($WhatIfOnly) {
    Write-Host "-WhatIfOnly supplied; nothing was removed." -ForegroundColor Green
    return
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Removed scheduled task '$TaskName'." -ForegroundColor Green
Write-Host "The forward record, its log and its status files were left untouched." -ForegroundColor Cyan
Write-Host "A stopped schedule means the record stops growing: every session" -ForegroundColor Yellow
Write-Host "missed is an observation that cannot be recovered later." -ForegroundColor Yellow
