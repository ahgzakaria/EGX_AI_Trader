# EGX Hybrid Daily OHLCV Bridge — replacing the Yahoo freshness dependency

**The operational blocker is fixed.** A locally-finalized Rubix completed-daily bar
now makes the previous session **current** without waiting for Yahoo to update
overnight, so the Expected Range Scalper's 09:45 Stage A snapshot no longer reports
a false `HISTORY_STALE`. No strategy, expected-range formula, weight, TP/SL or
scenario logic changed; production stays disabled.

| Flag | Value |
|---|---|
| `paper_enabled` | true |
| `production_enabled` | **false** |
| `automatic_execution` / `broker_orders_enabled` | **false** |
| TP / SL | **+2% / -2%** (unchanged) |
| `use_rubix_final_daily` | true |

## Architecture (isolated, records-only)

```
Trusted external provider (EODHD / Twelve Data)   ── historical backfill ──┐   [DISABLED until audited]
                                                                           ▼
Rubix local finalizer  ── builds the latest COMPLETED session from        Reconciliation ──► Normalized
chronological events after the 14:15–14:25 auction ───────────────────►   layer            Daily Cache
                                                                                              │  (per-row
                                                                        provenance per row    │  provenance +
                                                                                              ▼  versioning)
                                          Expected Range Scalper (Stage A freshness overlay) · Swing/Daily* · Backtests*
```
`core/daily_bridge/`: `schema.py` (canonical bar), `rubix_daily_builder.py`
(Phase 2/3/4), `normalized_cache.py` (versioned, provenance), `reconcile.py`
(Phase 7), `provider_chain.py` (Phase 8, external tier off). Scripts:
`run_rubix_daily_finalizer.py` (Phase 5), `run_daily_bridge_validation.py`
(Phase 10/11). *Swing/Daily and Backtests keep their own untouched Yahoo path — the
overlay is wired **only** into the ERS historical accessor.

## Real 2026-07-22 validation

Built from 201,586 captured events (received-timestamp chronology, since the
exchange `market_timestamp` can freeze):

| Metric | Value |
|---|---|
| Symbols | 265 |
| **FINAL bars** | **216** (209 `COMPLETE_DAILY_BAR` + 7 `COMPLETE_CONTINUOUS_AUCTION_MISSING`) |
| Naturally inactive (not failures) | 27 `SYMBOL_INACTIVE` |
| Incomplete | 13 `DATA_GAP`, 8 `PARTIAL_LATE_START`, 1 `PARTIAL_EARLY_STOP` |
| Auction events present | 244 symbols |

Worked examples (official close = auction result, kept separate from continuous):

| Symbol | Open | High | Low | continuous_close | auction_last | official_close | Volume |
|---|--:|--:|--:|--:|--:|--:|--:|
| COMI.CA | 139.90 | 140.13 | 138.80 | 139.95 | 139.90 | **139.90** | 3,972,917 |
| ARAB.CA | 0.239 | 0.239 | 0.232 | 0.234 | 0.232 | **0.232** | 509,729,868 |
| ABUK.CA | 72.91 | 73.95 | 72.02 | 72.38 | 72.38 | **72.38** | 1,267,328 |

## Key design decisions (declared + tested)

- **Chronology** uses `received_at`; a frozen `market_timestamp` never breaks a bar
  (tested with a bar whose exchange timestamp is frozen at 2026-07-01).
- **High/Low policy = `CONTINUOUS_SESSION_HL`**: daily High/Low come from continuous
  Last prints only; the single auction clearing price is stored separately and
  **not** mixed into the continuous range the scalper uses (`auction_included_in_hl=false`).
- **High/Low use traded Last only** — never Bid/Ask.
- **Volume = cumulative traded shares**: validated monotonic (no resets); the daily
  total is the cumulative **max**, never a sum of repeated snapshots. A decreasing
  cumulative volume is flagged and the bar is not marked FINAL.
- **Duplicate identical snapshots suppressed**; naturally inactive symbols are
  classified `SYMBOL_INACTIVE`, never as a connection failure.
- **No silent overwrite**: a normal re-run is idempotent; `--force-rebuild` inserts a
  new version and keeps prior versions auditable; a changed bar without force is
  stored inactive so the original active row is never overwritten.

## Freshness integration (the unblock)

The ERS `HistoricalSelector` overlays FINAL Rubix bars **newer than Yahoo's last
row and no newer than the latest completed session** (never today's forming bar,
never overwriting a Yahoo row). Measured:

- **During the 07-22 session (expected 07-21):** overlay adds only 07-21 → 171
  symbols current (was stale under Yahoo-only).
- **07-23 09:45 pre-session (expected 07-22):** overlay adds 07-21 + 07-22 → **185
  of 265 symbols current from local Rubix, with no Yahoo overnight update** (189 use
  the overlay). Remaining stale symbols are honestly those Rubix could not finalize.

## Reports

`reports/daily_bridge/`: `rubix_daily_bars.csv`, `rubix_daily_quality.csv`,
`provider_reconciliation.csv`, `session_finalization_summary.csv`,
`symbol_history_freshness.csv`, plus per-session `YYYY-MM-DD/` (raw / normalized /
rejected bars, reconciliation, `session_summary.json`, `validation_log.txt`).

---

## Final questions

1. **Can Rubix produce a valid latest completed daily bar?** **Yes** — 216 of 265
   validated FINAL bars for 2026-07-22 with correct OHLC, official close and volume.
2. **How many 2026-07-22 symbols are complete?** **216** (209 full complete + 7
   continuous-complete/auction-missing); 27 naturally inactive; 22 partial/gap.
3. **Does Rubix Volume mean traded shares?** **Yes** — cumulative traded shares,
   monotonically increasing from 0; the daily total is the cumulative max (verified
   on COMI/ARAB/ABUK: monotonic, 0 resets).
4. **Does Rubix official Close match AuctionLast?** **Yes** — official close = the
   auction result when present, and it can differ from the continuous close (COMI
   139.90 vs 139.95; ARAB 0.232 vs 0.234). Both are stored separately.
5. **Are auction and continuous values separated correctly?** **Yes** — continuous
   OHLC + `continuous_close` + `continuous_volume` are distinct from `auction_last`
   + `auction_volume`; auction prices are excluded from the continuous High/Low.
6. **Which historical external provider is preferred?** **EODHD** as the primary
   backfill/corporate-action candidate (Twelve Data secondary), **not activated** —
   it stays disabled until the Phase 6 audit (coverage, OHLC accuracy, volume
   semantics, official-close vs auction, adjusted/unadjusted, depth, delay, cost) is
   completed and approved. No subscription/purchase was made.
7. **Can Yahoo be reduced to emergency fallback?** **Yes** — the provider chain ranks
   Rubix-FINAL latest sessions first, external historical second, the normalized
   cache third, and Yahoo legacy last (tier 4, emergency only). Yahoo is no longer
   the only path to the latest completed bar and is not treated as ground truth.
8. **Will the next 09:45 snapshot avoid false HISTORY_STALE?** **Yes** — after
   finalizing 07-22, the 07-23 pre-session snapshot sees 07-22 as current for 185
   symbols purely from local Rubix, independent of Yahoo. Symbols Rubix could not
   finalize remain honestly flagged (per-symbol freshness recorded).
9. **Did any strategy or production behavior change?** **No** — only the isolated
   `core/daily_bridge/` package, the ERS historical accessor's freshness overlay, new
   scripts, reports and tests changed. Expected-range formulas, weights, TP/SL,
   scenario logic and all frozen strategies (Swing/Daily, Classic, Breakout,
   Adaptive) are untouched; production stays disabled. 375 tests pass.
