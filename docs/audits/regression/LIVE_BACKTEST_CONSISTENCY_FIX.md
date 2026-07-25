# Live Scanner and Backtest Scope Consistency Fix

## Problems

The screenshots showed two different issues that appeared related but had
separate causes:

1. The standard Backtest displayed `47.13%` while the validated Phase 5
   Strategy Only baseline is `65.81%`.
2. The Live Scanner displayed zero BUY opportunities even though three stocks
   passed the frozen technical strategy.

## Root Causes

### Backtest

`47.13%` was produced from Full Available History, including years before the
Walk-Forward OOS coverage. The Phase 5 baseline uses `2020-08-06` through
`2026-06-08` with fresh initial capital. The results were valid individually
but not directly comparable.

### Live Scanner

The scanner used `LIVE_MODEL`, which is the legacy AI hard-filter mode. The
three valid technical BUY candidates had AI probabilities below the unchanged
60% threshold and were therefore demoted to WATCH.

## Fix

- Added `LIVE_ADVISORY` to the unified decision service.
- The frozen strategy remains the final source of BUY/WATCH/AVOID.
- Live AI probability, level, and threshold status remain visible.
- Low AI probability no longer changes a valid strategy BUY.
- Legacy `LIVE_MODEL` hard filtering remains unchanged and tested.
- Added an explicit Backtest Scope selector:
  - `Validated Phase 5 OOS (comparable baseline)`
  - `Full Available History (research only)`
- The UI defaults to the validated comparable scope and displays exact start
  and end dates with every result.
- Full History remains available and shows a warning that it cannot be compared
  directly with Phase 5.
- Non-BUY rows now display `Not evaluated (non-BUY)` instead of an ambiguous
  `None` AI probability.
- Phase 6 metadata records `LIVE_ADVISORY` accurately for new Scan runs.

## Frozen Components

No thresholds, indicators, technical rules, scoring, ranking weights, position
sizing, entries, exits, portfolio constraints, costs, or market-regime logic
were changed.

## Validation

Targeted consistency tests:

```text
4 passed
```

Complete regression suite:

```text
34 passed in 7.79s
```

The legacy hard filter, Strategy Only, Walk-Forward AI, risk overlays,
deterministic portfolio selection, Phase 6 tracking, and Phase 7 forward tests
remain green.
