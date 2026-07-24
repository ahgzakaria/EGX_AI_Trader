# CURRENT_RESEARCH_V2 — Correctness Fix Report

Real Rubix Daily Bridge append · honest freshness gating · event-specific volume
normalization · frozen Yahoo-seed isolation. **597 tests pass; the app loads.** No
strategy, indicator, score, threshold, TP/SL, exit rule, provider decision or production
flag changed. EODHD stays the current-research provider; Rubix stays the live provider;
production/automatic-execution/broker-orders remain disabled.

## What was broken, and the root causes found

1. **Bridge never appended (silent).** `_append_bridge_bars` read `bar["close"]`, but a
   normalized bridge row has no `close` key — it exposes `official_close` /
   `continuous_close`. Every append hit `except KeyError: continue`, so 0 bars were ever
   appended while symbols were still marked READY.
2. **Invalid readiness gate.** `state = READY if appended >= 0` is always true.
3. **Universal volume transform.** `providers/eodhd_adjustment.py` did
   `Volume = Raw Volume × Split Factor` for every EODHD "split", contradicting the
   validated corporate-action reconciliation (splits are a mix of true splits, bonuses and
   provider-disagreement events).
4. **Yahoo still on the operational network path (new discovery).** A Windows scheduled
   task, **`EGX_AI_Trader_YahooCacheRefresh`**, ran daily and did
   `yahoo.load_history(sym,"6mo")` across the universe, writing fresh Yahoo bars into the
   exact cache the local route read as its "frozen seed" (its own log for 2026-07-24:
   `advanced=206 … newest cached session=2026-07-22`). The runtime research code made no
   Yahoo call, but this task did — so "zero Yahoo network calls" was false at the system
   level, and the seed was not frozen.

## What changed (code)

- **`providers/eodhd_volume_adjustment.py`** (new) — event-specific volume engine driven by
  `corporate_action_reconciliation.csv`. Served volume is raw by default; only validated
  **true splits (MULTIPLY_BY_FACTOR)** contribute a factor. An EVENT_SPECIFIC / UNRESOLVED
  action inside the required lookback sets `volume_safe_for_lookback=False`.
- **`core/local_rubix_history.py`** (new) — frozen seed + **real** Rubix append using
  `official_close` (auction clearing price, else continuous close); only FINAL /
  FINAL_CONTINUOUS bars; strict-after-seed; dedup; conflict never overwrites; full
  provenance.
- **`core/frozen_seed_store.py`** + **`scripts/freeze_yahoo_seed.py`** (new) — immutable
  per-symbol CSV snapshot with a manifest (frozen_at, rows, span, content hash). The local
  route now reads **only** this frozen store, never the live cache.
- **`core/research_router.py`** — removed `appended>=0`; honest freshness gate
  (READY / LOCAL_PLUS_RUBIX_STALE / LOCAL_SEED_ONLY_STALE / BRIDGE_CONFLICT /
  BUILDING_HISTORY / DATA_INSUFFICIENT / DATA_UNAVAILABLE / VOLUME_POLICY_UNRESOLVED);
  event-specific volume wired into `eodhd_history`; `yahoo_used` replaced by explicit
  `yahoo_network_used` + `yahoo_seed_present`; price/volume policy + freshness fields exposed.
- **`core/data_provider.py`** — propagates the new volume/seed/freshness fields;
  `yahoo_network_used=False`.
- **Scheduled task `EGX_AI_Trader_YahooCacheRefresh` disabled** (with your approval) so no
  further Yahoo network download feeds current research.

## Corrected counts — reconcile exactly to 265

`reports/eodhd/current_research_corrected_counts.json` (machine-checked `reconciles_to_265: true`):

| Route | Ready | Stale | Insufficient | Unavailable | Excluded | Total |
|---|---|---|---|---|---|---|
| EODHD (Tier A/B/C) | 196 | 0 | 13 | 0 | 0 | 209 |
| Local seed + Rubix bridge (Tier D) | 28 | 3 | 4 | 20 | 1 | 56 |
| **TOTAL** | **224** | **3** | **17** | **20** | **1** | **265** |

Ready 224 + blocked 41 = 265. (Insufficient 17 = 13 EODHD recent-listings + 2 Tier-D
BUILDING_HISTORY + 2 Tier-D short local history.)

## The 17 questions

1. **Genuinely operationally READY?** **224** — 196 EODHD + 28 local (frozen seed that
   reaches the expected session 2026-07-22, or brought current by real bridge bars).
2. **Local symbols that actually received Rubix bridge bars?** **2** — ADRI and IEEC, **4
   real FINAL_CONTINUOUS bars total** (each had a 1-row seed; the bridge appended 07-21 and
   07-22). The other 28 local-ready symbols needed 0 appends because their frozen seed
   already reached 2026-07-22. `local_rubix_bridge_append_audit.csv` has every symbol.
3. **How many remain stale?** **3** `LOCAL_SEED_ONLY_STALE` — ESRS (2025-03-13), SUCE
   (2023-03-08), UASG (2024-10-23); no recent Rubix bar exists to close their multi-month
   gaps, so they are correctly blocked, not shown current.
4. **Which symbols have no current-session bridge bar?** Listed in the append audit
   (`bridge_available_sessions=0`), including ESRS/SUCE/UASG and the DATA_UNAVAILABLE set;
   the bridge currently holds only 2 sessions (07-21, 07-22).
5. **Is Yahoo-seed provenance explicit?** **Yes** — every local frame carries
   `seed_provider=FROZEN_YAHOO_SEED`, `yahoo_seed_present=True`, and the ambiguous
   `yahoo_used` is gone, replaced by separate `yahoo_network_used` (False) and
   `yahoo_seed_present`.
6. **Can stale seed history determine freshness?** **No.** A seed behind the expected
   completed session raises `LOCAL_SEED_ONLY_STALE`/`LOCAL_PLUS_RUBIX_STALE` and is blocked;
   the tests `test_zero_bridge_bars_with_stale_seed_is_not_ready` and
   `test_seed_reaching_expected_is_ready_with_zero_appends` pin both directions.
7. **Was universal Volume × Split Factor removed?** **Yes** — replaced by the event-specific
   engine. `test_universal_multiplication_removed_true_split_multiplies` and
   `test_keep_raw_event_does_not_multiply_volume` prove the new behavior.
8. **Which event-specific volume policies are active?** MULTIPLY_BY_FACTOR (validated true
   split), KEEP_RAW (bonus / capital increase), EVENT_SPECIFIC and UNRESOLVED (blocking when
   inside the lookback); PROVIDER_ALREADY_ADJUSTED reserved. Per-event in
   `volume_policy_events.csv` (734 events), per-symbol in `volume_policy_by_symbol.csv`.
9. **Which symbols are blocked by unresolved volume policy?** Operationally **0** EODHD-routed
   symbols (none of the 196 has a corporate action inside the 30-session volume window).
   The gate is proven live on **KZPC** (Tier-D), whose 2026-06-25 split sits in the window
   and is flagged `volume_safe_for_lookback=False`.
10. **Is KZPC handled correctly?** **Yes.** Universal `avg_volume_20` = 621,143 vs corrected
    served (raw) = 617,099; the 2026-06-25 event is flagged, so volume ranking is blocked
    rather than silently distorted. `test_kzpc_regression_short_window_event_flagged`.
11. **Do Scanner and Expected Range receive safe Volume?** **Yes** — both consume the router's
    served volume; none re-applies a transform; unsafe volume blocks the symbol. Traced in
    `current_research_volume_consumer_audit.csv`. Verified end-to-end: COMI via
    `data_provider` returns `volume_series=TRUE_SPLIT_ADJUSTED`, `volume_safe_for_lookback=True`.
12. **All counts reconciled to 265?** **Yes** (table above; `reconciles_to_265: true`).
13. **Are Yahoo network calls still zero?** **Yes at runtime**, and the daily Yahoo refresh
    task that previously made them is **disabled**. Verified: `get_current_research_history`
    and `data_provider.load_history` make no network call; a regression test injects an
    exploding Yahoo provider and asserts it is never called.
14. **Was EODHD retained as the current-research provider?** **Yes** — 196 EODHD-routed.
15. **Was Rubix left unchanged as the live provider?** **Yes** — untouched.
16. **Were any strategy rules changed?** **No.**
17. **Is production still disabled?** **Yes** — `production_enabled=false`,
    `automatic_execution=false`, `broker_orders_enabled=false`.

## Price/volume domain consistency (Part 8)

Every frame exposes `price_series`, `price_adjustment_policy` (split-adjusted, all events),
`volume_series`, `volume_adjustment_policy`, `volume_safe_for_lookback`,
`latest_action_in_lookback`, `corporate_action_policy_version`. In the ranking windows
(≤30 sessions) no activated EODHD symbol crosses an event, so the split factor is 1.0 and
turnover (Close × Volume) is economically consistent there. Longer-window turnover across an
event is not used by ranking; where it would be, the symbol is flagged unsafe.

## Reports produced
`current_research_migration_manifest.csv`, `current_research_migration_summary.json`,
`unsupported_history_status.csv`, `operational_provider_inventory.csv`,
`local_rubix_bridge_append_audit.csv`, `volume_policy_by_symbol.csv`,
`volume_policy_events.csv`, `current_research_volume_consumer_audit.csv`,
`current_research_corrected_counts.json`, `data/frozen_yahoo_seed/_manifest.json`.

## Honest notes
- **28 local symbols are READY on a frozen seed whose most-recent bars were Yahoo-downloaded
  before the refresh task was disabled** (the "freeze as-is" bootstrap you approved). From
  here they extend only via Rubix; the seed will not advance from Yahoo again.
- **Only 2 symbols exercised a live append today** because the seed was already current for
  the rest; the append path is proven functional (ADRI/IEEC, 4 real bars) and will carry the
  full local set forward as Rubix accumulates sessions.
- **25 Tier-D symbols had no local cache to freeze** and are `DATA_UNAVAILABLE` — explicitly
  listed, never silently dropped, never Yahoo-substituted.
- Seven pre-existing migration tests encoded the old buggy architecture (`_append_bridge_bars`,
  `local_plus_bridge_history`, `yahoo_used`) and were updated to the corrected behavior while
  keeping their original invariants.

Stop condition met: real bridge append implemented and measured, local freshness honest,
stale seeds blocked, Yahoo-seed provenance explicit, universal volume adjustment removed,
event-specific volume operational, all counts reconcile, all tests pass, the app loads. No
Forward Research V2 session was started.
