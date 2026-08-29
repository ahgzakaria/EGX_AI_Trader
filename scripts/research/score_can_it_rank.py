r"""Can the score be rebuilt, or only retired?

`SCORE_DIAGNOSIS.md` measured the total at r = -0.032 on 638 realised trades and
stopped there, saying a rebuild was real work and not attempted. This is that
work, and it starts from the only question that decides whether a rebuild is
possible at all:

    within the population the score actually discriminates -- the bars that pass
    every OTHER gate -- does any weighting of its five components rank the
    outcome out of sample?

If a weighting fitted on 2016-2022 ranks 2023-2026 better than chance, the score
can be rebuilt and the weights are the answer. If it does not, no reweighting
can help and the honest fix is to stop presenting a number as a ranking.

Three things this is careful about, because each one can manufacture a result:

* **The population.** Ranking is measured among candidates that reached the
  score, not across all bars. A score that separates a breakout from a
  collapsing penny stock has done nothing useful -- the gates already did that.
* **The target is lift, not return.** Net return over the same twenty sessions
  minus what an average tradeable name did on the same days. Otherwise the
  ranking is measuring which month it was.
* **Weights are fitted on train and read on validation, once.** Fitting and
  reading on the same era is how a score with no signal comes to look like one.

    venv\Scripts\python.exe scripts\research\score_can_it_rank.py
"""
from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd
from scipy import stats

from core.environment import load_project_environment

load_project_environment()

from scripts.research.panel import load                          # noqa: E402
from scripts.research.score_components import (                  # noqa: E402
    COMPONENTS, MAXIMUM, add_components,
)
from scripts.research.signal_scan import SPLIT, costs_by_symbol  # noqa: E402

HOLD = 20

#: The shipped gate settings, from `config/settings.json`.
MIN_TREND, MIN_MOMENTUM, MIN_VOLUME = 12, 3, 0
MIN_RR, MAX_RR = 3.0, 100.0
QUALITY_ADX, QUALITY_VOLUME, QUALITY_ATR, QUALITY_ROOM = 21, 1.0, 1.5, 3.0
MARKET_TREND_ADX, MARKET_WEAK_ADX = 25, 18


def prepare():
    panel = load()
    costs = costs_by_symbol(panel["Symbol"].unique())
    data = add_components(panel)

    frames = []
    for _symbol, frame in data.groupby("Symbol", sort=False):
        f = frame.sort_values("Date").copy()
        close = f["Close"]
        # From the fill, not from the signal's own close: the strategy's entry
        # is a limit at today's close filled on a later bar, so the earliest
        # honest measurement starts the next session.
        f["forward"] = (close.shift(-HOLD - 1) / close.shift(-1) - 1) * 100
        frames.append(f)
    data = pd.concat(frames, ignore_index=True)
    data["net"] = data["forward"] - data["Symbol"].map(costs)
    return data


def eligible(data: pd.DataFrame) -> pd.Series:
    """Bars that pass every gate except the score and the confidence.

    This is the population the score is asked to rank. Reproducing the waterfall
    rather than approximating it matters: a looser population would let the
    score take credit for separations the gates had already made.
    """
    adx, close = data["ADX"], data["Close"]
    trending = adx >= MARKET_TREND_ADX
    weak = (adx >= MARKET_WEAK_ADX) & (adx < MARKET_TREND_ADX)
    regime = (
        (trending & (data["EMA20"] > data["EMA50"]) & (close > data["EMA20"]))
        | (weak & (close > data["EMA50"]))
    )
    room = (data["res"] - close) / close * 100
    quality = (
        (adx >= QUALITY_ADX)
        & (data["VOLUME_RATIO"] >= QUALITY_VOLUME)
        & (data["ATR_PERCENT"] >= QUALITY_ATR)
        & (room >= QUALITY_ROOM)
    )
    return (
        regime & quality
        & (data["trend"] >= MIN_TREND)
        & (data["momentum"] >= MIN_MOMENTUM)
        & (data["volume"] >= MIN_VOLUME)
        & (data["rr"] >= MIN_RR) & (data["rr"] <= MAX_RR)
        & data["net"].notna()
    )


def rank_power(frame: pd.DataFrame, values: pd.Series, label: str):
    """Spearman rho, its t, and the spread between the best and worst fifth."""
    if len(frame) < 40:
        print(f"{label:<34}{'too few':>10}")
        return None
    rho, p = stats.spearmanr(values, frame["lift"])
    quintile = pd.qcut(values.rank(method="first"), 5, labels=False)
    top = frame.loc[quintile == 4, "lift"].mean()
    bottom = frame.loc[quintile == 0, "lift"].mean()
    print(f"{label:<34}{len(frame):>8,}{rho:>+9.3f}{p:>9.2f}"
          f"{bottom:>+10.2f}{top:>+10.2f}{top - bottom:>+10.2f}")
    return {"n": len(frame), "rho": rho, "p": p, "spread": top - bottom}


def main() -> int:
    data = prepare()
    pool = data[data["net"].notna()]
    per_date = pool.groupby("Date")["net"].mean()
    data["lift"] = data["net"] - data["Date"].map(per_date)

    candidates = data[eligible(data)].copy()
    train = candidates[candidates["Date"] < SPLIT]
    valid = candidates[candidates["Date"] >= SPLIT]
    print(f"{len(candidates):,} bars pass every gate except the score "
          f"({len(train):,} train / {len(valid):,} validation)")
    print(f"of {len(data):,} bars in the panel -- "
          f"{len(candidates) / len(data) * 100:.2f}%\n")

    header = (f"{'':<34}{'n':>8}{'rho':>9}{'p':>9}{'worst 5th':>10}"
              f"{'best 5th':>10}{'spread':>10}")

    print("=" * 90)
    print("1. THE SHIPPED SCORE, AND EACH COMPONENT, WITHIN THAT POPULATION")
    print("=" * 90)
    for era, frame in (("train", train), ("validation", valid)):
        print(f"\n{era}")
        print(header)
        print("-" * 90)
        rank_power(frame, frame["score"], "the shipped total")
        for name in COMPONENTS:
            rank_power(frame, frame[name], f"  {name}")

    print("\n" + "=" * 90)
    print("2. CAN A FITTED WEIGHTING DO BETTER?")
    print("=" * 90)
    print("Weights fitted on train only, then read on validation once. The")
    print("comparison that matters is the last two rows: a rebuild is possible")
    print("only if the fitted weighting beats the shipped one out of sample.\n")

    matrix = train[list(COMPONENTS)].to_numpy(float)
    target = train["lift"].to_numpy(float)
    # Ordinary least squares on the raw components. Deliberately the simplest
    # thing that could work: if a linear reweighting cannot rank, a more
    # elaborate fit on the same five inputs is fitting noise more thoroughly.
    fitted, *_ = np.linalg.lstsq(
        np.column_stack([matrix, np.ones(len(matrix))]), target, rcond=None)
    weights, intercept = fitted[:-1], fitted[-1]

    print("fitted weight per point of each component (train):")
    for name, weight in zip(COMPONENTS, weights):
        print(f"  {name:<12}{weight:>+9.4f}   "
              f"(worth {weight * MAXIMUM[name]:+.2f}% at its maximum)")
    print(f"  {'intercept':<12}{intercept:>+9.4f}\n")

    print(header)
    print("-" * 90)
    for era, frame in (("train", train), ("validation", valid)):
        rebuilt = frame[list(COMPONENTS)].to_numpy(float) @ weights
        rank_power(frame, frame["score"], f"{era}: shipped weights")
        rank_power(frame, pd.Series(rebuilt, index=frame.index),
                   f"{era}: fitted weights")
        equal = frame[list(COMPONENTS)].div(pd.Series(MAXIMUM)).sum(axis=1)
        rank_power(frame, equal, f"{era}: equal weights")
        print()

    print("=" * 90)
    print("3. IS THERE ANYTHING ELSE IN THE DATA THAT RANKS THEM?")
    print("=" * 90)
    print("The score is built from five things. If none of them rank, the")
    print("question is whether the strategy is measuring the wrong quantities")
    print("altogether, so every other indicator it already computes is tried.\n")
    others = ["ATR_PERCENT", "VOLUME_RATIO", "ADX", "RSI", "RSI7", "BB_WIDTH",
              "BB_POSITION", "EMA20_DIST", "EMA50_DIST", "EMA200_DIST",
              "EMA20_SLOPE", "EMA50_SLOPE", "RSI_SLOPE", "ADX_RISING",
              "OBV_SLOPE", "MACD_HIST", "MACD_CROSS_AGE", "DIST_HIGH20",
              "DIST_LOW20", "rr"]
    print(f"{'feature':<34}{'train rho':>12}{'valid rho':>12}"
          f"{'valid p':>10}{'both ways?':>12}")
    print("-" * 82)
    survivors = []
    for name in others:
        if name not in candidates.columns:
            continue
        a = train[[name, "lift"]].dropna()
        b = valid[[name, "lift"]].dropna()
        if len(a) < 40 or len(b) < 40:
            continue
        rho_a = stats.spearmanr(a[name], a["lift"])[0]
        rho_b, p_b = stats.spearmanr(b[name], b["lift"])
        agree = "yes" if rho_a * rho_b > 0 else ""
        if agree and p_b < 0.05:
            survivors.append(name)
            agree = "YES, p<.05"
        print(f"{name:<34}{rho_a:>+12.3f}{rho_b:>+12.3f}{p_b:>10.2f}{agree:>12}")

    print(f"\nfeatures agreeing in both eras at p < 0.05: "
          f"{survivors if survivors else 'none'}")
    print("\nTwenty features were tried. At p < 0.05 one false positive per")
    print("twenty is the expectation, so a lone survivor is a hypothesis and")
    print("not a finding.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
