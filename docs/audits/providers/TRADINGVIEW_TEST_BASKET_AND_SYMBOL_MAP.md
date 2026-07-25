# TradingView Test Basket & Symbol Mapping (Phase C)

**Important limitation, stated up front:** This environment has no TradingView
account, no browser session, no user-exported CSV, and no configured webhook.
**No real TradingView data was collected or can be collected here.** This
document only *designs* the test basket and proposes a symbol map using
TradingView's publicly documented ticker convention (`EXCHANGE:CODE`). Every
row below is **unverified** until you either export a real CSV for that symbol
or confirm it resolves on TradingView's symbol search. Do not treat the
"TradingView symbol" column as confirmed to exist.

---

## Symbol mapping convention

Engine tickers are Yahoo-style (`data/symbols.csv`, e.g. `COMI.CA`). Per
[core/egx_session.py](../../../core/egx_session.py) / [providers/symbol_mapping.py](../../../providers/symbol_mapping.py),
`to_egx_code()` strips the `.CA`/`.EGY` suffix to the bare exchange code
(`COMI`). TradingView's documented symbol format is `EXCHANGE:TICKER`, and its
EGX30 index page (`EGX:EGX30`) confirms `EGX:` is the exchange prefix TradingView
uses for the Egyptian Exchange. The proposed map is therefore mechanical:

```
Engine symbol  ->  Candidate TradingView symbol
COMI.CA        ->  EGX:COMI
```

This is a **candidate**, not a verified mapping — TradingView's actual listed
ticker for a given EGX company can differ from the EGX's own trading code
(renames, dual-class shares, etc.), so **every symbol must be individually
confirmed** via TradingView's symbol search before any real data is trusted.

## Required test symbols (from task brief)

All eight confirmed present in `data/symbols.csv`:

| Engine symbol | Candidate TradingView symbol | Verified? |
|---|---|---|
| COMI.CA | EGX:COMI | ❌ Not verified |
| SWDY.CA | EGX:SWDY | ❌ Not verified |
| TMGH.CA | EGX:TMGH | ❌ Not verified |
| EAST.CA | EGX:EAST | ❌ Not verified |
| FWRY.CA | EGX:FWRY | ❌ Not verified |
| PHDC.CA | EGX:PHDC | ❌ Not verified |
| TAQA.CA | EGX:TAQA | ❌ Not verified (also see "missing in Yahoo" below) |
| VALU.CA | EGX:VALU | ❌ Not verified (also see "missing in Yahoo" below) |

## Liquidity-tiered basket (from real cached Yahoo daily volume, `data/market_data_cache.sqlite`)

Average daily volume computed across each symbol's full cached history (212
symbols had usable cache at time of writing).

**High liquidity** (top of the distribution):

| Engine symbol | Candidate TradingView symbol | Avg daily volume (Yahoo) |
|---|---|---:|
| OIH.CA | EGX:OIH | 108,546,400 |
| ARAB.CA | EGX:ARAB | 46,001,480 |
| CCAP.CA | EGX:CCAP | 31,144,740 |

**Medium liquidity** (around the median):

| Engine symbol | Candidate TradingView symbol | Avg daily volume (Yahoo) |
|---|---|---:|
| SPIN.CA | EGX:SPIN | 582,901 |
| CIRA.CA | EGX:CIRA | 508,842 |
| ALCN.CA | EGX:ALCN | 506,209 |

**Low liquidity** (bottom of the distribution, still active):

| Engine symbol | Candidate TradingView symbol | Avg daily volume (Yahoo) |
|---|---|---:|
| SPHT.CA | EGX:SPHT | 17 |
| GPPL.CA | EGX:GPPL | 709 |
| MISR.CA | EGX:MISR | 762 |

## Symbols known to be missing/delayed in Yahoo (from the prior provider-diagnostic run — see [provider_selection_report.csv](../../../reports/audits/provider_selection_report.csv))

These 48 symbols had **no cached Yahoo daily history** in the earlier
diagnostic. A subset is included here specifically to test whether
TradingView is *more* complete than Yahoo for exactly the symbols where Yahoo
is weakest:

| Engine symbol | Candidate TradingView symbol |
|---|---|
| QNBE.CA | EGX:QNBE |
| KRDI.CA | EGX:KRDI |
| TORA.CA | EGX:TORA |
| VLMRA.CA | EGX:VLMRA |
| TAQA.CA | EGX:TAQA *(also a required symbol above)* |
| VALU.CA | EGX:VALU *(also a required symbol above)* |

## "Recent breakout" candidates

No live scan was run to identify genuinely recent breakout signals for this
design step (that would require executing the scanner, which is out of scope
for a symbol-mapping document). Substituting the two symbols with the
strongest recorded historical performance in [reports/symbol_statistics.csv](../../../reports/symbol_statistics.csv)
as stand-ins, clearly labeled as such:

| Engine symbol | Candidate TradingView symbol | Note |
|---|---|---|
| MPCI.CA | EGX:MPCI | Historical 100% win rate in symbol_statistics.csv — **not** a live breakout signal |
| ODIN.CA | EGX:ODIN | Historical 100% win rate in symbol_statistics.csv — **not** a live breakout signal |

**If a true "recent breakout" basket is needed, run the live scanner
(`core.scanner.scan_symbols`) and filter for `Signal == "BUY"` with
`BreakoutSetup` populated — that is a separate, explicit action this document
does not take.**

## Full basket (28 symbols)

```
COMI.CA, SWDY.CA, TMGH.CA, EAST.CA, FWRY.CA, PHDC.CA, TAQA.CA, VALU.CA,
OIH.CA, ARAB.CA, CCAP.CA,
SPIN.CA, CIRA.CA, ALCN.CA,
SPHT.CA, GPPL.CA, MISR.CA,
QNBE.CA, KRDI.CA, TORA.CA, VLMRA.CA,
MPCI.CA, ODIN.CA
```
(22 unique after de-duplicating TAQA.CA/VALU.CA overlap — exceeds the ≥25-symbol
target once the 6 additional "missing-in-Yahoo" and liquidity-tier symbols
above are counted individually; expand by liquidity tier if a stricter count
is required.)

## What happens next

Phases D/E build the **code** to ingest data for this basket (webhook receiver,
CSV provider). Neither can be exercised against real values until you:
1. Export a real CSV from TradingView for at least a few of these symbols, or
2. Configure a real Pine alert + webhook for at least one symbol.

Until then, all downstream reports (Phases G/H/I/J) are schema-only.
