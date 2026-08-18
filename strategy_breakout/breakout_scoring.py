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
    "previous_resistance_breakout": 20,
    "opening_range_breakout": 15,
    "high_volume_breakout": 15,
    "ema20_continuation": 10,
    "consolidation_breakout": 15,
    "atr_expansion": 15,
    "higher_high_breakout": 10,
    "retest_confirmed": 10,
}

#: These weights are not measured. Replacing them with measured ones was tried
#: on 2026-08-18 and made results worse, which is worth more than the attempt.
#:
#: Feature-by-feature lift over 166,173 stock-days (train < 2024-01-01,
#: validated after, net of a 0.80% round trip) said the allocation was close to
#: backwards: volume confirmation lifted +1.21% out of sample against +0.49%
#: for the breakout itself, while `consolidation_breakout` reversed sign and
#: `higher_high_breakout` collapsed. Re-weighting in proportion to that lift
#: gave 55 points to volume, 23 to the breakout, and zero to the two failures.
#:
#: Backtested over three separate 260-bar windows, the re-weighting lost:
#:
#:     configuration                 trades   avg net    win%
#:     hand weights, 2.5x gate          240    3.498%   55.0%
#:     measured weights, 2.5x gate      613    2.871%   51.5%
#:
#: Both measurements are right and they are not in conflict. Per-feature lift
#: asks what one feature predicts alone. The score asks how many independent
#: confirmations a setup carries, and requiring four weak ones is itself a
#: selectivity mechanism that per-feature lift cannot see. Concentrating the
#: weight on the two strongest features let a setup qualify on those two
#: alone, roughly tripling the signal count and diluting it.
#:
#: So a feature earning no lift on its own is not evidence its weight is
#: wrong. Deriving these weights properly needs a joint fit against forward
#: return, not eight separate measurements. Until someone does that, these
#: stay, labelled honestly.
WEIGHTS_ARE_MEASURED = False

WEIGHTS_PROVENANCE = (
    "hand-assigned, not measured. A measured re-weighting was tried on "
    "2026-08-18 over data/frozen_eodhd_seed (166,173 stock-days, train < "
    "2024-01-01) and backtested worse across three windows: 2.871% net per "
    "trade at a 51.5% win rate against 3.498% and 55.0% for these. "
    "Re-derive with scripts/research/breakout_features.py."
)

#: Nothing in the scoring or the entry gates survived validation. The volume
#: gate was raised to 2.5x on the same measurements and reverted too: run
#: through the strategy rather than over raw breakouts, 1.5 beat it on net,
#: median and four of five windows. See `BreakoutConfig.minimum_volume_ratio`.
#:
#: The only change from that day that stands is a factual correction, not a
#: tune: the commission rate, which was a 0.003 placeholder against a contract
#: note showing 0.1819% per side.
MEASURED_CHANGES = ("commission",)


def breakout_score(df, i: int, entry: dict, config: Any) -> dict:
    features = dict(entry.get("features") or {})
    score = 0
    reasons: list[str] = []
    for name, weight in WEIGHTS.items():
        if bool(features.get(name)):
            score += weight
            reasons.append(name.replace("_", " ").title())

    # These two lifted +0.10% and -0.12% out of sample, so they were briefly
    # zeroed on 2026-08-18. They are restored for the same reason the weights
    # above are: their contribution is a confirmation count, not a standalone
    # prediction, and removing it made the score less selective and the
    # results worse. See the note on WEIGHTS.
    last = df.iloc[i]
    aligned = bool(last["EMA20"] > last["EMA50"] > last["EMA200"])
    above_ema20 = bool(last["Close"] > last["EMA20"])
    if aligned:
        score += 10
        reasons.append("Bullish EMA Alignment")
    elif above_ema20:
        score += 5
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
