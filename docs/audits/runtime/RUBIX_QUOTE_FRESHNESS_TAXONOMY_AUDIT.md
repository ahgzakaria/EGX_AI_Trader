# Rubix Quote Freshness — Taxonomy Audit

Why a quote from the previous exchange session was displayed as `FRESH`, and
what the shared typed contract replaces it with.

**Research only.** No indicator, score, threshold or decision rule changed. No
collector process was started, stopped or inspected for this audit beyond
read-only queries.

---

## 1. The observed case

From `RUN_20260804_005327` (preserved, marked `INVALID_DATA_PROVENANCE`):

| Field | Value |
|---|---|
| `LivePriceTimestamp` | `2026-08-03T11:30:09Z` — 14:30 Cairo |
| `LivePriceReceivedTimestamp` | `2026-08-03T17:26:33Z` |
| `LivePriceStatus` | **`FRESH`** |
| `DataAgeSeconds` | ≈ 16,034 (4.45 hours) |
| `CompletedSessionClose` (EODHD) | 2026-07-30 |

A quote captured at the **close of 2026-08-03** was labelled `FRESH` while the
daily candle underneath it was 2026-07-30.

## 2. The path a quote travels

```
rubix_live_market.db (quotes: ticker, last_price, market_timestamp, received_at)
        │
        ▼
providers/rubix_sqlite_provider.py
    _state_from_snapshot(snapshot, freshness)     ← the status decision
        │  uses core/egx_session.assess_quote_freshness(received_at, exchange_at)
        ▼
core/data_provider.py  →  metadata["live_quote_freshness"] = overlay["freshness"]
        │
        ▼
core/scanner.py:414    →  row["LivePriceStatus"]
        │
        ├─→ scan_results.csv  (archive)
        └─→ dashboard/home.py, dashboard/scan_status_panel.py  (banner + table)
```

## 3. The exact old rule

`core/egx_session.assess_quote_freshness` computes
`lag = trading_session_lag(quote_session, now)` and then:

```python
if phase == "OPEN":
    ...age checks...
    usable = age_minutes <= open_stale_after_minutes and lag == 0
    return SessionFreshness(usable, phase, lag, reason)

usable = lag == 0                       # every non-OPEN phase
return SessionFreshness(usable, phase, lag, reason)
```

`providers/rubix_sqlite_provider._state_from_snapshot` then:

```python
if not freshness.usable:
    return self.STALE, freshness.reason
if freshness.phase == "OPEN":
    ...bar age / exchange age checks...
return self.FRESH, freshness.reason      # ← falls through for EVERY closed phase
```

**So the rule was: `FRESH` whenever the quote's session equals the latest
completed EGX session, for any phase other than OPEN.**

## 4. Why that produced the observed label

At the scan time (≈00:53 Cairo on 2026-08-04) the phase was not `OPEN`, and the
latest *completed* EGX session was 2026-08-03. A quote stamped 2026-08-03 14:30
therefore had `lag == 0` → `usable = True` → the `phase == "OPEN"` branch was
skipped → the function returned `FRESH`.

Nothing malfunctioned. The rule conflated two different claims:

- *"this quote belongs to the most recently completed session"* — true, and
  useful; and
- *"this quote is live"* — false, and what the word `FRESH` communicates.

The elapsed-age check that would have caught 4.45 hours only ever ran inside
the `OPEN` branch, so outside market hours age was not consulted at all.

## 5. Secondary observations

- **No generic seconds budget would fix this.** A quote 30 seconds old across a
  session boundary is not live; a quote 3 hours old at 13:00 during a quiet
  continuous session may be legitimately the last print. Session membership and
  phase decide, and age is a check *within* the permitted session.
- **Phase granularity was binary.** `egx_session_phase` returns `OPEN` for the
  whole 10:00–14:15 continuous window; the 14:15–14:25 closing auction and the
  post-auction period were not distinguished for quote purposes, so an auction
  print and a continuous print were treated identically.
- **Mapping was not part of the freshness verdict.** `SYMBOL_MISSING` existed
  as a separate provider state, but an unmapped symbol and an unavailable quote
  were not distinguishable in `LivePriceStatus` downstream.
- **Timestamp validity had no status.** A missing, malformed, future or
  contradictory timestamp fell into the same buckets as "no data".
- **Overlay permission was implicit.** There was no single place stating
  whether a quote may be *displayed*, *labelled live*, or *enter decision
  inputs* — three separate permissions that the old code answered with one
  string.

## 6. What the shared taxonomy replaces it with

`core/rubix_quote_freshness.py` classifies from explicit typed inputs, with the
evaluation instant injected by the caller — it reads no clock, no database, no
Streamlit state and no global settings.

| Status | Meaning |
|---|---|
| `RUBIX_LIVE_CURRENT` | verified mapping, valid price and timestamps, quote in the current session, phase permits a live quote, receive lag within budget |
| `RUBIX_CURRENT_SESSION_LAST` | belongs to the current/latest session, but the market is closed or it is a closing observation — **displayable, never "Live"** |
| `RUBIX_PREVIOUS_SESSION` | belongs to a session before the permitted one |
| `RUBIX_STALE` | right session, but receive/market freshness exceeds the budget |
| `RUBIX_UNAVAILABLE` | no usable quote |
| `RUBIX_UNMAPPED` | no verified mapping for the operational symbol |
| `RUBIX_TIMESTAMP_INVALID` | missing, malformed, future or contradictory timestamps — deliberately **not** collapsed into unavailable |

The observed 2026-08-03 14:30 quote classifies as `RUBIX_CURRENT_SESSION_LAST`
when evaluated shortly after that session's close, and as
`RUBIX_PREVIOUS_SESSION` once 2026-08-04 becomes the permitted session. Under
no evaluation instant does it classify as `RUBIX_LIVE_CURRENT`.

Overlay permission is a separate typed result stating `may_display_quote`,
`may_label_live`, `may_overlay_display_price` and `may_enter_decision_inputs`
independently, so a quote can be shown without being called live and without
touching the decision inputs.
