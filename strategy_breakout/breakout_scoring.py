"""Breakout-only score; no Classic score component is reused."""

from __future__ import annotations

from typing import Any


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


def breakout_score(df, i: int, entry: dict, config: Any) -> dict:
    features = dict(entry.get("features") or {})
    score = 0
    reasons: list[str] = []
    for name, weight in WEIGHTS.items():
        if bool(features.get(name)):
            score += weight
            reasons.append(name.replace("_", " ").title())

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
