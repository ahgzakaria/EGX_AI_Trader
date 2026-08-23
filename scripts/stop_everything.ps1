# Stop everything this project runs, cleanly.
#
# "Cleanly" is the whole point. Closing the console window kills the collector
# outright: across 3516 log lines the supervisor had never once reached its own
# shutdown path, so every stop skipped the final WAL checkpoint and left the
# health file still claiming the collector was up. This asks first and only
# kills what refuses to leave.
#
# Order matters. The collector goes first and is given time, because it is the
# one holding a 5.9 GB database open. The dashboard is stopped last: it is a
# viewer, it holds nothing, and killing it outright costs nothing.
#
# Pure ASCII on purpose. Windows PowerShell 5.1 reads a BOM-less UTF-8 file as
# ANSI, and one non-ASCII character in a string is enough to make it a parse
# error -- which is how a scheduled script once failed while running fine in
# the dev shell.

[CmdletBinding()]
param(
    # How long the collector gets to shut down on its own before being killed.
    [int]$GraceSeconds = 25,
    # Report what is running and what would happen, then change nothing.
    [switch]$WhatIfOnly
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$stopFlag = Join-Path $root 'data\runtime\stop_requested.flag'
$log = Join-Path $root 'logs\rubix_supervisor.log'

function Get-ProjectPython {
    Get-CimInstance Win32_Process -Filter "name='python.exe' OR name='pythonw.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*EGX_AI_Trader*" }
}

function Get-Role([string]$commandLine) {
    switch -Wildcard ($commandLine) {
        '*rubix_collector_supervisor*' { 'collector supervisor'; break }
        '*rubix_feed.cli*'             { 'rubix feed'; break }
        '*streamlit run*'              { 'dashboard'; break }
        '*run_daily_orb_automation*'   { 'ORB automation'; break }
        default                        { 'other project process' }
    }
}

$running = @(Get-ProjectPython)
if ($running.Count -eq 0) {
    Write-Host "Nothing is running. Everything is already stopped." -ForegroundColor Green
    exit 0
}

Write-Host "Running now:" -ForegroundColor Cyan
foreach ($p in $running) {
    $age = [int]((Get-Date) - $p.CreationDate).TotalMinutes
    Write-Host ("  PID {0,-6} {1,-22} up {2} min" -f $p.ProcessId, (Get-Role $p.CommandLine), $age)
}

if ($WhatIfOnly) {
    Write-Host "`n-WhatIfOnly: nothing was stopped." -ForegroundColor Yellow
    exit 0
}

# --- 1. Ask the supervisor to stop itself ---------------------------------
$supervisors = @($running | Where-Object { $_.CommandLine -like '*rubix_collector_supervisor*' })
if ($supervisors.Count -gt 0) {
    New-Item -ItemType Directory -Force -Path (Split-Path $stopFlag) | Out-Null
    Set-Content -Path $stopFlag -Value ("stop requested {0}" -f (Get-Date -Format o)) -Encoding utf8
    Write-Host "`nAsked the collector to stop. Waiting up to $GraceSeconds s..." -ForegroundColor Cyan

    # The supervisor checks the flag once per heartbeat, then leaves through the
    # same finally every other exit uses: child stopped, database checkpointed,
    # health file marked, lock released. That takes a moment on a 5.9 GB file.
    $deadline = (Get-Date).AddSeconds($GraceSeconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 700
        if (-not (Get-CimInstance Win32_Process -Filter "name='python.exe'" -ErrorAction SilentlyContinue |
                  Where-Object { $_.CommandLine -like '*rubix_collector_supervisor*' })) { break }
    }

    $stillUp = @(Get-CimInstance Win32_Process -Filter "name='python.exe' OR name='pythonw.exe'" -ErrorAction SilentlyContinue |
                 Where-Object { $_.CommandLine -like '*rubix_collector_supervisor*' })
    if ($stillUp.Count -eq 0) {
        # Trust the log, not the disappearance: a process can vanish by dying.
        $shutdownLogged = $false
        if (Test-Path $log) {
            $shutdownLogged = [bool](Get-Content $log -Tail 40 |
                                     Where-Object { $_ -like '*supervisor_shutdown*' })
        }
        if ($shutdownLogged) {
            Write-Host "  Collector shut down cleanly and checkpointed the database." -ForegroundColor Green
        } else {
            Write-Host "  Collector is gone, but wrote no shutdown line -- it did not exit cleanly." -ForegroundColor Yellow
        }
    } else {
        Write-Host "  Collector did not stop in time. Forcing it." -ForegroundColor Yellow
        $stillUp | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    }

    # The flag is a request, not a setting. Leaving it behind would stop
    # tomorrow morning's collector a second after it starts.
    Remove-Item $stopFlag -Force -ErrorAction SilentlyContinue
}

# --- 2. Everything else ----------------------------------------------------
$rest = @(Get-ProjectPython)
foreach ($p in $rest) {
    Write-Host ("Stopping {0} (PID {1})" -f (Get-Role $p.CommandLine), $p.ProcessId)
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Milliseconds 900

# --- 3. Say what is actually true now --------------------------------------
$left = @(Get-ProjectPython)
$ports = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
           Where-Object { $_.LocalPort -in 8501, 8502, 8511 })

Write-Host ""
if ($left.Count -eq 0 -and $ports.Count -eq 0) {
    Write-Host "All stopped. No project process is running and no dashboard port is open." -ForegroundColor Green
    exit 0
}

Write-Host "Something is still up:" -ForegroundColor Red
$left | ForEach-Object { Write-Host ("  PID {0} {1}" -f $_.ProcessId, (Get-Role $_.CommandLine)) }
$ports | ForEach-Object { Write-Host ("  port {0} held by PID {1}" -f $_.LocalPort, $_.OwningProcess) }
exit 1
