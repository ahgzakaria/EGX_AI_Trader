"""Breakout-only score; no Classic score component is reused."""

from __future__ import annotations

from typing import Any


#: Weights measured, not chosen.
#:
#: These were hand-assigned: 20 for a resistance breakout, 15 for volume, 15
#: for consolidation, and so on. Nobody had measured them. Measuring them over
#: the archived daily history -- 60 most-traded names, 166,173 stock-days,
#: trained before 2024-01-01 and validated after -- said the allocation was
#: close to backwards. Each weight below is the feature's out-of-sample lift
#: over the base rate, averaged across 5, 10 and 20-day holds and net of the
#: 0.80% round trip, normalised to 100.
#:
#:   feature                        hand   train lift   test lift
#:   high_volume_breakout             15       +2.06%      +1.21%
#:   previous_resistance_breakout     20       +1.75%      +0.49%
#:   atr_expansion                    15       +0.21%      +0.42%
#:   ema20_continuation               10       -0.08%      +0.04%
#:   higher_high_breakout             10       +1.21%      -0.10%
#:   consolidation_breakout           15       +0.50%      -0.72%
#:
#: The last two carried 25 points between them while failing out of sample --
#: consolidation_breakout reversed sign, and on only 362 validation days. Both
#: are now zero. They are kept as named keys rather than deleted so the
#: measurement that retired them stays visible.
#:
#: `opening_range_breakout` and `retest_confirmed` are not in the archived
#: daily data and could not be measured; they keep a nominal weight and are
#: marked as such.
WEIGHTS = {
    "high_volume_breakout": 55,
    "previous_resistance_breakout": 23,
    "atr_expansion": 19,
    "ema20_continuation": 2,
    "higher_high_breakout": 0,
    "consolidation_breakout": 0,
    "opening_range_breakout": 5,
    "retest_confirmed": 5,
}

#: Which weights rest on measurement and which do not, so a reader never has
#: to guess. Unmeasured weights are small by intent.
UNMEASURED_FEATURES = ("opening_range_breakout", "retest_confirmed")

#: Provenance for the measured weights, carried with the score itself.
WEIGHTS_PROVENANCE = (
    "measured 2026-08-18 over data/frozen_eodhd_seed: 60 symbols by median "
    "turnover, 166,173 stock-days, train < 2024-01-01, validated after, "
    "net of a 0.80% round trip"
)


def breakout_score(df, i: int, entry: dict, config: Any) -> dict:
    features = dict(entry.get("features") or {})
    score = 0
    reasons: list[str] = []
    for name, weight in WEIGHTS.items():
        if bool(features.get(name)):
            score += weight
            reasons.append(name.replace("_", " ").title())

    # EMA alignment carried 10 points and closing above EMA20 carried 5. Both
    # were measurably worthless out of sample -- alignment lifted +1.13% in
    # training and +0.10% in validation, and closing above EMA20 was negative
    # in validation at every horizon. They stay as recorded reasons, because
    # the reader still wants to see the trend context, and contribute nothing
    # to the score.
    last = df.iloc[i]
    aligned = bool(last["EMA20"] > last["EMA50"] > last["EMA200"])
    above_ema20 = bool(last["Close"] > last["EMA20"])
    if aligned:
        reasons.append("Bullish EMA Alignment")
    elif above_ema20:
        reasons.append("Close Above EMA20")
    score = min(int(score), 100)
    confidence = min(
        100,
        int(round(score * 0.75 + min(float(features.get("volume_ratio", 0)), 3) / 3 * 25)),
    )
    return {
        "score": score,
        "confidence": confidence,
        "reasons": reasons,
        "features": features,
    }


def edge_score(score: float, confidence: float, rr: float) -> float:
    """Independent comparison aid, not a gate and not Classic/AI ranking."""

    rr_quality = max(0.0, min(float(rr) / 3.0, 1.0)) * 100
    return round(min(100.0, score * 0.60 + confidence * 0.15 + rr_quality * 0.25), 2)
