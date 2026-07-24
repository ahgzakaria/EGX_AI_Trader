"""Transparent 0-10 Edge Score that never replaces the frozen Score."""

from __future__ import annotations

from decision_support.quality import finite


DEFAULT_WEIGHTS = {
    "trend_quality": 1.0, "momentum": 0.8, "relative_volume": 0.9,
    "liquidity": 1.0, "spread": 0.9, "bid_ask_balance": 0.4,
    "atr_feasibility": 0.7, "resistance_room": 0.7,
    "support_quality": 0.5, "market_strength": 0.8,
    "sector_strength": 0.5, "setup_quality": 0.8,
    "rubix_freshness": 0.9, "historical_performance": 0.5,
    "volatility": 0.5, "ai_probability": 0.6,
}


def calculate_edge_score(factors, weights=None):
    """Weighted mean over observed factors, with evidence completeness."""

    weights = dict(weights or DEFAULT_WEIGHTS)
    weighted = 0.0
    observed_weight = 0.0
    total_weight = sum(max(0.0, float(value)) for value in weights.values())
    contributions = {}
    missing = []
    for name, weight in weights.items():
        weight = max(0.0, float(weight))
        value = finite(factors.get(name))
        if value is None:
            missing.append(name)
            continue
        value = min(1.0, max(0.0, value))
        contribution = value * weight
        weighted += contribution
        observed_weight += weight
        contributions[name] = round(contribution, 6)
    score = weighted / observed_weight * 10.0 if observed_weight else 0.0
    completeness = observed_weight / total_weight * 100.0 if total_weight else 0.0
    return {
        "edge_score": round(score, 2),
        "evidence_completeness_percent": round(completeness, 1),
        "edge_contributions": contributions,
        "missing_edge_factors": missing,
    }
