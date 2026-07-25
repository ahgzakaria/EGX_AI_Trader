# Rubix Daily-Candle Capability Audit (Phase A)

**Status:** ❌ **Rubix data is NOT currently safe to aggregate into production Swing/Daily candles.**
**Method:** Read-only inspection of the live adapter database (`sqlite … mode=ro`). No writes, no schema changes, no network access.
**Database:** `D:\EGX_AI_Trader\data\rubix_live_market.db` (185 MB + active WAL)
**Audit date:** 2026-07-20 (Cairo = UTC+3, DST confirmed)

This audit is the gate required before any bridge is enabled. Per the task
brief: *"Do not enable the bridge if the data semantics cannot be validated."*
The findings below fail that bar on **three independent axes** (volume, OHLC
validity, coverage). The bridge is therefore delivered **disabled / shadow-only**.

---

## 1. Database shape

| Table | Rows | Purpose |
|---|---|---|
| `quotes` | 535,714 | Last/bid/ask snapshots + **cumulative** session volume |
| `candles_1m` | 51,485 | Adapter-derived 1-minute OHLCV (sparse, quote-sampled) |
| `feed_metrics` | 1,314,397 | Collector telemetry (latency, heartbeats, disconnects) |
| `sqlite_sequence` | 2 | SQLite internal |

`candles_1m` columns: `ticker, minute, open, high, low, close, volume, updates`.
`minute` timestamps are stored in **UTC** (`+00:00`); EGX trades in Cairo, so
session-date grouping **must** convert UTC → `Africa/Cairo` first.

Distinct tickers: **265** (matches the scanner universe).
`candles_1m` time range: `2026-07-01T00:00Z … 2026-07-20T15:34Z`.
Sessions actually present with data: **2026-07-14, 07-15, 07-16, 07-19, 07-20**
(plus one stray bar on 07-01). Everything before 07-14 is effectively empty —
the collector only began populating usefully ~6 sessions ago.

---

## 2. 🔴 Finding 1 — Volume is quote-derived and undercounts truth by ~2×–10×

This is the decisive blocker, and it is exactly the risk the brief flagged.

- `quotes.volume` is **cumulative** within a session (monotonic non-decreasing).
  For COMI on 07-20 it rose `70,060 → 5,387,806`. `MAX(quotes.volume)` per Cairo
  day is therefore the *true* daily traded volume.
- `candles_1m.volume` is **per-bar incremental** (non-monotonic) — but because
  the adapter only samples quotes intermittently, the captured bars miss most of
  the session. **Summing `candles_1m.volume` recovers only a fraction of the day.**

Comparison across the 20 highest-volume symbols on session **2026-07-19**
(`SUM(candle volume)` vs `MAX(quote cumulative volume)`):

| Symbol | Candle sum | Quote cumulative (truth) | Captured |
|---|---:|---:|---:|
| ARAB | 172,521,476 | 804,072,735 | 21.5% |
| KRDI | 282,997,926 | 378,838,099 | 74.7% |
| SPMD | 81,894,875 | 190,696,261 | 42.9% |
| BTFH | 40,166,290 | 126,966,888 | 31.6% |
| OIH | 6,836,224 | 69,697,001 | **9.8%** |
| … | | | |

**Median captured = 46.4%, mean = 47.8%, range = 9.8%–74.7%.**

The shortfall is **not** a constant that could be scaled away — it varies per
symbol and per session with collector sampling luck. Any volume-dependent
indicator (relative volume / RVOL, `quality_min_volume_ratio`, volume-confirmation
gates) fed from candle-summed Rubix volume would be **systematically wrong and
non-deterministic**.

> The only defensible daily volume Rubix can offer is `MAX(quotes.volume)` per
> Cairo session (the cumulative total), and even that depends on having captured
> the final print of the day. It is a *quote-derived estimate*, not official
> exchange time-and-sales.

---

## 3. 🔴 Finding 2 — ~30% of candle rows have zero/negative OHLC on the good sessions

Zero-or-negative OHLC counts by Cairo session date:

| Cairo date | Bars | Bad (OHLC ≤ 0) | Bad % |
|---|---:|---:|---:|
| 2026-07-20 | 4,919 | 109 | 2.2% |
| 2026-07-19 | 22,843 | 6,812 | **29.8%** |
| 2026-07-16 | 21,714 | 6,610 | **30.4%** |
| 2026-07-15 | 266 | 13 | 4.9% |
| 2026-07-14 | 1,742 | 72 | 4.1% |

On the two sessions with meaningful coverage (07-16, 07-19), **~30% of the source
one-minute rows are invalid** (zero or negative open/high/low/close). These must
be rejected outright — never repaired — which further thins an already sparse
signal and pushes High/Low estimates further from reality.

Internal OHLC ordering (`high ≥ low`, `high ≥ open/close`, `low ≤ open/close`) is
consistent — **0 ordering violations** across all 51,485 rows. So *individual
non-zero bars* are internally coherent; the problem is missing minutes and the
large block of zero-valued rows.

Duplicate `(ticker, minute)` rows: **0** (primary key enforced). Good.

---

## 4. 🔴 Finding 3 — Session coverage is sparse and inconsistent

EGX regular session = 270 minutes (10:00–14:30 Cairo). On **2026-07-19**:

- 265 symbols have bars.
- Bars/symbol: **min 2, median 100, max 124** — i.e. even the best-covered
  symbol captured < half the session's minutes.
- **52 of 265 symbols captured fewer than 60 bars.**
- Per-symbol span: min 10 min, median 320 min, max 406 min.

Session-boundary bleed (Cairo local time of first/last bar):

| Cairo date | Bars | Cairo span |
|---|---:|---|
| 2026-07-20 | 4,919 | 08:30 … 18:34 |
| 2026-07-19 | 22,843 | 08:20 … 16:50 |
| 2026-07-16 | 21,714 | 03:00 … 14:42 |
| 2026-07-14 | 1,742 | 08:20 … 18:42 |

Bars appear well outside 10:00–14:30 (e.g. 03:00 and 18:42 Cairo), so raw rows
mix pre-market/after-hours/stale snapshots with the regular session. Aggregation
**must** clip to the regular session window in Cairo, which removes yet more data.

Whole-session capture is also unreliable: 07-15 has just **266 bars total (≈1 per
symbol)** and 07-01 has **1 bar** — the collector was effectively down. A session
that looks "present" can still be near-empty.

---

## 5. Per-session safety verdict

| Cairo session | Present? | OHLC valid | Volume usable | Coverage | Safe to aggregate for production? |
|---|---|---|---|---|---|
| 2026-07-20 | partial (still-collecting day) | 2.2% bad | ❌ candle-sum ≪ truth | thin (median ~22 bars) | ❌ No |
| 2026-07-19 | yes | ❌ 29.8% bad | ❌ ~46% of truth | median 100/270 | ❌ No |
| 2026-07-16 | yes | ❌ 30.4% bad | ❌ | 03:00 bleed | ❌ No |
| 2026-07-15 | near-empty | 4.9% bad | ❌ | ~1 bar/symbol | ❌ No |
| 2026-07-14 | thin | 4.1% bad | ❌ | 8 bars (COMI) | ❌ No |

**No recent session passes all three gates.** A correctly-implemented strict
validator (positive OHLC, session-window clip, minimum coverage ratio, volume
reliability) rejects **every** current Rubix session for production use.

---

## 6. Answers to the Phase-A questions

1. **Enough 1-minute data for reliable daily candles?** No. Coverage is sparse
   (median ≤ 100 of 270 session minutes) and inconsistent day to day.
2. **Volume semantics?** `candles_1m.volume` is incremental but quote-sampled and
   captures only ~10–75% (median 46%) of true daily volume. `quotes.volume` is
   cumulative and its per-day MAX is the only trustworthy daily total.
3. **OHLC semantics?** Internally consistent per bar (0 ordering violations), but
   ~30% of rows on the fullest sessions are zero/negative and must be rejected;
   High/Low understate true intraday range because minutes are missing.
4. **Timezone?** Stored in UTC. Cairo = UTC+3 in July 2026 (DST active). Session
   grouping requires UTC→Cairo conversion and a 10:00–14:30 clip.
5. **Duplicates / negatives?** No duplicate minutes. No negative volume, no OHLC
   ordering violations — but a large zero-OHLC block (Finding 2).
6. **Safe to enable the bridge?** ❌ **No.** Delivered disabled / shadow-only.

---

## 7. Consequence for the bridge

- The Completed-Daily-Candle Bridge is built with a **strict validator** that
  rejects, on today's data, essentially all Rubix sessions (positive-OHLC,
  session-window, coverage-ratio, and volume-reliability checks).
- It is **not wired into production routing.** `core/data_provider._load_swing_daily_history`
  is unchanged; Swing/Daily continues to use Yahoo history + a display-only Rubix
  quote overlay, exactly as before.
- It runs only in **shadow mode** via a standalone script, producing comparison
  and reconciliation reports for review.
- Production activation is **not recommended** until the adapter emits either
  official exchange daily candles or a dense, validated 1-minute feed whose
  summed volume reconciles with the cumulative quote volume.
