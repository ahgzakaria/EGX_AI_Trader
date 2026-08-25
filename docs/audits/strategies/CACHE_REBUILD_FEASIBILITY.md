# Can the Daily Cache Be Rebuilt?

**Question:** the `open` field in `data/market_data_cache.sqlite` is fabricated.
The agreed remedy was "rebuild the cache, don't repair it." This checks whether
that rebuild is actually possible before anything is overwritten.

**Status:** read-only investigation. **Nothing was rebuilt and nothing was
overwritten.** The cache is untouched.

**Short answer: no.** Every source that covers the history is already fabricated
at origin, and the only clean source covers 16 sessions. A rebuild would
reproduce the defect or destroy ten years of history to gain three weeks.

---

## 1. Every historical source on disk is already fabricated

| Source | Rows | `open` == previous `close` | `open` outside `[low, high]` |
|---|--:|--:|--:|
| `data/yahoo_cache/*.csv` (198 files) | 48,436 | **98.4%** | **15.7%** |
| `data/frozen_yahoo_seed/*.csv` (35 files) | 68,337 | **97.7%** | **23.2%** |
| `data/frozen_eodhd_seed/*.csv` (241 files) | 588,528 | **72.5%** | 0.0% |
| **Rebuilt from traded minutes** | 3,153 bars | **12.9%** | **0.0%** |

The bottom row is the control, and it is what an honest EGX daily bar looks like:
about one open in eight genuinely equals the prior close, and none falls outside
its own bar.

The decisive line is the first. **The raw Yahoo CSVs — provider output, sitting on
disk before any local ingestion — are already 98.4% carried forward and 15.7%
impossible.** The corruption is upstream, in the data as received. Rebuilding the
sqlite cache from those files reproduces it exactly.

EODHD is not an escape route. Its carry-forward rate is 72.5%, merely clipped
into range so the impossible-bar test cannot see it. It is fabricated too, just
more tidily.

## 2. There is only one daily source, not two

The attribution argument in `EOD_OPEN_FIELD_DEFECT_AUDIT.md` reasons that two
unrelated providers showing the identical signature implies local corruption,
because vendors do not independently produce the same defect. The premise is
false: **they are not two sources.**

The cache holds 11,541 `provider='rubix'` daily rows, spanning 2016-07-14 to
2026-07-13, for exactly five symbols. Joined against the `yahoo` rows for the
same symbol and date:

| Symbol | Overlapping rows | Identical OHLC |
|---|--:|--:|
| COMI.CA | 2,465 | 2,464 (100.0%) |
| EAST.CA | 2,461 | 2,460 (100.0%) |
| FWRY.CA | 1,681 | 1,680 (99.9%) |
| SWDY.CA | 2,461 | 2,460 (100.0%) |
| TMGH.CA | 2,461 | 2,460 (100.0%) |

They are the same bars. And they cannot be otherwise: `rubix_live_market.db`
begins at **2026-07-01**, so ten years of "rubix" daily rows were never
aggregated from minutes at all. They are Yahoo rows carrying a `rubix` label.

`providers/rubix_daily_aggregator.py:233` is therefore correct *and* irrelevant to
these rows — it never ran on them. There is no post-aggregation shift to hunt
for, because there was no aggregation. The signature is identical because the
data is identical.

## 3. The one clean source is three weeks long

Rebuilding daily bars from `candles_1m` the way the aggregator does — open from
the first traded minute — produces **3,153 bars over 208 symbols**, with a 12.9%
carry-forward rate and **zero** impossible bars. It is genuinely clean.

It spans **2026-08-02 to 2026-08-25**. Sixteen sessions.

The strategy requires `min_bars: 250` of daily history. Rebuilding the cache from
this source would leave every symbol below the threshold and delete the ten-year
record the entire backtest rests on.

## 4. What follows

- **Do not rebuild the cache.** It cannot be made correct from anything currently
  on disk, and the attempt would be destructive.
- **Keep the existing cache.** `open` is the column with proof against it, and
  the swing backtest's entries and exits never read it.

  This should not harden into "close, high and low are sound." A parallel check
  against minute-derived truth (n=161, contaminated by a 10:1 scale artifact on
  ORAS.CA and a stale cohort near the cache cutoff) puts `close` at a median
  offset of exactly **0.000%** with 29.2% of rows differing by more than 0.1% —
  that half holds. But `high` (+0.270%, 62.7%) and `low` (-0.200%, 57.8%) carry
  small non-zero median offsets. The sample is too small and too contaminated to
  resolve whether that is real. It does not change the priority — `open` is the
  one with impossible bars — but the soundness of `high` and `low` is a caveat
  awaiting a larger overlap, not an established finding.
- **The honest framing is a missing column, not a corrupt cache.** EGX daily
  opens are not available from any source this project currently has. Consumers
  should be made to fail loudly on a missing open rather than silently consume a
  carried-forward one.
- **The forward path already exists and is already the project's stated policy** —
  `frozen_yahoo_seed/_manifest.json` says "extend only via Rubix bridge." The
  minute-derived open is correct. It simply needs time to accumulate history, and
  it starts from 2026-08-02.
- **A real historical open needs a new source.** That is a procurement question,
  not an engineering one.

## Limits

- This establishes that no source on disk carries real historical opens. It does
  not establish that no obtainable source does.
- The 12.9% control rate comes from 16 sessions and 208 symbols. It is a
  plausibility check on the other rates, not a precise population figure.
- Nothing here is a recommendation to trade.

## Reproduce

The source comparison and the minute-derived control are both short read-only
queries; the figures above come from `data/yahoo_cache`, `data/frozen_yahoo_seed`,
`data/frozen_eodhd_seed`, `data/market_data_cache.sqlite` and
`data/rubix_live_market.db`.
