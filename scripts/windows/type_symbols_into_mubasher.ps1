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
    [string]$SymbolFile = "",

    # Test with a handful before committing to the whole universe.
    [int]$First = 0,
    [int]$Skip  = 0,

    # After typing a symbol, before Enter. This is a live-search combo: it
    # queries as you type and Enter only commits once a result has resolved.
    # At 180 ms the first run produced "WDYTALMTANMTAQATMGHTRTOTWSAT" in the box
    # and "Stocks(0) No Results" in the dropdown -- symbols concatenated because
    # Enter had nothing to commit and the field was never cleared.
    [int]$DelayMs = 700,

    # After Enter, before the next symbol.
    [int]$CommitMs = 350,

    # Seconds to focus the "Add symbol to export" field before typing starts.
    [int]$CountdownSeconds = 8,

    # How a symbol is committed to the list. "Enter" or "TabEnter".
    [ValidateSet("Enter", "TabEnter")]
    [string]$Commit = "Enter",

    [switch]$WhatIfOnly
)

# $PSScriptRoot is not populated inside a param block under Windows
# PowerShell 5.1, so a default that used it bound an empty string and the script
# died on its own first line with "Cannot bind argument to parameter 'Path'".
# It works under pwsh 7, which is how it passed every time I ran it. Resolved
# here instead, where both hosts agree.
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $SymbolFile) { $SymbolFile = (Join-Path (Split-Path -Parent (Split-Path -Parent $scriptDir)) "data\universe\mubasher_export_symbols.txt") }

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
Write-Host "  Pace     : $DelayMs ms lookup + $CommitMs ms commit  (about $([math]::Round($symbols.Count * ($DelayMs + $CommitMs + 40) / 1000.0)) s total)"
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
    # Select-all first, so this symbol replaces whatever is in the box rather
    # than appending to it. Without it one failed commit corrupts every symbol
    # after it: the field kept accumulating letters until it matched nothing at
    # all, and the run silently degraded from "some symbols missing" to "no
    # symbols at all, plus a nonsense string".
    [System.Windows.Forms.SendKeys]::SendWait("^a")
    Start-Sleep -Milliseconds 40

    [System.Windows.Forms.SendKeys]::SendWait($symbol)
    Start-Sleep -Milliseconds $DelayMs          # let the lookup resolve
    if ($Commit -eq "TabEnter") {
        [System.Windows.Forms.SendKeys]::SendWait("{TAB}")
        Start-Sleep -Milliseconds 60
    }
    [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
    Start-Sleep -Milliseconds $CommitMs

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
Write-Host "  Each symbol now replaces the box rather than appending, so a failed" -ForegroundColor Yellow
Write-Host "  one costs that symbol and nothing after it."
