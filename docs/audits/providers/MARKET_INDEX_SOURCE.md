# Where `^CASE30` comes from

*2026-09-12*

`strategy/market_analyzer.py` blocks new buys while EGX30 is below both its
EMAs, and `config/settings.json` has `require_market_analyzer: true`. Until this
change nothing in the project served `^CASE30`: the Yahoo backtest cache held
one bar of it, EODHD does not carry it, and the local-seed path has no index.
The gate took its fail-open branch on every bar of every run. See
`docs/audits/strategies/DAILY_STRATEGY_DIAGNOSIS.md` section 7 for the diagnosis
and for what turning it on measured.

## The source

MubasherTrade PRO's `History/CASE/history.db` holds the index as table `_EGX30`.

| | |
|---|---|
| Sessions | 3,147 |
| Span | 2013-09-22 to 2026-09-07 |
| Duplicate sessions | 0 |
| Non-positive or unparseable close / high / low | 0 |
| Non-positive volume / turnover | 0 |

There is no export file for it -- the terminal's Export History dialog exports
instruments the user types in, and the index was never among them -- so unlike
the 230 equities in the frozen record it cannot be verified against a second
copy of itself. It is taken from `history.db` under the same condition as the
three ISIN-resolved symbols already there: only when `history.db` ends on the
export's own last session, so the whole record is one record of one day.

The minute store holds `_EGX30ETF`, which is a fund that tracks the index and
has its own price and volume. It is not the index and is not read.

## Where it is served from

**Backtests** read `data/frozen_mubasher/index/EGX30.csv`, hashed in the
manifest under the key `^CASE30` like every other file in that record. It is
served whole, not trimmed to `history_period`: the period bounds the stock being
tested, not how much of the market's past was knowable on a given date, and
trimming it to ten years would leave the filter without an EMA200 for the first
two hundred sessions of every run. `market_analyzer` slices the index by date,
so nothing later than the bar being judged is visible.

**Live** reads the `market_index` table of `data/measured_turnover.db`, which
`scripts/import_mubasher_local.py` rewrites whole on every run. The index is
kept out of the measured-turnover table itself, because sector share is a ratio
of one symbol's turnover to the market's and an index row there would be counted
as a company.

It is served from Mubasher whichever way `live_history_source` is set. EODHD has
no `^CASE30` to disagree with, so there is nothing to shadow.

## How stale the index may be

The index is daily only, so it is exactly as fresh as the terminal's last
history download -- normally a session or three behind the equities beside it,
which do get a minute-store tail. `core/mubasher_live_history.py` refuses it
past `INDEX_LAG_LIMIT_SESSIONS` (20) and reports the lag in the provenance
either way.

That bound is a judgement, and these are the numbers behind it. Measured on
2,947 EGX30 sessions from 2014-07-21, how often an index *n* sessions old gives
the same block/allow decision as the current one:

| index age | same decision |
|---|---|
| 1 session | 97.8% |
| 3 sessions | 95.1% |
| 5 sessions | 92.8% |
| 10 sessions | 88.4% |
| 20 sessions | 84.1% |
| 30 sessions | 81.3% |
| no index at all | 78.5% |

The gate blocks on 21.5% of sessions, which is why failing open agrees with the
current index on only 78.5%. A stale index beats no index at every age measured
-- the limit is where that margin stops being worth claiming, not where it
disappears.
