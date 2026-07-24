# Rubix Completed-Daily-Candle Bridge — Implementation & Findings Report

**Status:** ✅ Built, tested, and running in **shadow mode**.
**Production routing:** ❌ **Not activated. Not recommended yet.** Left disabled
pending explicit user approval, exactly as required.
**Date:** 2026-07-20 · **Universe:** 265 EGX symbols · **Cairo = UTC+3 (DST).**

The bridge is a **standalone, additive data layer**. It is **not wired into
`core/data_provider`'s production routing** — `_load_swing_daily_history` is
byte-for-byte unchanged. Swing/Daily still uses Yahoo history + a display-only
Rubix quote overlay. Everything below was produced by a separate shadow runner.

---

## What was built

| Phase | Component | File |
|---|---|---|
| A | Data-capability audit (the gate) | [`RUBIX_DAILY_CAPABILITY_AUDIT.md`](RUBIX_DAILY_CAPABILITY_AUDIT.md) |
| B | Cairo completed-session detection | `core/egx_session.py` (additive) |
| C | Safe aggregation + strict validation | `providers/rubix_daily_aggregator.py` |
| D | History merge (append-only, never overwrite) | `providers/rubix_completed_daily_bridge.py` |
| E | Per-candle provenance (app-owned DB) | `providers/rubix_bridge_provenance.py` |
| F | Shadow config + comparison report | `config/*`, `services/rubix_daily_shadow.py` |
| G | Yahoo/Rubix reconciliation report | `services/rubix_daily_shadow.py` |
| H | Dashboard disclosure (display-only) | `dashboard/home.py` |
| I | Fail-safe behavior + tests | `tests/test_rubix_completed_daily_bridge.py` |

**Config** (`config/settings.json → rubix_daily_bridge`, all conservative/off):
`enabled: false`, `shadow_mode: true`, `close_safety_minutes: 15`,
`minimum_coverage_ratio: 0.90`, `minimum_volume_reliability: 0.90`.

**Reports produced this run:**
- `reports/rubix_daily_shadow_comparison.csv` (265 rows)
- `reports/rubix_yahoo_daily_reconciliation.csv` (605 overlapping sessions)

---

## Validation results

- **New bridge acceptance suite:** `tests/test_rubix_completed_daily_bridge.py`
  — **16 passed** (completion/weekend/holiday/DST, forming-session exclusion,
  Low=0 rejection, malformed volume, low coverage, low volume-reliability,
  missing symbol, disconnected DB, **read-only immutability**, append-only,
  never-overwrite-Yahoo, frame-identity ⇒ decision equivalence, reconcile
  without mutation).
- **Full existing suite:** **183 passed, 0 failed** — Classic, Breakout,
  Adaptive, walk-forward, providers, portfolio determinism, replay all green.
- **Frozen-strategy equivalence:** shadow over 265 symbols → **Indicator
  Differences = none for every symbol; Decision Changed = No for every symbol.**
- **Rubix DB immutability:** verified by test (row counts unchanged) and by
  design (`sqlite … mode=ro`, no writes anywhere in the new code).

---

## The 10 questions, answered

**1. Does Rubix contain enough 1-minute data to build reliable daily candles?**
No. Median coverage is **≤ 100 of 270 session minutes**; 52/265 symbols captured
< 60 bars on the fullest session. On today's data the **90% coverage gate
rejects 100% of sessions** (COMI's best session reached only 28.9%).

**2. Are Rubix Volume values usable, and what do they represent?**
Two different things:
- `candles_1m.volume` (per-minute, quote-sampled) — **not usable** for a daily
  total; it captures a median **46%** (range 10–75%) of the day.
- `quotes.volume` (**cumulative** session total) — **usable and accurate**. Its
  per-day MAX reconciles with Yahoo's official daily volume: **median 0.000%
  difference, 99.7% of overlapping sessions within ±1%.** The bridge uses this
  cumulative value as the daily volume, never the candle sum.

**3. How many symbols have valid completed Rubix candles?**
**0 of 265** under the conservative gates. Every session is rejected
(`RUBIX_REJECTED`) for coverage < 90% (and/or candle-volume reliability < 90%).

**4. Latest completed Rubix session?**
2026-07-20 is closed and evaluated as completed (Cairo close + 15-min safety
passed), but it is **rejected** on coverage (8.1% for COMI). Last session with
non-trivial capture: 2026-07-19 (still rejected).

**5. How many Yahoo-missing sessions can safely be appended?**
**0 today.** With the gates enforced, no completed Rubix session currently
qualifies to append onto Yahoo history.

**6. How many candles were rejected and why?**
All of them. Dominant reason: **coverage below 90%** (sparse 1-minute capture);
secondary: **volume-reliability below 90%** (candle-sum ≪ cumulative). The audit
also found **~30% zero/negative-OHLC rows** on the two fullest sessions, which
are dropped before aggregation and never repaired.

**7. How many signals change in Shadow Mode?**
**0.** Sessions appended = 0 ⇒ merged history is identical to Yahoo-only ⇒
Signal/Score/RR unchanged for all 265 symbols; Decision Changed = No everywhere.

**8. How closely do Rubix candles match Yahoo after reconciliation?**
Across **605 overlapping sessions** (206 symbols, 2026-07-14/15/16):

| Field | Median \|diff\| | Within ±1% | Notes |
|---|---:|---:|---|
| **Close** | **0.000%** | 95.7% | official close, essentially exact |
| **Volume** | **0.000%** | 99.7% | cumulative quote = official EOD volume |
| Open | 0.989% | 50.1% | sparse capture misses the opening print |
| High | 0.819% | 56.0% | intraday high **understated** |
| Low | 0.572% | 64.5% | intraday low **understated** |

Fat tails exist (a few corrupt symbols reach ~900% — extra evidence the strict
validator is necessary). **Takeaway:** Close and Volume reconcile; **Open/High/Low
do not**, because the 1-minute feed is too sparse to capture the true extremes.

**9. Did any strategy, indicator, or threshold change?**
**No.** No trading formula, gate, or threshold was touched. Additions are: a
new isolated data layer, new config (defaulted off), disclosure text, and tests.
`core/data_provider` routing is unchanged.

**10. Is production activation recommended?**
**No.** Volume and Close are trustworthy, but **Open/High/Low are not** at the
current capture density, and coverage rejects every session anyway. The engine
consumes full OHLCV (ranges, ATR, volume ratios), so partial trust is not enough.

---

## Recommendation

Keep the bridge **disabled / shadow-only**. Re-evaluate for production **only
after** the adapter provides one of:

1. **Official exchange daily candles** (true OHLCV time-and-sales), or
2. A **dense, validated 1-minute feed** whose per-session coverage clears the
   90% gate *and* whose summed volume reconciles with the cumulative quote total.

When that lands, re-run the audit and shadow/reconciliation reports; if
Open/High/Low reconcile to Yahoo within tolerance across multiple completed
sessions, activation can be reconsidered — as a separate, explicit, user-approved
change that wires the bridge into `core/data_provider`. Until then it remains a
diagnostic layer with zero production impact.

## Fail-safe behavior (Phase I) — enforced in code

A missing / incomplete / malformed / zero-valued / stale / duplicated /
out-of-session / under-covered Rubix candle is **rejected**; Yahoo history is
retained; the exact reason is recorded (provenance + shadow report). The symbol
is **never removed** from the scanner and a data failure is **never** classified
as `AVOID` — rejection yields `RUBIX_REJECTED` / `CURRENT_SESSION_EXCLUDED`
labels on the data layer, entirely separate from any trading signal.
