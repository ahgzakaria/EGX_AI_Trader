r"""Is buying the dip in a strong name better than buying its breakout?

The breakout strategy buys strength at its highest recent price. This asks the
opposite question at the same horizon: in a name that is already trending, does
waiting for a pullback pay better than paying up?

It is the only kind of new strategy worth testing here, because the horizon is
not a free choice. Measured over 112,000 observations in ``holding_period.py``,
the round trip is 1,030% of the average intraday move on EGX, 292% at three
days, and only at ten days does the average move first exceed it. Every
intraday idea dies on that arithmetic before any signal quality is discussed --
which is what the ORB outcome measurement then confirmed on live signals.

So both sides here hold for twenty sessions, and the comparison is against the
same benchmark the breakout work used: an equal-weight position in the whole
universe on the same days. Beating zero is not the test; beating what you would
have got by owning everything is.

The entries all require the same regime -- price above its 200-day average and
12-1 momentum in the top third -- and differ only in *when* they buy:

* **breakout**: a close above the 20-day high on 2.5x volume (the shipped rule)
* **pullback to the 10-day low**: the trend is intact, the price is at a
  short-term low
* **pullback to the 20-day average**: a deeper give-back
* **N red days**: a mechanical dip, no level involved

Nothing here is fitted. Each variant is reported for both eras, and a variant
that only works in one is that era, not a strategy.

    venv\Scripts\python.exe scripts\research\pullback_entry.py
"""

from __future__ import annotations

from pathlib import Path
import statistics
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from scripts.research.breakout_filters import COST, HOLD, SPLIT, build, load


def add_pullback_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Per-symbol dip measures, all computed only from data already available.

    Every rolling window is shifted so the current bar never contributes to the
    level it is being compared against -- the same rule that keeps the breakout
    from breaking out of itself.
    """
    frames = []
    for _, frame in panel.groupby("Symbol", sort=False):
        frame = frame.sort_values("Date").copy()
        close = frame["Close"]
        frame["low_10"] = close.rolling(10).min().shift(1)
        frame["mean_20"] = close.rolling(20).mean().shift(1)
        frame["at_10_day_low"] = close <= frame["low_10"]
        frame["below_20_day_mean"] = close < frame["mean_20"]
        down = close < close.shift(1)
        frame["red_2"] = down & down.shift(1).fillna(False)
        frame["red_3"] = frame["red_2"] & down.shift(2).fillna(False)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def in_uptrend(data: pd.DataFrame) -> pd.Series:
    """The regime both sides share, so the comparison is about entry timing."""
    return data["above_200"] & (data["momentum_rank"] >= 0.67)


ENTRIES = {
    "breakout on 2.5x volume (shipped)":
        lambda d: in_uptrend(d) & d["breakout"] & (d["volume_ratio"] >= 2.5),
    "pullback to the 10-day low":
        lambda d: in_uptrend(d) & d["at_10_day_low"],
    "pullback below the 20-day mean":
        lambda d: in_uptrend(d) & d["below_20_day_mean"],
    "two red days":
        lambda d: in_uptrend(d) & d["red_2"],
    "three red days":
        lambda d: in_uptrend(d) & d["red_3"],
    "10-day low AND 2.5x volume":
        lambda d: in_uptrend(d) & d["at_10_day_low"] & (d["volume_ratio"] >= 2.5),
}


def describe(values, benchmark, label: str) -> str:
    if len(values) < 20:
        return f"  {label:<36} {len(values):>5} trades - too few to read"
    mean = statistics.fmean(values)
    wins = sum(1 for v in values if v > 0) / len(values) * 100
    lift = mean - benchmark
    return (f"  {label:<36} {mean:+6.2f}%  lift {lift:+6.2f}%  "
            f"win {wins:4.1f}%  n={len(values):>5}")


def main() -> int:
    panel = add_pullback_features(build(load()))
    print(f"Universe {panel['Symbol'].nunique()} names, "
          f"hold {HOLD} sessions, cost {COST:.2f}% a round trip, "
          f"split at {SPLIT}\n")
    print("Lift is against owning the whole universe on the same days, which is "
          "the only\nhonest benchmark when the validation era is a bull market.\n")

    for era, frame in (("TRAINING (before " + SPLIT + ")", panel[panel["Date"] < SPLIT]),
                       ("VALIDATION (from " + SPLIT + ")", panel[panel["Date"] >= SPLIT])):
        print(era)
        for label, rule in ENTRIES.items():
            selected = frame[rule(frame).fillna(False)]
            if selected.empty:
                print(f"  {label:<36} no trades")
                continue
            # The benchmark is recomputed per entry: it has to be the universe
            # on *those* days, not on all days, or a rule that only fires in
            # good weather is credited with the weather.
            days = frame[frame["Date"].isin(selected["Date"])]
            benchmark = statistics.fmean(days["forward"].tolist())
            print(describe(selected["forward"].tolist(), benchmark, label))
        print()

    print("A variant that leads in one era and not the other is that era. The "
          "breakout row is\nthe standard to beat, not a baseline: it already "
          "survives both.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
