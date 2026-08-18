"""Fit the breakout weights jointly, then judge them through the strategy.

Eight separate per-feature measurements produced weights that backtested worse
than the hand-assigned ones they replaced. The failure was not in the
measuring, it was in measuring one feature at a time: the features overlap --
`high_volume_breakout` is a strict subset of `previous_resistance_breakout` --
and the score's real work is counting how many independent confirmations
agree. A univariate lift cannot see either of those.

A joint fit can. Logistic regression estimates each coefficient holding the
others fixed, so a feature that only ever fires alongside a stronger one gets
the credit it independently deserves, which is often none.

Two rules carried over from the attempt that failed:

* **Judge through the strategy, not over raw bars.** The volume-band finding
  was real on unfiltered breakouts and evaporated once several other
  confirmations had already fired. Candidate weights are backtested by running
  BreakoutSwingStrategy itself.
* **Report every window.** Five separate 260-bar windows, not the flattering
  one, and a candidate has to win most of them to be worth anything.

Read-only. Ships nothing: it prints candidate weights and how they did.

    venv/Scripts/python.exe scripts/research/joint_weight_fit.py
"""
from __future__ import annotations

import glob
import os
import statistics
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

COST = 0.80
HOLD = 20
TOP_N = 40
BARS = 260
WINDOWS = (0, 400, 700, 1000, 1200)
SPLIT_DATE = "2024-01-01"
MAX_PLAUSIBLE_RANGE = 25.0        # EGX caps a session at +/-20%

#: The engine's own features, in the order the score sums them.
FEATURES = (
    "previous_resistance_breakout",
    "high_volume_breakout",
    "ema20_continuation",
    "consolidation_breakout",
    "atr_expansion",
    "higher_high_breakout",
    "ema_aligned",
)


def load_frames():
    frames = {}
    for path in glob.glob("data/frozen_eodhd_seed/*.csv"):
        symbol = os.path.basename(path)[:-4]
        frame = pd.read_csv(path)
        if len(frame) < 400:
            continue
        frame = frame.rename(columns=str.title)
        frame = frame[["Date", "Open", "High", "Low", "Close", "Volume"]].dropna()
        frame = frame[(frame[["Open", "High", "Low", "Close"]] > 0).all(axis=1)]
        frame = frame[frame["Volume"] > 0]
        frame = frame[
            (frame["High"] - frame["Low"]) / frame["Open"] * 100 <= MAX_PLAUSIBLE_RANGE
        ]
        if len(frame) >= 400:
            frames[symbol] = frame.reset_index(drop=True)
    return frames


def with_features(frame: pd.DataFrame) -> pd.DataFrame:
    f = frame.copy()
    close, high, low, volume = f["Close"], f["High"], f["Low"], f["Volume"]

    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()
    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(14).mean()
    volume_ratio = volume / volume.rolling(20).mean()
    resistance = high.rolling(20).max().shift(1)
    consolidation_width = (
        (high.rolling(10).max() - low.rolling(10).min())
        / low.rolling(10).min() * 100
    ).shift(1)

    breakout = close > resistance
    f["previous_resistance_breakout"] = breakout
    f["high_volume_breakout"] = breakout & (volume_ratio >= 1.5)
    f["ema20_continuation"] = (close > ema20) & (low <= ema20 * 1.01)
    f["consolidation_breakout"] = breakout & (consolidation_width <= 8)
    f["atr_expansion"] = (high - low) >= atr * 1.2
    f["higher_high_breakout"] = high > resistance
    f["ema_aligned"] = (ema20 > ema50) & (ema50 > ema200)
    f["forward"] = (close.shift(-HOLD) - close) / close * 100.0 - COST
    return f


def fit_weights(train: pd.DataFrame):
    """Joint logistic fit; returns coefficients and the scaled weights."""

    from sklearn.linear_model import LogisticRegression

    x = train[list(FEATURES)].astype(float).to_numpy()
    y = (train["forward"] > 0).astype(int).to_numpy()

    model = LogisticRegression(max_iter=2000, C=1.0)
    model.fit(x, y)
    coefficients = dict(zip(FEATURES, model.coef_[0]))

    # Only positive contributions can be weights; a feature whose independent
    # effect is negative earns nothing rather than subtracting, because the
    # score is a confirmation count and a negative weight would let one
    # feature veto several others.
    positive = {k: v for k, v in coefficients.items() if v > 0}
    total = sum(positive.values())
    weights = {
        name: (round(value / total * 100) if name in positive else 0)
        for name, value in coefficients.items()
    }
    return coefficients, weights


def backtest(weights: dict, minimum_score: int, prepared: dict) -> dict:
    """Run the real strategy with these weights, keeping windows separate.

    Pooling the windows would let one exceptional stretch carry a candidate
    that loses everywhere else, which is the failure mode this whole file
    exists to avoid.
    """

    from strategy_breakout import breakout_scoring
    from strategy_breakout.breakout_strategy import BreakoutConfig, BreakoutSwingStrategy

    original = breakout_scoring.WEIGHTS
    breakout_scoring.WEIGHTS = weights
    try:
        base = vars(BreakoutConfig())
        strategy = BreakoutSwingStrategy(
            BreakoutConfig(**{**base, "minimum_score": minimum_score})
        )
        per_window = {}
        for offset in WINDOWS:
            nets = []
            for frame in prepared.values():
                start = max(200, len(frame) - BARS - offset)
                stop = max(start, len(frame) - offset - HOLD - 1)
                for i in range(start, stop):
                    try:
                        result = strategy.evaluate(frame, i)
                    except Exception:
                        continue
                    if result.get("Signal") != "BUY":
                        continue
                    entry = float(frame["Close"].iloc[i])
                    exit_price = float(frame["Close"].iloc[i + HOLD])
                    nets.append((exit_price - entry) / entry * 100.0 - COST)
            per_window[offset] = nets
        return per_window
    finally:
        breakout_scoring.WEIGHTS = original


def summarise(label: str, per_window: dict, baseline: dict | None = None) -> dict:
    pooled = [n for nets in per_window.values() for n in nets]
    if not pooled:
        print(f"{label:<38}{'no signals':>39}")
        return {}

    beaten = ""
    if baseline:
        wins = sum(
            1 for offset, nets in per_window.items()
            if nets and baseline.get(offset)
            and statistics.fmean(nets) > statistics.fmean(baseline[offset])
        )
        beaten = f"{wins}/{len(WINDOWS)}"

    print(f"{label:<38}{len(pooled):>8,}{statistics.fmean(pooled):>10.3f}%"
          f"{sum(1 for n in pooled if n > 0) / len(pooled) * 100:>9.1f}%"
          f"{statistics.median(pooled):>10.3f}%{beaten:>9}")

    # Per window, because only the most recent one is genuinely out of sample
    # against a fit trained before 2024-01-01. A candidate that wins only in
    # the windows it was trained on has told us nothing.
    cells = []
    for offset in WINDOWS:
        nets = per_window.get(offset) or []
        cells.append(
            f"{statistics.fmean(nets):+6.2f}%/{len(nets):<4}" if nets else "  --      "
        )
    print(f"{'':<38}" + "".join(f"{c:>13}" for c in cells))
    return per_window


#: Which era each window actually falls in, against a fit trained before
#: 2024-01-01. Printed with the results because the pooled average is
#: dominated by the in-sample windows and reads as an improvement that the
#: out-of-sample window does not support.
WINDOW_ERAS = {
    0: "OUT-OF-SAMPLE  2025-07 to 2026-07",
    400: "mixed          2023-11 to 2024-11",
    700: "in training    2022-08 to 2023-08",
    1000: "in training    2021-06 to 2022-05",
    1200: "in training    2020-08 to 2021-08",
}


def main() -> int:
    from indicators.technical import calculate_indicators
    from strategy_breakout.breakout_scoring import WEIGHTS as HAND_WEIGHTS

    frames = load_frames()
    liquidity = {s: (f["Close"] * f["Volume"]).tail(250).median() for s, f in frames.items()}
    universe = sorted(liquidity, key=liquidity.get, reverse=True)[:TOP_N]

    featured = pd.concat(
        [with_features(frames[s]).assign(Symbol=s) for s in universe],
        ignore_index=True,
    ).dropna(subset=["forward"])
    train = featured[featured["Date"] < SPLIT_DATE]
    test = featured[featured["Date"] >= SPLIT_DATE]
    print(f"{len(universe)} symbols   train {len(train):,}   test {len(test):,}")
    print()

    coefficients, fitted = fit_weights(train)
    print("joint logistic fit, trained before", SPLIT_DATE)
    print(f"{'feature':<32}{'coefficient':>13}{'fitted':>9}{'hand':>7}")
    print("-" * 61)
    for name in FEATURES:
        print(f"{name:<32}{coefficients[name]:>+13.4f}{fitted[name]:>9}"
              f"{HAND_WEIGHTS.get(name, 0):>7}")
    print()

    # Stability: refit on the validation era. Coefficients that flip sign
    # between eras are noise whatever their magnitude.
    test_coefficients, _ = fit_weights(test)
    flipped = [
        name for name in FEATURES
        if np.sign(coefficients[name]) != np.sign(test_coefficients[name])
    ]
    print(f"sign-stable across eras : "
          f"{len(FEATURES) - len(flipped)}/{len(FEATURES)}")
    if flipped:
        print(f"flipped                 : {', '.join(flipped)}")
    print()

    prepared = {}
    for symbol in universe:
        try:
            prepared[symbol] = calculate_indicators(frames[symbol].copy())
        except Exception:
            pass

    # A coefficient that changes sign between eras is noise however large it
    # is, and the largest fitted weight sits on one. This variant keeps only
    # the features whose sign held, so the comparison says whether the gain
    # came from the fit or from the unstable terms.
    stable = {
        name: weight if name not in flipped else 0
        for name, weight in fitted.items()
    }
    stable_total = sum(stable.values())
    if stable_total:
        stable = {k: round(v / stable_total * 100) for k, v in stable.items()}

    print(f"backtested through BreakoutSwingStrategy, {len(WINDOWS)} windows kept apart")
    print(f"{'candidate':<38}{'trades':>8}{'avg net':>10}{'win%':>9}"
          f"{'median':>10}{'windows':>9}")
    print("-" * 84)
    baseline = summarise("hand weights (shipping)", backtest(HAND_WEIGHTS, 65, prepared))
    for minimum_score in (40, 65):
        summarise(f"joint fit, score {minimum_score}",
                  backtest(fitted, minimum_score, prepared), baseline)
    for minimum_score in (40, 65):
        summarise(f"joint fit minus flipped, score {minimum_score}",
                  backtest(stable, minimum_score, prepared), baseline)

    print()
    print("stable-only weights:", {k: v for k, v in stable.items() if v})
    print()
    print("windows, left to right:")
    for offset in WINDOWS:
        print(f"  w-{offset:<5} {WINDOW_ERAS[offset]}")
    print()
    print("READ THE FIRST COLUMN, NOT THE POOLED AVERAGE. Four of the five")
    print("windows sit inside the era the weights were fitted on, so a pooled")
    print("figure is mostly in-sample and will flatter any fit. Only w-0 is a")
    print("real test. When this was run on 2026-08-18 the joint fit scored")
    print("+3.89% there against +3.90% for the shipping weights -- a tie --")
    print("while beating them by 3 to 5 points in every training-era window.")
    print("That is what overfitting looks like, and it was not shipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
