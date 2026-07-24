# EODHD Phase 1 — Parallel Historical Provider Validation

Date: 2026-07-18  
Verdict: **PASS** for technical integration before subscription.  
Production routing verdict: **NOT CHANGED / NOT YET APPROVED AS A YAHOO REPLACEMENT**.

## Direct answer

EODHD works technically with EGX AI Trader. The authenticated API returned real
EGX daily OHLCV data using `.EGX` symbols, the normalized frame matches the
existing engine schema, symbol lookup worked, and corporate-action endpoints
returned dividends and splits where available.

The free account is not sufficient for a full production comparison. Its
dashboard showed `20 / 20 API calls`, `Not Active`, and no extra calls remaining.
The free historical window is limited to approximately one year; none of the
returned histories reached the frozen engine's 250-bar minimum.

## Architecture

`EODHDProvider` was added beside Yahoo, Rubix, TickerChart, and Local Cache.
It is registered as an available historical provider but no Scanner, Dashboard,
Forward Testing, Backtest, or Replay route selects it.

```text
Existing runtime route (unchanged)
  Backtest / historical default -> Yahoo

Parallel validation route only
  EODHD API -> EODHDProvider -> normalized daily OHLCV -> audit CSV
```

Authentication is read only from `EODHD_API_TOKEN` or `EODHD_API_KEY`. The token
is never stored in source, settings, CSV, metadata, errors, or reports.

## Implemented API coverage

- Historical Daily OHLCV: `/api/eod/{ticker}.EGX`
- Exchange symbol lookup: `/api/exchange-symbol-list/EGX`
- Search: `/api/search/{query}`
- Dividends: `/api/div/{ticker}.EGX`
- Splits: `/api/splits/{ticker}.EGX`
- Exchange timezone metadata: `Africa/Cairo`
- Strict invalid-candle checks: missing/non-positive prices, negative volume,
  and impossible OHLC ranges

EODHD's official documentation states that its EOD endpoint returns Open, High,
Low, Close, adjusted close, and Volume. Its EGX exchange page uses symbols such
as `COMI.EGX` and declares the exchange timezone as Africa/Cairo.

## Observed free-account results

The audit artifact contains one row for every one of the project's 265 symbols:

| Observation | Result |
|---|---:|
| Project symbols audited | 265 |
| Present in EODHD EGX symbol lookup | 241 |
| Absent from EODHD EGX symbol lookup | 24 |
| Histories returned before/within free-account constraints | 167 |
| Unavailable/connection-limited rows | 98 |
| Invalid candles in returned histories | 0 |
| Earliest free-history date observed | 2025-07-20 |
| Latest date observed | 2026-07-16 |
| Median EODHD row count | 242 |
| Maximum EODHD row count | 243 |
| Histories meeting the engine's 250-bar minimum | 0 |
| Dividend events returned | 62 |
| Split events returned | 38 |

Yahoo local-cache comparison was available for 217 project symbols; 48 Yahoo
cache entries were unavailable. Within overlapping dates, 139 symbols had one
or more Yahoo trading dates not present in EODHD. Most observed differences were
Sundays, but this Phase does not infer that EODHD is wrong: suspended/illiquid
trading, provider date policy, and free-plan truncation must be investigated on
a paid-history sample before any routing decision.

## Why the full validation stopped

The account dashboard showed the free daily allowance fully consumed. The
remaining connection failures are therefore not evidence that the provider
implementation is broken. They are also not evidence that a paid subscription
will cover every project symbol. A paid trial or explicit entitlement confirmation
is still required to validate all 265 histories and the missing-date differences.

No additional EODHD calls were made after the free limit was identified.

## Routing and trading regression

- `backtest_provider` remains Yahoo.
- Scanner/Dashboard/Forward Testing routes remain unchanged.
- EODHD is historical-only and has no live-data behavior.
- No strategy, AI, ranking, indicator, portfolio, risk, entry, exit, Backtest,
  Replay, or Forward Testing calculation was modified.
- Phase 8 replay is not required to prove this pre-subscription connection check;
  EODHD cannot be reached by replay and the active historical route remains Yahoo.
- Full automated regression suite: **147 passed in 31.62 seconds**.
- EODHD targeted tests: **5 passed**.
- Static syntax/import compilation for all new and modified modules: passed.

## Files added

- `providers/eodhd_provider.py`
- `scripts/audit_eodhd_phase1.py`
- `scripts/report_eodhd_phase1.py`
- `tests/test_eodhd_provider.py`
- `reports/eodhd_phase1_symbol_comparison.csv`
- `reports/eodhd_phase1_summary.json`

## Files modified

- `providers/symbol_mapping.py` — centralized `.CA` to `.EGX` conversion.
- `core/data_provider.py` — registers EODHD as a parallel provider only; routing
  keys and defaults are unchanged.

## Evidence and limitations

- Detailed comparison: `reports/eodhd_phase1_symbol_comparison.csv`
- Aggregate summary: `reports/eodhd_phase1_summary.json`
- The free plan proves connectivity, authentication, schema compatibility,
  lookup, and corporate-action access.
- It does not prove ten-year depth, complete 265-symbol coverage, or historical
  equivalence with Yahoo.
- The API token supplied in chat should be rotated before production use.

## Final verdict

**PASS** — EODHD works as a parallel historical provider and is technically
compatible with EGX AI Trader. Do not replace Yahoo or purchase solely on the
free-window comparison. Before subscription, confirm that the selected plan
provides at least ten years of EGX EOD history, corporate actions, and enough API
calls to audit the complete universe.
