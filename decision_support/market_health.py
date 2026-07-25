"""Breadth and market-health summaries derived from already-scanned rows."""

from __future__ import annotations

import pandas as pd

from decision_support.quality import relative_volume


def calculate_market_health(results, rvol_lookback=20):
    advances = declines = unchanged = 0
    rvol_values = []
    regimes = []
    for row in results or []:
        frame = row.get("Data")
        if isinstance(frame, pd.DataFrame) and len(frame) >= 2:
            current = float(frame["Close"].iloc[-1])
            previous = float(frame["Close"].iloc[-2])
            if current > previous:
                advances += 1
            elif current < previous:
                declines += 1
            else:
                unchanged += 1
            value = relative_volume(frame, rvol_lookback)
            if value is not None:
                rvol_values.append(value)
        regimes.append(str(row.get("Regime") or "UNKNOWN").upper())
    total = advances + declines + unchanged
    breadth = (advances - declines) / total if total else 0.0
    bull = sum(value == "BULL" for value in regimes)
    bear = sum(value == "BEAR" for value in regimes)
    regime_strength = (bull - bear) / len(regimes) if regimes else 0.0
    volume_strength = sum(rvol_values) / len(rvol_values) if rvol_values else None
    normalized_volume = min(1.0, (volume_strength or 0.0) / 1.5)
    score = max(0.0, min(10.0, (
        (breadth + 1.0) / 2.0 * 4.0
        + (regime_strength + 1.0) / 2.0 * 4.0
        + normalized_volume * 2.0
    )))
    bias = "STRONG" if score >= 7.5 else "CONSTRUCTIVE" if score >= 6 else "MIXED" if score >= 4 else "WEAK"
    return {
        "advances": advances, "declines": declines, "unchanged": unchanged,
        "advance_decline": advances - declines,
        "breadth_percent": round(breadth * 100, 1),
        "volume_strength": round(volume_strength, 2) if volume_strength is not None else None,
        "bull_symbols": bull, "bear_symbols": bear,
        "overall_market_score": round(score, 2), "market_bias": bias,
        "factor_score": score / 10.0,
    }
