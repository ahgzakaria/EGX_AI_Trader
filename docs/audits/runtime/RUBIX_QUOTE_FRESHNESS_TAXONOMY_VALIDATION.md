# Rubix Quote Freshness — Taxonomy Validation

Evidence that the shared taxonomy refuses to call a previous-session quote live,
and that a denied overlay always leaves the EODHD daily close in place.

**No production scan was run, no collector process was started or stopped, no
production database was written, and the archived run was not modified.**

---

## 1. Controlled replay of `RUN_20260804_005327`

The 194 archived rows were replayed through the classifier at six explicit
evaluation instants. Each scenario states the session a live quote would have
to belong to at that moment.

| Evaluated at (Cairo) | Permitted session | Result | Live | Overlay denied | EODHD close retained |
|---|---|---|---:|---:|---:|
| 2026-08-03 13:00 (continuous) | 2026-08-03 | 190 `TIMESTAMP_INVALID`, 4 `UNAVAILABLE` | **0** | 194 | 194 |
| 2026-08-03 14:20 (auction) | 2026-08-03 | 190 `TIMESTAMP_INVALID`, 4 `UNAVAILABLE` | **0** | 194 | 194 |
| 2026-08-03 14:40 (post-market) | 2026-08-03 | 190 `CURRENT_SESSION_LAST`, 4 `UNAVAILABLE` | **0** | 194 | 194 |
| **2026-08-04 00:53 (the archived scan instant)** | 2026-08-03 | 190 `CURRENT_SESSION_LAST`, 4 `UNAVAILABLE` | **0** | 194 | 194 |
| 2026-08-04 11:00 (next session) | 2026-08-04 | 190 `PREVIOUS_SESSION`, 4 `UNAVAILABLE` | **0** | 194 | 194 |
| 2026-08-08 12:00 (weekend) | 2026-08-06 | 190 `PREVIOUS_SESSION`, 4 `UNAVAILABLE` | **0** | 194 | 194 |

**The archive recorded `FRESH` for 193 of 194 rows.** Under the shared taxonomy
not one row is `RUBIX_LIVE_CURRENT` at any instant, the decision overlay is
denied for all 194, and the EODHD daily close is retained for all 194.

The 13:00 and 14:20 scenarios return `TIMESTAMP_INVALID` because the quotes are
stamped 14:30 — genuinely in the future relative to those instants. That is the
correct verdict, not a defect: a quote from the future is never trustworthy.

**No date is relabelled.** The classifier echoes the archived market timestamp
unchanged: archived `2026-08-03T11:30:09+00:00` → classified
`2026-08-03T11:30:09+00:00`.

## 2. Statuses implemented

`RUBIX_LIVE_CURRENT`, `RUBIX_CURRENT_SESSION_LAST`, `RUBIX_PREVIOUS_SESSION`,
`RUBIX_STALE`, `RUBIX_UNAVAILABLE`, `RUBIX_UNMAPPED`, `RUBIX_TIMESTAMP_INVALID`.

`TIMESTAMP_INVALID` is deliberately not collapsed into `UNAVAILABLE`: "we have
no quote" and "we have a quote we cannot trust" call for different action.

## 3. Phase policy

`ExchangePhase` distinguishes `PRE_OPEN`, `CONTINUOUS` (10:00–14:15),
`CLOSING_AUCTION` (14:15–14:25), `POST_MARKET`, `WEEKEND` and `HOLIDAY`. Only
`CONTINUOUS` and `CLOSING_AUCTION` can yield `RUBIX_LIVE_CURRENT`.

Boundaries are pinned by test: 09:59 pre-open, 10:00 continuous, 14:14
continuous, 14:15 auction, 14:25 post-market. Sunday is a trading day; Friday
and Saturday are not; configured holidays are honoured. A 14:20 auction quote
is not judged by continuous-session rules, and a 14:30 quote is not live.

## 4. Overlay permission

Four independent permissions replace one status string: `may_display_quote`,
`may_label_live`, `may_overlay_display_price`, `may_enter_decision_inputs`.

A price enters decision inputs only when the daily symbol is `CURRENT` under
the per-symbol daily model **and** the quote is `RUBIX_LIVE_CURRENT`. A live
quote on a daily-stale symbol is displayable and may be labelled live, but is
refused the decision inputs — the strategy reads a daily candle, and a tick is
not one.

Denied overlays render:

```
Decision price: EODHD daily close
Rubix overlay: not applied — <reason>
```

## 5. Setting

`rubix_live_quote_freshness_seconds`, default **300 s**, registered in
`DEFAULT_SETTINGS` and documented there as data quality, not strategy. The
value preserves the previous intraday behaviour (`open_stale_after_minutes = 5`)
so nothing tightens for genuinely live quotes; the session and phase checks —
which are new — do the work elapsed age was wrongly doing alone. Broken,
negative or missing values fall back to the default.

## 6. Tests

`tests/test_rubix_quote_freshness.py` — **54 tests**: classification for all
seven statuses, timestamp trust (missing, malformed, future, contradictory,
tolerated skew), every phase boundary, Sunday/weekend/holiday, overlay
permission for each non-live status, price-source separation, configuration
fallback, and purity assertions that the classifier reads no clock, database,
settings or network and manages no collector process.

Every test injects its evaluation instant; a structural test asserts none reads
the wall clock, so none changes meaning at a date rollover.

## 7. Mutation checks — all seven bite

| Reverted protection | Result |
|---|---|
| previous-session check removed | 4 failed |
| mapping check removed | 2 failed |
| receive freshness check removed | 2 failed |
| market-phase check removed (post-market called live) | 5 failed |
| overlay permits a daily-stale symbol | 1 failed |
| denied overlay replaces the EODHD close | 7 failed |
| future market timestamp accepted | 1 failed |

Sources restored after each; baseline and restored state both 54 passed.

## 8. Archive provenance added

Future scan rows carry: `RubixQuoteStatus`, `RubixMarketTimestamp`,
`RubixReceiveTimestamp`, `RubixQuoteSession`, `RubixPermittedSession`,
`RubixExchangePhase`, `RubixReceiveLagSeconds`, `RubixFreshnessBudgetSeconds`,
`RubixStatusReason`, `RubixOverlayApplied`, `RubixDecisionInputAllowed`,
`DisplayPriceSource`, `DecisionPriceSource`, `RubixOverlayDenialReason`,
`DailyCandleSession`, `DailyFreshnessStatus`.

`RUN_20260804_005327` is untouched and keeps its `INVALID_DATA_PROVENANCE`
marker; it is used only as replay input.

## 9. Scope

This task delivers the shared taxonomy, the overlay-permission model, and the
scanner and Dashboard consumers. **Stock Details, Watchlist and AI Analysis
integration are not part of it** and remain outstanding, as does the split
current-decision/full-audit export. The scanner classifies the quote only
after the daily gate and the decision, so no quote can resurrect a
daily-skipped symbol or alter a strategy result.
