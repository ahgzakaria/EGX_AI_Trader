<#
.SYNOPSIS
    Remove the sector liquidity refresh scheduled task.

.DESCRIPTION
    Unregisters the task. It does not delete data/sector_flow.db, the coverage
    report or any log -- removing a schedule should never destroy data.

    Unlike the forward-testing record, what this task produces IS recreatable:
    the rebuild derives everything from daily history that is still on disk, so
    a gap can be closed later by running the script again. What is lost while
    the schedule is off is only currency -- the Sector Liquidity page goes
    quietly stale, which is exactly the condition this task was registered to
    end. The page's own staleness warning is then the only thing left watching.

.EXAMPLE
    .\remove_sector_flow_refresh_task.ps1
#>

[CmdletBinding()]
param(
    [string]$TaskName = "EGX Sector Flow Refresh",
    [switch]$WhatIfOnly
)

$ErrorActionPreference = "Stop"

Write-Host "=== Sector flow refresh - scheduled task removal ===" -ForegroundColor Cyan

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
Write-Host "The sector history, coverage report and logs were left untouched." -ForegroundColor Cyan
Write-Host "Rebuild manually with:" -ForegroundColor Yellow
Write-Host "  venv\Scripts\python.exe scripts\refresh_sector_flow.py" -ForegroundColor Yellow
