<#
.SYNOPSIS
    Type the active universe into MubasherTrade PRO's "Add symbol to export"
    box, one symbol at a time, so the Export History dialog can be filled
    without 240 manual entries.

.DESCRIPTION
    Mubasher's export dialog holds a list -- it has a Remove All button -- but
    the only way in is that one search field, and typing 240 codes by hand is
    the reason the export has not happened.

    This types them. Nothing more: it does not click Export, does not choose
    dates, does not touch the output path. Those stay yours, because they are
    the parts where a wrong value silently produces the wrong file.

    IT IS A BLIND TYPIST. It cannot see the dialog, cannot tell whether a
    symbol was accepted, and cannot tell whether the field is even focused. It
    sends keystrokes to whatever window is in front when the countdown ends.
    That is why -First exists: run it with three symbols, look at the list, and
    only then run the rest.

    If the field needs a dropdown pick rather than a plain Enter, adjust
    -Commit. Some builds want Enter, some want Tab then Enter, and I cannot
    tell which from here.

.EXAMPLE
    # Always start here. Three symbols, then look at the dialog.
    .\type_symbols_into_mubasher.ps1 -First 3

.EXAMPLE
    .\type_symbols_into_mubasher.ps1

.EXAMPLE
    # Resume after a stall, skipping what is already in the list.
    .\type_symbols_into_mubasher.ps1 -Skip 120 -DelayMs 250
#>

[CmdletBinding()]
param(
    [string]$SymbolFile = (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) "data\universe\mubasher_export_symbols.txt"),

    # Test with a handful before committing to the whole universe.
    [int]$First = 0,
    [int]$Skip  = 0,

    # Between keystrokes. Too fast and the dialog's own lookup drops entries;
    # this is a search box that queries as you type.
    [int]$DelayMs = 180,

    # Seconds to focus the "Add symbol to export" field before typing starts.
    [int]$CountdownSeconds = 8,

    # How a symbol is committed to the list. "Enter" or "TabEnter".
    [ValidateSet("Enter", "TabEnter")]
    [string]$Commit = "Enter",

    [switch]$WhatIfOnly
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms

if (-not (Test-Path -LiteralPath $SymbolFile)) {
    throw "Symbol list not found: $SymbolFile"
}

$symbols = Get-Content -LiteralPath $SymbolFile |
           ForEach-Object { $_.Trim().ToUpperInvariant() } |
           Where-Object { $_ -and $_ -notmatch '^#' }

if ($Skip  -gt 0) { $symbols = $symbols | Select-Object -Skip $Skip }
if ($First -gt 0) { $symbols = $symbols | Select-Object -First $First }

# SendKeys treats + ^ % ~ ( ) { } [ ] as syntax. EGX codes are letters and
# digits, but a stray one would be silently reinterpreted rather than typed,
# so refuse instead of sending something other than what the file says.
$unsafe = $symbols | Where-Object { $_ -notmatch '^[A-Z0-9_.-]+$' }
if ($unsafe) {
    throw ("These symbols contain characters SendKeys would reinterpret: " +
           ($unsafe -join ", ") + ". Remove them from the list first.")
}

Write-Host "=== Mubasher export: symbol typist ===" -ForegroundColor Cyan
Write-Host "  Types symbols only. Does not press Export, set dates, or pick a path." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Source   : $SymbolFile"
Write-Host "  Symbols  : $($symbols.Count)"
Write-Host "  Commit   : $Commit"
Write-Host "  Pace     : $DelayMs ms  (about $([math]::Round($symbols.Count * ($DelayMs + 120) / 1000.0)) s total)"
Write-Host ""

if ($WhatIfOnly) {
    Write-Host "  First 10 : $(($symbols | Select-Object -First 10) -join ', ')"
    Write-Host "-WhatIfOnly supplied; nothing was typed." -ForegroundColor Green
    return
}

Write-Host "  Click into Mubasher's 'Add symbol to export' box now." -ForegroundColor Yellow
Write-Host "  Keystrokes go to whatever window is focused when this reaches zero." -ForegroundColor Yellow
Write-Host "  Ctrl+C cancels." -ForegroundColor Yellow
Write-Host ""
for ($i = $CountdownSeconds; $i -gt 0; $i--) {
    Write-Host "`r  starting in $i ... " -NoNewline
    Start-Sleep -Seconds 1
}
Write-Host "`r  typing.            "

$done = 0
foreach ($symbol in $symbols) {
    [System.Windows.Forms.SendKeys]::SendWait($symbol)
    Start-Sleep -Milliseconds $DelayMs          # let the lookup catch up
    if ($Commit -eq "TabEnter") {
        [System.Windows.Forms.SendKeys]::SendWait("{TAB}")
        Start-Sleep -Milliseconds 60
    }
    [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
    Start-Sleep -Milliseconds 120

    $done++
    if ($done % 20 -eq 0) {
        Write-Host ("  {0,3} / {1}  ({2})" -f $done, $symbols.Count, $symbol)
    }
}

Write-Host ""
Write-Host "Typed $done symbol(s)." -ForegroundColor Green
Write-Host "  Now check the dialog's list before exporting:" -ForegroundColor Yellow
Write-Host "    - does it hold $done entries, or did some lookups drop?"
Write-Host "    - set From/To to the range you want; they default to one day"
Write-Host "    - set the output path, then press Export yourself"
Write-Host ""
Write-Host "  If entries are missing, re-run with -Skip <count already in> and a" -ForegroundColor Yellow
Write-Host "  larger -DelayMs. The search field is the bottleneck, not the typing."
