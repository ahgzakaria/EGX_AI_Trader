"""Independent target geometry for BREAKOUT_SWING."""

from __future__ import annotations

from typing import Any

import pandas as pd


TARGET_MODELS = (
    "MEASURED_MOVE",
    "ATR_EXPANSION",
    "RISK_MULTIPLE",
    "NEXT_WEEKLY_RESISTANCE",
    "TRAILING_ATR",
)


def _value(config: Any, name: str):
    return getattr(config, name)


def next_weekly_resistance(
    df: pd.DataFrame, i: int, entry_price: float, lookback: int
) -> float | None:
    historical = df.iloc[:i].copy()
    if historical.empty or not isinstance(historical.index, pd.DatetimeIndex):
        return None
    weekly = historical["High"].resample("W-THU").max().dropna().tail(int(lookback))
    levels = sorted(float(value) for value in weekly if float(value) > entry_price)
    return levels[0] if levels else None


def target_candidates(
    df: pd.DataFrame,
    i: int,
    entry: dict,
    stop: float,
    config: Any,
) -> dict:
    price = float(entry["entry_price"])
    risk = price - float(stop)
    atr = float(df["ATR"].iloc[i])
    precision = int(_value(config, "price_precision"))
    window = int(_value(config, "measured_move_window"))
    prior = df.iloc[max(0, i - window) : i]
    range_height = (
        float(prior["High"].max() - prior["Low"].min())
        if not prior.empty else risk * float(_value(config, "risk_target_multiple"))
    )
    breakout_level = float(entry.get("breakout_level") or price)
    weekly = next_weekly_resistance(
        df, i, price, int(_value(config, "weekly_resistance_lookback"))
    )
    values = {
        "MEASURED_MOVE": breakout_level + range_height,
        "ATR_EXPANSION": price + atr * float(_value(config, "atr_target_multiple")),
        "RISK_MULTIPLE": price + risk * float(_value(config, "risk_target_multiple")),
        "NEXT_WEEKLY_RESISTANCE": weekly,
        # A nominal research objective accompanies the dynamic trailing exit.
        "TRAILING_ATR": price + max(
            atr * float(_value(config, "atr_target_multiple")),
            risk * float(_value(config, "partial_second_r")),
        ),
    }
    return {
        name: (round(float(value), precision) if value is not None else None)
        for name, value in values.items()
    }


def select_targets(
    df: pd.DataFrame,
    i: int,
    entry: dict,
    stop: float,
    config: Any,
) -> dict:
    model = str(_value(config, "target_model")).upper()
    if model not in TARGET_MODELS:
        raise ValueError(f"Unsupported breakout target model: {model}")
    price = float(entry["entry_price"])
    risk = price - float(stop)
    precision = int(_value(config, "price_precision"))
    candidates = target_candidates(df, i, entry, stop, config)
    selected = candidates.get(model)
    fallback_used = False
    if selected is None or selected <= price:
        selected = candidates["RISK_MULTIPLE"]
        fallback_used = model != "RISK_MULTIPLE"
    target1 = round(
        price + risk * float(_value(config, "partial_first_r")), precision
    )
    target2 = round(float(selected), precision)
    reward = target2 - price
    rr = reward / risk if risk > 0 else 0.0
    return {
        "model": model,
        "target1": target1,
        "target2": target2,
        "risk": risk,
        "reward": reward,
        "rr": round(rr, 3),
        "valid": risk > 0 and target2 > price,
        "fallback_used": fallback_used,
        "candidates": candidates,
        "partial_levels": [
            target1,
            round(price + risk * float(_value(config, "partial_second_r")), precision),
        ],
    }
