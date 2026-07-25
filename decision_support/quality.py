"""Observable market-quality measurements with explicit missing evidence."""

from __future__ import annotations

import math
import pandas as pd


def finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def relative_volume(frame, lookback=20):
    if frame is None or frame.empty or "Volume" not in frame:
        return None
    volume = pd.to_numeric(frame["Volume"], errors="coerce").dropna()
    if len(volume) < 2:
        return None
    current = float(volume.iloc[-1])
    average = float(volume.iloc[-(int(lookback) + 1):-1].mean())
    return current / average if average > 0 else None


def spread_metrics(bid, ask):
    bid, ask = finite(bid), finite(ask)
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return {"spread_percent": None, "spread_score": None, "spread_quality": "Unavailable"}
    midpoint = (bid + ask) / 2.0
    spread = (ask - bid) / midpoint * 100.0
    if spread <= 0.10:
        label, score = "Excellent", 1.0
    elif spread <= 0.25:
        label, score = "Good", 0.8
    elif spread <= 0.50:
        label, score = "Fair", 0.5
    else:
        label, score = "Poor", 0.0
    return {"spread_percent": spread, "spread_score": score, "spread_quality": label}


def liquidity_metrics(price, volume, rvol, spread_score, bid_size=None, ask_size=None, trades=None, config=None):
    config = config or {}
    price, volume = finite(price), finite(volume)
    turnover = price * volume if price is not None and volume is not None else None
    components = {}
    if volume is not None:
        components["volume"] = min(1.0, volume / max(1.0, float(config.get("minimum_volume", 100_000))))
    if turnover is not None:
        components["turnover"] = min(1.0, turnover / max(1.0, float(config.get("minimum_turnover", 1_000_000))))
    if rvol is not None:
        components["relative_volume"] = min(1.0, float(rvol) / 2.0)
    if spread_score is not None:
        components["spread"] = float(spread_score)
    if finite(bid_size) is not None and finite(ask_size) is not None:
        depth = finite(bid_size) + finite(ask_size)
        components["depth"] = min(1.0, depth / max(1.0, float(config.get("minimum_volume", 100_000))))
    if finite(trades) is not None:
        components["trades"] = min(1.0, finite(trades) / 100.0)
    score = sum(components.values()) / len(components) if components else None
    label = "Unavailable" if score is None else (
        "Excellent" if score >= 0.85 else "Good" if score >= 0.65
        else "Fair" if score >= 0.4 else "Poor"
    )
    return {
        "liquidity_score": score, "liquidity_quality": label,
        "turnover": turnover, "liquidity_components": components,
    }


def atr_feasibility(atr, price, target_percent=2.0):
    atr, price = finite(atr), finite(price)
    if atr is None or price is None or price <= 0:
        return {"atr_percent": None, "atr_score": None, "atr_feasible": None}
    atr_percent = atr / price * 100.0
    ratio = atr_percent / max(0.01, float(target_percent))
    return {
        "atr_percent": atr_percent,
        "atr_score": min(1.0, ratio),
        "atr_feasible": ratio >= 1.0,
    }


def bid_ask_balance(bid_size, ask_size):
    bid_size, ask_size = finite(bid_size), finite(ask_size)
    if bid_size is None or ask_size is None or bid_size + ask_size <= 0:
        return None
    return bid_size / (bid_size + ask_size)
