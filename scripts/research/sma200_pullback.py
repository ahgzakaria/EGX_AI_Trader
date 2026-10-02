r"""Which liquid names have pulled back to test their 200-day average -- and what such tests did.

    venv\Scripts\python.exe scripts\research\sma200_pullback.py
    venv\Scripts\python.exe scripts\research\sma200_pullback.py --since 2014-01-01

The idea being tested: a stock in a long uptrend that falls back to its 200-day
simple average is an opportunity, because the average holds. This lists the names
doing that on the last session, then replays every such test in the measured
record and says what followed.

A test, on one session, is all of:

* the 200-day average is rising (above where it was 20 sessions earlier);
* the name came from above: within the previous 60 sessions it closed at least
  10% over the average;
* it is at the average now: the low reached within 2% above it and the close is
  no more than 3% below it;
* liquid: at least 5M EGP a session over the last 20 (Swing Breakout's floor),
  a session without a trade counted as zero;
* no move past EGX's +/-20% limit in the last 200 sessions -- that is an
  unadjusted split or bonus issue, and it distorts the average itself.

Only the first session of a test counts; a name sitting at its average for a
week is one test, not five.

What followed is measured against the median liquid stock over the same
sessions (``core.measured_benchmark``'s definition of the market a holder
faces), so a test that rose only because everything rose earns nothing. Also
recorded: whether the close reached 5% above the average before it closed 5%
below it, within 20 sessions -- the bounce and the break.

Read-only. Writes nothing.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from t0_radar import sma200 as S                                     # noqa: E402

# The definition of a test is ``t0_radar.sma200``'s, read rather than restated,
# so the page and this replay cannot describe two different tests.
CAME_FROM_ABOVE, ZONE_ABOVE, ZONE_BELOW = S.CAME_FROM_ABOVE, S.ZONE_ABOVE, S.ZONE_BELOW
SELLOFF_SHARE, CROSS_SECTION = S.SELLOFF_SHARE, S.CROSS_SECTION
REARM_SESSIONS = 20
HORIZONS = (5, 10, 20)
BOUNCE, BREAK = 0.05, 0.05


def load():
    from core.universe import company_name

    frames = S.load_frames()
    return frames, {symbol: company_name(symbol) for symbol in frames}


def panel(frames):
    """Per symbol and calendar session: the test's inputs, and what came after."""

    calendar = S.market_calendar(frames)
    closes, parts = {}, []
    for symbol, f in frames.items():
        frame = S.features(f, calendar)
        frame["symbol"] = symbol
        parts.append(frame)
        closes[symbol] = f["Close"].reindex(calendar).ffill(limit=5)
    long = pd.concat(parts).rename_axis("date").reset_index()
    prices = pd.DataFrame(closes)

    long["liquid"] = S.liquid(long)
    long["zone"] = S.in_zone(long)
    long["state"] = S.state(long)
    long["setup"] = long["state"].isin([S.ON_OR_ABOVE, S.BELOW])
    long = long.sort_values(["symbol", "date"])
    previous = (long.groupby("symbol")["setup"]
                .transform(lambda s: s.shift(1).rolling(REARM_SESSIONS, min_periods=1).max())
                .fillna(0))
    long["event"] = long["setup"] & (previous == 0)

    # Forward returns on the calendar, for the names and for the median liquid name.
    for h in HORIZONS:
        forward = (prices.shift(-h) / prices - 1).stack().rename(f"fwd{h}")
        long = long.merge(forward, left_on=["date", "symbol"], right_index=True, how="left")
        bench = (long[long["liquid"]].groupby("date")[f"fwd{h}"]
                 .agg(lambda s: s.median() if s.notna().sum() >= CROSS_SECTION else np.nan))
        long[f"lift{h}"] = long[f"fwd{h}"] - long["date"].map(bench)

    breadth = long[long["liquid"]].groupby("date")["below_sma20"].mean()
    long["selloff"] = long["date"].map(breadth) >= SELLOFF_SHARE
    return calendar, prices, long, breadth


def bounce_or_break(event, prices, calendar):
    """'bounce' if the close reached +5% over the average first, 'break' if -5% first."""

    position = calendar.get_loc(event["date"])
    path = prices[event["symbol"]].iloc[position + 1:position + 1 + 20]
    level = event["sma"]
    for value in path:
        if value >= level * (1 + BOUNCE):
            return "bounce"
        if value <= level * (1 - BREAK):
            return "break"
    return "neither"


def episodes(events, calendar, gap=10):
    """Tests a sell-off produces arrive together; dates more than ``gap`` sessions
    apart start a new episode, and each episode is one independent observation."""

    dates = sorted(events["date"].unique())
    labels, episode, last = {}, 0, None
    for day in dates:
        position = calendar.get_loc(day)
        if last is not None and position - last > gap:
            episode += 1
        labels[day], last = episode, position
    return events["date"].map(labels)


def summarize(events, label, calendar):
    events = events[events["lift20"].notna()]      # early sessions had too few names to compare
    row = {"group": label, "tests": len(events)}
    for h in (10, 20):
        row[f"lift {h}d median"] = events[f"lift{h}"].median() * 100
    row["lift 20d mean"] = events["lift20"].mean() * 100
    row["return 20d median"] = events["fwd20"].median() * 100
    row["beat market 20d"] = (events["lift20"] > 0).mean() * 100
    outcome = events["outcome"].value_counts(normalize=True)
    row["bounce"], row["break"] = outcome.get("bounce", 0.0) * 100, outcome.get("break", 0.0) * 100
    if len(events):
        by_episode = events.groupby(episodes(events, calendar))["lift20"].median()
        row["episodes"] = len(by_episode)
        row["episodes beat"] = int((by_episode > 0).sum())
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", default="2014-01-01")
    args = parser.parse_args(argv)
    pd.set_option("display.width", 220)
    pd.set_option("display.float_format", lambda v: f"{v:,.2f}")

    frames, names = load()
    calendar, prices, long, breadth = panel(frames)
    latest = calendar[-1]
    print(f"{len(frames)} symbols, record through {latest.date()}; "
          f"{breadth.iloc[-1]:.0%} of liquid names below their SMA20 on the last session")

    # --- the history -----------------------------------------------------------
    events = long[long["event"] & (long["date"] >= args.since)
                  & long["fwd20"].notna()].copy()
    events["outcome"] = [bounce_or_break(e, prices, calendar) for _, e in events.iterrows()]
    events["year"] = events["date"].dt.year
    above, selloff = events["dist"] >= 0, events["selloff"]
    groups = [
        (f"every test since {args.since[:4]}", events.index == events.index),
        ("2014-2017", events["year"].between(2014, 2017)),
        ("2018-2022", events["year"].between(2018, 2022)),
        ("2023-2026", events["year"].between(2023, 2026)),
        ("closed on or above the average", above),
        ("closed below the average", ~above),
        (f"broad sell-off (>= {SELLOFF_SHARE:.0%} below SMA20)", selloff),
        ("broad sell-off, closed on or above", selloff & above),
        ("broad sell-off, closed below", selloff & ~above),
        ("broad sell-off, on or above, 2014-2019", selloff & above & (events["year"] <= 2019)),
        ("broad sell-off, on or above, 2020-2026", selloff & above & (events["year"] >= 2020)),
        ("normal market, closed on or above", ~selloff & above),
    ]
    table = pd.DataFrame([summarize(events[mask], label, calendar) for label, mask in groups])
    print("\nWHAT A TEST OF THE 200-DAY AVERAGE DID NEXT -- lift is % over the median liquid "
          "stock over the same sessions; return is the stock's own; bounce/break: +5% / -5% "
          "from the average first, within 20 sessions; an episode is a run of tests no more "
          "than 10 sessions apart")
    print(table.to_string(index=False))

    # --- the last session ---------------------------------------------------------
    now = long[(long["date"] == latest)].copy()
    now["name"] = now["symbol"].map(names)
    columns = ["symbol", "name", "close", "sma", "dist", "low_dist", "peak", "slope", "rsi",
               "turnover"]

    def show(frame):
        out = frame[columns].copy()
        out["dist"], out["low_dist"], out["peak"], out["slope"] = (
            out["dist"] * 100, out["low_dist"] * 100, out["peak"] * 100, out["slope"] * 100)
        out["turnover"] = out["turnover"] / 1e6
        out["name"] = out["name"].str.slice(0, 32)
        return out.rename(columns={
            "sma": "SMA200", "dist": "close vs SMA200 %", "low_dist": "low vs SMA200 %",
            "peak": "was above, max %", "slope": "SMA200 20d slope %", "rsi": "RSI14",
            "turnover": "turnover 20d (M)"}).sort_values("close vs SMA200 %")

    market = "a broad sell-off" if breadth.iloc[-1] >= SELLOFF_SHARE else "a normal market"
    for wanted, label in (
            (S.ON_OR_ABOVE, f"TESTING ON {latest.date()} in {market} -- closed ON OR ABOVE it"),
            (S.BELOW, f"TESTING ON {latest.date()} in {market} -- closed BELOW it"),
            (S.APPROACHING, "APPROACHING: 2-6% above a rising average, came from 10%+ above"),
            (S.BROKE, "ALREADY THROUGH IT: 3-10% below a rising average they were 10%+ above")):
        part = now[now["state"] == wanted]
        print(f"\n{label} ({len(part)}):")
        print(show(part).to_string(index=False) if len(part) else "  none")


if __name__ == "__main__":
    main()
