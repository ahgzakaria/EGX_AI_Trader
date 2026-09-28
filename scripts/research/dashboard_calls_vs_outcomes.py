r"""Do the Daily Dashboard's calls predict what the stocks then do? The live record.

    venv\Scripts\python.exe scripts\research\dashboard_calls_vs_outcomes.py

The owner's complaint, 2026-09-28: stocks rise and fall with no relation to what
the dashboard predicted. The backtest research already says its selection has no
out-of-sample edge (DAILY_STRATEGY_DIAGNOSIS.md §1). This asks the same question
of every call the dashboard actually made -- `data/forward_testing.db`, BUY,
WATCH and AVOID with their scores -- rather than of a backtest.

## Fixed before running

* **One call per symbol per session**: the last one recorded that day.
* **The outcome** is the close ``h`` sessions after the call's session, from the
  live Mubasher record, as lift over the median symbol across the same sessions
  (``core.measured_benchmark``). ``h`` is 5 and 10; 10 is primary, and 20 is too
  long for a record that began in July.
* **What a working call looks like**: BUY above WATCH above AVOID, and within
  WATCH a higher score doing better than a lower one. If AVOID does as well as
  BUY, the classification carries nothing.
* **The comparison** is a plain first close above the prior twenty-session high
  on the same sessions and symbols -- the rule the backtest research measured as
  real (CHART_PATTERNS.md, CONFIRMED_VOLUME_BREAKOUT.md).
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np                                                   # noqa: E402
import pandas as pd                                                  # noqa: E402

HORIZONS = (5, 10)
PRIMARY = 10
FIRST_SESSION = "2026-07-01"


def calls(database=PROJECT_ROOT / "data" / "forward_testing.db"):
    with sqlite3.connect(f"file:{Path(database).as_posix()}?mode=ro", uri=True) as db:
        frame = pd.read_sql(
            "SELECT signal_date, ticker, signal_type, score, recorded_at FROM signals "
            "WHERE signal_date >= ?", db, params=(FIRST_SESSION,))
    frame = frame.sort_values("recorded_at").drop_duplicates(
        ["signal_date", "ticker"], keep="last")
    return frame


def closes_for(tickers):
    from core.mubasher_live_history import READY, mubasher_live_history

    out = {}
    for ticker in sorted(set(tickers)):
        frame, status, _ = mubasher_live_history(ticker.split(".")[0], min_bars=30)
        if status == READY and frame is not None and not frame.empty:
            f = frame[["High", "Close"]].astype(float).copy()
            f.index = pd.to_datetime(f.index).normalize()
            out[ticker] = f[~f.index.duplicated(keep="last")].sort_index()
    return out


def forward(frame, session, h):
    day = pd.Timestamp(session).normalize()
    i = frame.index.searchsorted(day, side="right") - 1
    if i < 0 or frame.index[i] != day or i + h >= len(frame):
        return None
    return (frame["Close"].iloc[i + h] / frame["Close"].iloc[i] - 1.0) * 100.0


def fresh_breakout(frame, session):
    day = pd.Timestamp(session).normalize()
    i = frame.index.searchsorted(day, side="right") - 1
    if i < 21 or frame.index[i] != day:
        return False
    level = frame["High"].iloc[i - 20:i].max()
    previous_level = frame["High"].iloc[i - 21:i - 1].max()
    return bool(frame["Close"].iloc[i] > level and frame["Close"].iloc[i - 1] <= previous_level)


def summarize(label, values):
    v = pd.Series(values, dtype=float).dropna()
    if v.empty:
        return f"{label:<30}{'n':>6} 0"
    return (f"{label:<30}{len(v):>6}{v.mean():>+9.2f}{v.median():>+9.2f}"
            f"{(v > 0).mean() * 100:>8.0f}%")


def main() -> int:
    from core.measured_benchmark import close_panel, median_return

    table = calls()
    closes = closes_for(table["ticker"])
    panel = close_panel()
    print(f"{len(table):,} calls on {table['signal_date'].nunique()} sessions, "
          f"{table['ticker'].nunique()} symbols, from {table['signal_date'].min()}")

    for h in HORIZONS:
        lifts = []
        for row in table.itertuples():
            frame = closes.get(row.ticker)
            if frame is None:
                continue
            ret = forward(frame, row.signal_date, h)
            bench = median_return(panel, row.signal_date, h)
            if ret is None or bench is None:
                continue
            lifts.append((row.signal_type, row.score, ret - bench,
                          fresh_breakout(frame, row.signal_date)))
        data = pd.DataFrame(lifts, columns=["call", "score", "lift", "breakout"])
        primary = " (primary)" if h == PRIMARY else ""
        print(f"\n{h} sessions later{primary} -- lift over the median symbol, %")
        print(f"{'group':<30}{'n':>6}{'mean':>9}{'median':>9}{'> mkt':>9}")
        print("-" * 63)
        for call in ("BUY", "WATCH", "AVOID"):
            print(summarize(call, data[data["call"] == call]["lift"]))
        watch = data[data["call"] == "WATCH"].copy()
        if len(watch) >= 50:
            watch["quintile"] = pd.qcut(watch["score"].rank(method="first"), 5,
                                        labels=["lowest", "2", "3", "4", "highest"])
            for q in ["lowest", "2", "3", "4", "highest"]:
                print(summarize(f"  WATCH score {q}", watch[watch["quintile"] == q]["lift"]))
        print(summarize("plain 20-day breakout", data[data["breakout"]]["lift"]))
        print(summarize("everything", data["lift"]))
        if len(data) > 30:
            corr = data[["score", "lift"]].corr(method="spearman").iloc[0, 1]
            print(f"rank correlation of score with lift: {corr:+.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
