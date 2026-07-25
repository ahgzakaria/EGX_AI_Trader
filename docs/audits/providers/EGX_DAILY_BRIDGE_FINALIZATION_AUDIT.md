# EGX Daily Bridge — Finalization Semantics & Session-Boundary Audit

**The bridge no longer conflates a continuous-session bar with an official daily
bar.** A bar is now `FINAL_OFFICIAL` only when a genuine randomized closing auction
is verified (by a real volume increment); a continuous-complete bar with no
confirmed auction is `FINAL_CONTINUOUS` / `OFFICIAL_CLOSE_UNCONFIRMED` and never
gets a silent `continuous_close`-as-official fallback. No strategy, expected-range
formula, weight, TP/SL or scenario logic changed; production stays disabled;
Swing/Daily never consumes Rubix rows.

| Flag | Value |
|---|---|
| `production_enabled` | **false** |
| `automatic_execution` / `broker_orders_enabled` | **false** |
| TP / SL | +2% / -2% (unchanged) |
| External providers | **disabled** (unaudited) |

## Separated bar semantics (Phase 1)

Each row now carries both concepts explicitly: `continuous_bar_status`
(`FINAL_CONTINUOUS` / `PARTIAL_SESSION` / `INVALID`), `auction_status`
(`AUCTION_CONFIRMED` / `AUCTION_MISSING` / `POST_AUCTION_REPEAT` /
`AUCTION_INCOMPLETE`), `official_bar_status` (`FINAL_OFFICIAL` /
`OFFICIAL_CLOSE_UNCONFIRMED` / `INVALID`), `official_close_confirmed`,
`expected_range_eligible`, `swing_daily_eligible`. The continuous OHLC + close +
volume are distinct from `auction_last` + `auction_volume`; `official_close` is set
**only** when the auction is confirmed.

## Auction confirmation (Phase 5) — real 2026-07-22 test set

All eight liquid test symbols are `AUCTION_CONFIRMED` by a genuine auction volume
increment (the official close = the final auction Last after real material
progression, never a repeated snapshot):

| Symbol | continuous_close | official_close (auction) | auction vol increment |
|---|--:|--:|--:|
| COMI | 139.95 | **139.90** | +58,153 |
| SWDY | 93.00 | **92.50** | +19,134 |
| TMGH | 100.00 | **100.20** | +126,435 |
| FWRY | 19.20 | **19.15** | +363,753 |
| ADIB | 49.00 | **49.00** | +10,186 |
| ABUK | 72.38 | **72.38** | +42,342 |
| ARAB | 0.234 | **0.232** | +38,548,555 |
| PRDC | 9.58 | **9.58** | +552,243 |

ADIB/ABUK/PRDC cleared at the continuous price but still traded genuine auction
volume — confirmed. Volume-increment (not price-move) is the reliable signal.

## Session-boundary trace (Phase 3/4)

`reports/daily_bridge/session_boundary_trace.csv` — 14,593 events across the
09:58–10:02, 14:13–14:17 and 14:23–14:28 windows for 07-21 + 07-22, each attributed
with confidence using received_at + market_timestamp (only when advancing/plausible)
+ cumulative-volume progression + duplicate detection:

| Attribution | Events |
|---|--:|
| CONTINUOUS_CONFIRMED | 6,708 |
| AUCTION_CONFIRMED | 821 |
| POST_AUCTION_REPEAT | 7,063 |
| LATE_FRAME_AMBIGUOUS | **0** |
| TIMESTAMP_UNRELIABLE | **0** |
| UNKNOWN | **0** |

Post-auction repeated snapshots are the dominant boundary artifact and are
correctly identified as repeats (not new auction values). Ambiguous frames are
quarantined at ≤0.5 confidence and never alter official OHLC.

## Multi-session finalization (Phase 8) — `multi_session_finalization.csv`

| Session | FINAL_CONTINUOUS | FINAL_OFFICIAL | AUCTION_MISSING | ER-eligible | Swing-eligible | official-confirm rate |
|---|--:|--:|--:|--:|--:|--:|
| 2026-07-21 | 197 | 193 | 49 | 197 | 0 | 97.97% |
| 2026-07-22 | 216 | 209 | 48 | 216 | 0 | 96.76% |

Two fully-collected sessions — **not yet the ≥3 required to approve official daily
bars**; official bars remain research-grade pending more sessions + external
reconciliation.

## Freshness overlay (Phase 6/7)

ERS consumes CONTINUOUS-session history: the Stage A overlay appends only
`expected_range_eligible` (continuous-final) Rubix bars, using `continuous_close`
(never a silent official close), labelled **`RUBIX_CONTINUOUS_DERIVED`** with
per-session official-close confirmation disclosed (07-23 pre-session: 189 symbols
overlaid, 348 sessions official-confirmed, 10 auction-unconfirmed). Swing/Daily is
gated `swing_daily_eligible=false` for every Rubix row until official OHLC semantics,
corporate actions and external reconciliation pass.

---

## Final questions

1. **Were auction-missing bars incorrectly labelled FINAL?** **Yes** — 7 bars were
   previously `finalization_status=FINAL` with `official_close` silently set to the
   continuous close. Fixed.
2. **How were the 7 bars reclassified?** APSW, FAIT, MIPH, MOIL, NEDA, SDTI, UPMS
   (07-22) → `continuous_bar_status=FINAL_CONTINUOUS`, `auction_status=AUCTION_MISSING`
   ("no auction events"), `official_bar_status=OFFICIAL_CLOSE_UNCONFIRMED`,
   `official_close=None` (no fallback), `expected_range_eligible=true`,
   `swing_daily_eligible=false`.
3. **Can received_at alone misclassify boundary events?** **In principle yes** (a
   pre-14:15 state delivered at 14:16 would look like an auction frame), which is why
   the policy also requires material value/volume progression. With that combined
   evidence, **0 boundary events were ambiguous** on real data.
4. **Were any real 2026-07-22 events assigned to the wrong phase?** **No** — 0
   `LATE_FRAME_AMBIGUOUS`, 0 `TIMESTAMP_UNRELIABLE`; the 7,063 post-14:15 repeats were
   correctly classified as repeats, not new auction prints.
5. **Does auction_last reliably represent the official close?** **Yes, for confirmed
   auctions** — verified by a genuine volume increment (all 8 liquid test symbols).
   Where no auction volume/print exists, the official close is left **unconfirmed**
   rather than guessed.
6. **How many bars are FINAL_CONTINUOUS?** 216 (07-22), 197 (07-21).
7. **How many are FINAL_OFFICIAL?** 209 (07-22), 193 (07-21).
8. **How many are Expected Range eligible?** 216 (07-22), 197 (07-21) — the
   continuous-final set.
9. **How many are Swing/Daily eligible?** **0** — Rubix rows are gated out of
   Swing/Daily until official OHLC + corporate actions + external reconciliation pass.
10. **Did any strategy or parameter change?** **No** — only `core/daily_bridge/`
    (schema, builder, boundary policy), the ERS continuous-derived overlay labelling,
    new scripts, reports and tests changed. TP/SL, weights, percentiles, scenario
    logic and all frozen strategies are untouched; production disabled; no external
    provider activated. 379 tests pass.
