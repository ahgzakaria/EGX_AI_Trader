<#
Starts the existing read-only adapter without copying its private protocol.
The caller supplies the dynamically assigned signed-session WebSocket URL.
#>
param(
    [Parameter(Mandatory = $false)]
    [string]$StreamerUrl = $env:TICKERCHART_STREAMER_URL,
    [string]$SymbolsFile = "data/universe/egx_universe.csv",
    [string]$Python = ".\venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"

if (-not $env:TICKERCHART_ADAPTER_PATH) {
    throw "Set TICKERCHART_ADAPTER_PATH to the existing adapter folder."
}
if (-not $env:TICKERCHART_DB_PATH) {
    throw "Set TICKERCHART_DB_PATH to the SQLite output file."
}
if (-not $StreamerUrl) {
    throw "Set TICKERCHART_STREAMER_URL to the current signed-session wss://.../ws/ URL."
}

$adapter = Join-Path $env:TICKERCHART_ADAPTER_PATH "adapter.py"
if (-not (Test-Path -LiteralPath $adapter -PathType Leaf)) {
    throw "adapter.py was not found under TICKERCHART_ADAPTER_PATH."
}
if (-not (Test-Path -LiteralPath $SymbolsFile -PathType Leaf)) {
    throw "Symbols file not found: $SymbolsFile"
}

# Convert the engine's Yahoo-style EGX suffix to the adapter's central
# TickerChart subscription convention. No strategy or symbol filtering occurs.
$symbols = Import-Csv -LiteralPath $SymbolsFile |
    Where-Object { -not $_.PSObject.Properties['is_active'] -or $_.is_active -match '^(?i)(1|true|yes|y|active)$' } |
    ForEach-Object {
        $value = if ($_.canonical_symbol) { $_.canonical_symbol } elseif ($_.Symbol) { $_.Symbol } elseif ($_.Ticker) { $_.Ticker } else { $_.PSObject.Properties[0].Value }
        $code = ([string]$value).Trim().ToUpperInvariant() -replace '\.CA$', ''
        if ($code -eq '^CASE30') { 'EGX30.EGY' } else { "$code.EGY" }
    }
$symbolArgument = ($symbols | Where-Object { $_ } | Sort-Object -Unique) -join ','
if (-not $symbolArgument) {
    throw "No symbols could be loaded from $SymbolsFile"
}

& $Python ".\scripts\run_tickerchart_collector.py" --url $StreamerUrl --symbols $symbolArgument --market EGY --database $env:TICKERCHART_DB_PATH
exit $LASTEXITCODE
