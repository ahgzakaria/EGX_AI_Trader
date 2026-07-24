"""Independent breakout entries and stop models.

All calculations use only rows at or before ``i``.  No Classic entry or stop
function is imported here.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


ENTRY_MODES = ("BREAKOUT_CLOSE", "RETEST_ENTRY", "FIRST_PULLBACK")
STOP_MODELS = (
    "ATR_STOP",
    "BREAKOUT_LOW",
    "RETEST_LOW",
    "EMA20_STOP",
    "RECENT_SWING_LOW",
    "SUPPORT_BUFFER",
)


def _value(config: Any, name: str):
    return getattr(config, name)


def previous_resistance(df: pd.DataFrame, i: int, lookback: int) -> float:
    if i <= 0:
        return float("nan")
    start = max(0, i - int(lookback))
    return float(df["High"].iloc[start:i].max())


def _breakout_at(df: pd.DataFrame, i: int, config: Any) -> tuple[bool, float]:
    if i < int(_value(config, "breakout_lookback")):
        return False, float("nan")
    level = previous_resistance(df, i, _value(config, "breakout_lookback"))
    return bool(float(df["Close"].iloc[i]) > level), level


def _recent_breakout(df: pd.DataFrame, i: int, config: Any):
    start = max(int(_value(config, "breakout_lookback")), i - int(_value(config, "retest_lookback")))
    for index in range(i - 1, start - 1, -1):
        passed, level = _breakout_at(df, index, config)
        if passed:
            return index, level
    return None, float("nan")


def evaluate_entry(df: pd.DataFrame, i: int, config: Any) -> dict:
    """Return a traceable candidate for the selected entry model."""

    mode = str(_value(config, "entry_mode")).upper()
    if mode not in ENTRY_MODES:
        raise ValueError(f"Unsupported breakout entry mode: {mode}")
    minimum = max(200, int(_value(config, "breakout_lookback")) + 2)
    if i < minimum:
        return {
            "eligible": False,
            "mode": mode,
            "reason": "INSUFFICIENT_HISTORY",
            "breakout_index": None,
            "breakout_level": None,
            "entry_price": None,
            "breakout_low": None,
            "retest_low": None,
            "features": {},
        }

    last = df.iloc[i]
    close = float(last["Close"])
    low = float(last["Low"])
    volume_ratio = float(last.get("VOLUME_RATIO", 0) or 0)
    atr = float(last.get("ATR", 0) or 0)
    current_range = float(last["High"] - last["Low"])
    direct_breakout, direct_level = _breakout_at(df, i, config)
    breakout_index = i if direct_breakout else None
    breakout_level = direct_level
    retest_low = None
    eligible = False
    reason = "NO_BREAKOUT_ENTRY"

    if mode == "BREAKOUT_CLOSE":
        eligible = direct_breakout
        reason = "BREAKOUT_CLOSE_CONFIRMED" if eligible else reason
    else:
        breakout_index, breakout_level = _recent_breakout(df, i, config)
        if breakout_index is not None:
            tolerance = float(_value(config, "retest_tolerance_percent")) / 100
            touched_level = low <= breakout_level * (1 + tolerance)
            held_level = close >= breakout_level
            retest_low = float(df["Low"].iloc[breakout_index + 1 : i + 1].min())
            if mode == "RETEST_ENTRY":
                eligible = touched_level and held_level
                reason = "RETEST_HELD" if eligible else "RETEST_NOT_CONFIRMED"
            else:
                ema20 = float(last.get("EMA20", 0) or 0)
                touched_ema = low <= ema20 * (1 + tolerance) if ema20 > 0 else False
                earlier = df.iloc[breakout_index + 1 : i]
                earlier_touch = False
                if not earlier.empty:
                    earlier_touch = bool((
                        earlier["Low"]
                        <= earlier["EMA20"] * (1 + tolerance)
                    ).any())
                eligible = held_level and (touched_level or touched_ema) and not earlier_touch
                reason = "FIRST_PULLBACK_HELD" if eligible else "FIRST_PULLBACK_NOT_CONFIRMED"

    consolidation_window = int(_value(config, "consolidation_window"))
    consolidation = df.iloc[max(0, i - consolidation_window) : i]
    consolidation_high = float(consolidation["High"].max()) if not consolidation.empty else close
    consolidation_low = float(consolidation["Low"].min()) if not consolidation.empty else close
    consolidation_width = (
        (consolidation_high - consolidation_low) / consolidation_low * 100
        if consolidation_low > 0 else 999.0
    )
    opening_range_high = last.get("OpeningRangeHigh")
    opening_range_breakout = (
        pd.notna(opening_range_high) and close > float(opening_range_high)
    )
    ema20 = float(last.get("EMA20", 0) or 0)
    continuation_tolerance = float(
        _value(config, "ema20_continuation_tolerance_percent")
    ) / 100
    features = {
        "previous_resistance_breakout": direct_breakout,
        "opening_range_breakout": bool(opening_range_breakout),
        "high_volume_breakout": bool(
            direct_breakout
            and volume_ratio >= float(_value(config, "minimum_volume_ratio"))
        ),
        "ema20_continuation": bool(
            ema20 > 0 and close > ema20 and low <= ema20 * (1 + continuation_tolerance)
        ),
        "consolidation_breakout": bool(
            direct_breakout
            and consolidation_width <= float(
                _value(config, "max_consolidation_width_percent")
            )
        ),
        "atr_expansion": bool(
            atr > 0
            and current_range >= atr * float(_value(config, "atr_expansion_multiple"))
        ),
        "higher_high_breakout": bool(
            float(last["High"]) > previous_resistance(
                df, i, _value(config, "breakout_lookback")
            )
        ),
        "retest_confirmed": bool(eligible and mode != "BREAKOUT_CLOSE"),
        "volume_ratio": volume_ratio,
        "consolidation_width_percent": consolidation_width,
    }
    return {
        "eligible": bool(eligible),
        "mode": mode,
        "reason": reason,
        "breakout_index": breakout_index,
        "breakout_level": float(breakout_level) if pd.notna(breakout_level) else None,
        "entry_price": close if eligible else None,
        "breakout_low": (
            float(df["Low"].iloc[breakout_index])
            if breakout_index is not None else None
        ),
        "retest_low": retest_low,
        "features": features,
    }


def stop_candidates(df: pd.DataFrame, i: int, entry: dict, config: Any) -> dict:
    """Calculate every requested stop model independently."""

    price = float(entry["entry_price"])
    last = df.iloc[i]
    atr = float(last["ATR"])
    precision = int(_value(config, "price_precision"))
    recent_start = max(0, i - int(_value(config, "recent_swing_window")) + 1)
    support_start = max(0, i - int(_value(config, "support_window")) + 1)
    recent_low = float(df["Low"].iloc[recent_start : i + 1].min())
    support = float(df["Low"].iloc[support_start : i + 1].min())
    breakout_low = float(entry.get("breakout_low") or last["Low"])
    retest_low = float(entry.get("retest_low") or breakout_low)
    candidates = {
        "ATR_STOP": price - atr * float(_value(config, "atr_stop_multiple")),
        "BREAKOUT_LOW": breakout_low,
        "RETEST_LOW": retest_low,
        "EMA20_STOP": float(last["EMA20"]) - atr * float(
            _value(config, "ema20_stop_buffer_atr")
        ),
        "RECENT_SWING_LOW": recent_low,
        "SUPPORT_BUFFER": support - atr * float(
            _value(config, "support_buffer_atr")
        ),
    }
    return {name: round(float(value), precision) for name, value in candidates.items()}


def select_stop(df: pd.DataFrame, i: int, entry: dict, config: Any) -> dict:
    model = str(_value(config, "stop_model")).upper()
    if model not in STOP_MODELS:
        raise ValueError(f"Unsupported breakout stop model: {model}")
    candidates = stop_candidates(df, i, entry, config)
    selected = candidates[model]
    valid = selected > 0 and selected < float(entry["entry_price"])
    return {
        "model": model,
        "stop": selected if valid else None,
        "valid": valid,
        "candidates": candidates,
    }
