r"""The five surviving score components, vectorised, and proved against the real ones.

`SCORE_DIAGNOSIS.md` measured the total score at r = -0.032 and named the
components responsible, but it did so on 638 realised trades. Deciding whether
the score can be *rebuilt* needs the components on every bar the score is asked
to discriminate within -- hundreds of thousands of them -- which the per-bar
functions are far too slow to supply.

So they are reimplemented here in vectorised form, and then **checked against the
originals on a random sample**, because a reimplementation nobody verified is a
second opinion from a witness who did not see anything. `verify()` is the whole
reason this module is separate from the analysis that uses it.

Only the five that still reach the total are here. `candle_score` and
`breakout_score` are computed by the engine and added to nothing.

    venv\Scripts\python.exe scripts\research\score_components.py
"""
from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

#: Each component's reachable maximum, for reading a weight against.
MAXIMUM = {"trend": 30, "volume": 20, "support": 15, "entry": 28, "momentum": 25}
COMPONENTS = tuple(MAXIMUM)


def add_components(panel: pd.DataFrame) -> pd.DataFrame:
    """Per-bar component scores, one column each, plus the inputs they use."""
    frames = []
    for _symbol, frame in panel.groupby("Symbol", sort=False):
        f = frame.sort_values("Date").copy()
        close, high, low, volume = f["Close"], f["High"], f["Low"], f["Volume"]

        # `entry.py` excludes today; `support.py` now does too. Note the
        # window is **nineteen** bars, not twenty: both compute
        # `High[max(0, i - 19) : i]`, which is i-19 through i-1 inclusive.
        # Writing the obvious `rolling(20).max().shift(1)` here produced eight
        # mismatches in 2,000 checks, all of them the extra bar changing the
        # resistance and so the reward ratio. That is what `verify` is for.
        f["res"] = high.rolling(19).max().shift(1)
        # The support window does include today, and is twenty bars.
        f["sup"] = low.rolling(20).min()
        f["avg_volume"] = volume.rolling(20).mean()
        f["obv_5"] = f["OBV"].shift(5)
        frames.append(f)
    data = pd.concat(frames, ignore_index=True)

    close, high, low = data["Close"], data["High"], data["Low"]
    atr = data["ATR"]

    # -- trend ---------------------------------------------------------
    data["trend"] = (
        np.where(
            (data["EMA20"] > data["EMA50"]) & (data["EMA50"] > data["EMA200"]), 20,
            np.where(data["EMA20"] > data["EMA50"], 12, 0))
        + np.where(close > data["EMA20"], 3, 0)
        + np.where(close > data["EMA50"], 3, 0)
        + np.where(close > data["EMA200"], 4, 0)
    )

    # -- volume --------------------------------------------------------
    data["volume"] = (
        np.where(data["Volume"] > data["avg_volume"] * 1.5, 10,
                 np.where(data["Volume"] > data["avg_volume"], 5, 0))
        + np.where(data["OBV"] > data["obv_5"], 10, 0)
    )

    # -- support / resistance ------------------------------------------
    to_support = (close - data["sup"]) / data["sup"] * 100
    to_resistance = (data["res"] - close) / close * 100
    data["support"] = (np.where(to_support <= 3, 10, 0)
                       + np.where(to_resistance >= 5, 5, 0))

    # -- entry ---------------------------------------------------------
    bar_range = (high - low)
    close_position = np.where(bar_range > 0, (close - low) / bar_range, 0.0)
    breakout = (close > data["res"]) & (data["Volume"] > data["avg_volume"] * 1.2)
    buy_high = close.round(3)
    stop = (data["sup"] - atr * 0.30).round(3)
    risk = buy_high - stop
    reward = (data["res"] + atr * 2).round(3) - buy_high
    with np.errstate(divide="ignore", invalid="ignore"):
        rr = np.where(risk > 0, (reward / risk).round(2), 0.0)
    # `entry_signal` returns zero for every component when risk or reward is
    # not positive, so the whole component collapses -- not just its RR part.
    valid = (risk > 0) & (reward > 0)
    data["rr"] = np.where(valid, rr, 0.0)
    data["entry"] = np.where(
        valid,
        np.where(breakout, 8, 0)
        + np.where((close - data["sup"]).abs() <= atr, 6, 0)
        + np.where(close_position >= 0.80, 4, 0)
        + np.where(rr >= 3, 10, np.where(rr >= 2, 8, np.where(rr >= 1.5, 5, 0))),
        0,
    )

    # -- momentum ------------------------------------------------------
    rsi, adx, macd = data["RSI"], data["ADX"], data["MACD_Signal"]
    data["momentum"] = (
        np.where(rsi.between(45, 65), 5, np.where(rsi.between(35, 45, "left"), 3, 0))
        + np.where((data["MACD"] > macd) & (data["MACD"] > 0), 10,
                   np.where(data["MACD"] > macd, 5, 0))
        + np.where(adx >= 30, 5, np.where(adx >= 25, 3, 0))
        + np.where((close > data["BB_MIDDLE"]) & (close < data["BB_UPPER"]), 5,
                   np.where(close <= data["BB_LOWER"], 3, 0))
    )

    data["score"] = data[list(COMPONENTS)].sum(axis=1)

    # ------------------------------------------------------------------
    # Confidence, which the BUY decision gates on separately
    # ------------------------------------------------------------------
    # Built from the same inputs as the score -- a second opinion from the same
    # witness -- but with different weights and two caps, so it is not a
    # monotone function of the score and has to be computed rather than
    # inferred.
    strength = (data["EMA20"] - data["EMA50"]) / data["EMA50"] * 100
    trend_confidence = np.minimum(
        np.where((data["EMA20"] > data["EMA50"])
                 & (data["EMA50"] > data["EMA200"]), 20,
                 np.where(data["EMA20"] > data["EMA50"], 12, 0))
        + np.where(close > data["EMA20"], 5, 0)
        + np.where(close > data["EMA50"], 5, 0)
        + np.where(close > data["EMA200"], 10, 0)
        + np.where(strength >= 3, 10, np.where(strength >= 1, 5, 0)),
        60,
    )
    volume_confidence = (
        np.where(data["Volume"] > data["avg_volume"] * 1.5, 10,
                 np.where(data["Volume"] > data["avg_volume"], 5, 0))
        + np.where(data["OBV"] > data["obv_5"], 10, 0)
    )
    momentum_confidence = (
        np.where(rsi.between(45, 65), 5, np.where(rsi.between(35, 45, "left"), 3, 0))
        + np.where((data["MACD"] > macd) & (data["MACD"] > 0), 10,
                   np.where(data["MACD"] > macd, 5, 0))
        + np.where(adx >= 30, 10, np.where(adx >= 25, 5, 0))
        + np.where((close > data["BB_MIDDLE"]) & (close < data["BB_UPPER"]), 5,
                   np.where(close <= data["BB_LOWER"], 3, 0))
    )
    entry_confidence = np.where(
        valid,
        np.where(breakout, 10, 0)
        + np.where((close - data["sup"]).abs() <= atr, 8, 0)
        + np.where(close_position >= 0.80, 5, 0)
        + np.where(rr >= 3, 10, np.where(rr >= 2, 8, np.where(rr >= 1.5, 5, 0))),
        0,
    )
    support_confidence = (np.where(to_support <= 3, 10, 0)
                          + np.where(to_resistance >= 5, 5, 0))
    data["confidence"] = np.minimum(
        trend_confidence + volume_confidence + momentum_confidence
        + entry_confidence + support_confidence,
        100,
    )
    return data


def verify(data: pd.DataFrame, samples: int = 400, seed: int = 20260829) -> dict:
    """Check the vectorised components against the real per-bar functions.

    Draws random bars, rebuilds each symbol's frame, and calls the actual
    `strategy/` functions. Any mismatch is a defect in this file, not in the
    strategy -- which is the point of running it before believing anything.
    """
    from strategy.entry import entry_signal
    from strategy.momentum import momentum_score
    from strategy.support import support_resistance
    from strategy.trend import trend_score
    from strategy.volume import volume_score

    rng = np.random.default_rng(seed)
    usable = data[data["Bar"] >= 250]
    picks = rng.choice(len(usable), size=min(samples, len(usable)), replace=False)
    rows = usable.iloc[picks]

    by_symbol = {s: f.sort_values("Date").reset_index(drop=True)
                 for s, f in data.groupby("Symbol", sort=False)}
    mismatches, checked = [], 0
    for _, row in rows.iterrows():
        frame = by_symbol[row["Symbol"]]
        i = int(row["Bar"])
        if i >= len(frame):
            continue
        checked += 1
        entry = entry_signal(frame, i)
        support = support_resistance(frame, i)
        confidence = min(
            trend_score(frame, i)["confidence"]
            + volume_score(frame, i)["confidence"]
            + momentum_score(frame, i)["confidence"]
            + entry["confidence"] + support["confidence"],
            100,
        )
        actual = {
            "confidence": confidence,
            "trend": trend_score(frame, i)["score"],
            "volume": volume_score(frame, i)["score"],
            "support": support["score"],
            "entry": entry["score"],
            "momentum": momentum_score(frame, i)["score"],
        }
        for name, value in actual.items():
            if int(value) != int(row[name]):
                mismatches.append((row["Symbol"], i, name, value, row[name]))
    return {"checked": checked, "mismatches": mismatches}


def main() -> int:
    from scripts.research.panel import load

    data = add_components(load())
    result = verify(data)
    print(f"checked {result['checked']} random bars against the real functions")
    if result["mismatches"]:
        print(f"{len(result['mismatches'])} MISMATCHES:")
        for row in result["mismatches"][:12]:
            print("   ", row)
        return 1
    print("every component matches on every sampled bar")
    print("\ncomponent distributions over the whole panel:")
    print(f"{'component':<12}{'max':>6}{'mean':>9}{'at max':>9}{'at zero':>9}"
          f"{'distinct':>10}")
    for name in COMPONENTS:
        column = data[name].dropna()
        print(f"{name:<12}{MAXIMUM[name]:>6}{column.mean():>9.1f}"
              f"{(column == MAXIMUM[name]).mean() * 100:>8.1f}%"
              f"{(column == 0).mean() * 100:>8.1f}%{column.nunique():>10}")
    total = data["score"].dropna()
    print(f"{'TOTAL':<12}{118:>6}{total.mean():>9.1f}"
          f"{(total == 118).mean() * 100:>8.1f}%{(total == 0).mean() * 100:>8.1f}%"
          f"{total.nunique():>10}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
