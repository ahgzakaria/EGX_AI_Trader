"""CONFIRMED_VOLUME_BREAKOUT -- seven conditions, all of them measured.

The rule, in one sentence: buy a liquid name in a long-term uptrend, on the day
it closes above its twenty-day high at the top of its range on two and a half
times its normal volume, at the next session's close, and hold it for twenty
sessions behind a stop under the base it broke.

What this strategy deliberately does *not* have, and why -- each of these is a
defect measured in the Daily Dashboard strategy it sits beside, documented in
`docs/audits/strategies/DAILY_STRATEGY_DIAGNOSIS.md`:

* **No score.** The Daily Dashboard sums seven components into a 0-118 scale
  whose correlation with the outcome is r = -0.032. A number that does not rank
  should not be presented as a ranking, and a threshold on it selects nothing.
  Here there are seven conditions and every one is a gate: a signal either is
  one or is not, and the reader is told which condition failed.
* **No confidence percentage.** It was computed from the same inputs as the
  score, so it was a second opinion from the same witness.
* **No invented target.** The Daily Dashboard places `Target1` at the twenty-bar
  resistance and `Target2` two ATR above it, which on a breakout puts the first
  target *below the entry* -- the stronger the breakout, the more negative the
  reward. This strategy exits on time or on the stop, so it never has to name a
  price it does not believe in.
* **No candle patterns.** Every pattern needs `Open`, and the `Open` this
  project can reach is carried forward from the previous close on 96-98% of
  bars. This module reads High, Low, Close and Volume, and nothing else.
* **No trailing stop.** On the shipped strategy the trail ends 390 of 484 trades
  at an average of -0.47%; here a stop tightened to 1.5 ATR turns the validation
  lift negative. Both are the same finding: a stop inside this market's daily
  noise is a fee, not a protection.
* **No contradictions between its own rules.** The Daily Dashboard rewards a
  breakout eight points in `entry.py` while `quality_filter.py` requires 3% of
  room below resistance, and measures that room off a *different* twenty-bar
  high than the one the targets are placed on -- the two disagree on 22.4% of
  bars. Every level here is computed once, in one place, from one window.

Look-ahead is prevented structurally. `measure` states every window in shifted
`rolling` form, so today can never break out of a high that includes itself, and
`evaluate` is a thin reader of that same table rather than a second
implementation of the arithmetic -- which is what
`tests/test_confirmed_volume_breakout.py` pins.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategy_momentum_breakout.config import BreakoutConfig, load as load_config


#: In the order they are evaluated. The first failure is the reject reason, so
#: a rejected row always says the most fundamental thing that was wrong with it
#: rather than the last thing checked.
GATES = (
    "PriceIntegrity",
    "Liquidity",
    "LongTermTrend",
    "Calm",
    "Breakout",
    "VolumeConfirmation",
    "ClosePosition",
)

REJECTION_REASONS = GATES + ("InsufficientHistory", "InvalidRisk")


def warmup_bars(cfg: BreakoutConfig) -> int:
    """Bars of history the rule needs before any of it means anything.

    The calm window is the longest and is shifted by one; EMA200 needs its own
    two hundred before it stops being mostly its seed.
    """
    return max(cfg.calm_window + 1, cfg.trend_warmup_bars,
               cfg.breakout_window + 1, cfg.turnover_window)


def measure(df: pd.DataFrame, cfg: BreakoutConfig | None = None) -> pd.DataFrame:
    """Every level and every gate, for every bar, in one vectorised pass.

    This is the strategy. `evaluate` reads a row out of it and formats the row;
    the backtest reads the whole thing. There is no second copy of the
    arithmetic to drift out of step with this one.
    """
    cfg = cfg or load_config()
    close, high, low = df["Close"], df["High"], df["Low"]
    volume, atr = df["Volume"], df["ATR"]

    out = pd.DataFrame(index=df.index)
    out["Close"] = close
    out["EMA200"] = df["EMA200"]

    # Closed *before* today: today cannot break out of a high that includes it.
    out["PriorHigh"] = high.rolling(cfg.breakout_window).max().shift(1)
    # The stop rides under the base, which does include today's low -- a stop is
    # a level you are willing to be wrong below, and today's low is part of it.
    out["BaseLow"] = low.rolling(cfg.stop_window).min()

    # The average includes today, matching `indicators.technical.VOLUME_RATIO`
    # and matching what the research measured. It makes the ratio harder to
    # clear than an average of the preceding bars alone would, which is the
    # conservative direction and the one already in the codebase.
    average_volume = volume.rolling(cfg.turnover_window).mean()
    out["VolumeRatio"] = np.where(average_volume > 0, volume / average_volume, 0.0)
    out["TurnoverEGP"] = (close * volume).rolling(cfg.turnover_window).mean()

    atr_percent = np.where(close > 0, atr / close * 100, np.inf)
    out["ATRPercent"] = atr_percent
    # The name against its own past year, shifted so today is not inside the
    # median it is being compared against.
    out["CalmReference"] = (
        pd.Series(atr_percent, index=df.index)
        .rolling(cfg.calm_window).median().shift(1))

    bar_range = (high - low)
    out["ClosePosition"] = np.where(bar_range > 0, (close - low) / bar_range, 0.0)

    out["StopLoss"] = (out["BaseLow"] - cfg.stop_atr_buffer * atr).round(
        cfg.price_precision)
    out["ReferenceRiskPercent"] = np.where(
        close > 0, (close - out["StopLoss"]) / close * 100, 0.0)

    # A session that moved further than EGX's daily price limit allows is a
    # corporate action or bad data, never a market move, and it is checked in
    # both directions: the untreated record's largest loss and its largest gain
    # were both of these. This project's prices are unadjusted, so a split
    # arrives as a collapse and a reverse split as a spike; either one inside
    # the windows the rule reads contaminates the twenty-day high and the volume
    # average, so no signal is taken while one is still in range.
    session_move = close.pct_change() * 100
    out["SessionMove"] = session_move
    corporate_action = session_move.abs() >= cfg.maximum_session_move_percent
    out["CorporateAction"] = corporate_action.fillna(False)
    contaminated_for = max(cfg.breakout_window, cfg.turnover_window,
                           cfg.stop_window)
    out["PriceIntegrity"] = ~(
        out["CorporateAction"].rolling(contaminated_for, min_periods=1)
        .max().astype(bool))

    out["Liquidity"] = out["TurnoverEGP"] >= cfg.minimum_turnover_egp
    out["LongTermTrend"] = close > out["EMA200"]
    out["Calm"] = out["ATRPercent"] <= out["CalmReference"]
    out["Breakout"] = close > out["PriorHigh"]
    out["VolumeConfirmation"] = out["VolumeRatio"] >= cfg.minimum_volume_ratio
    out["ClosePositionPassed"] = out["ClosePosition"] >= cfg.minimum_close_position

    warmup = warmup_bars(cfg)
    out["Warm"] = np.arange(len(df)) >= warmup
    # A stop at or above the close is not a stop. A breakout bar excludes it,
    # but it is checked rather than assumed: the neighbouring strategy's one
    # silent contradiction -- a target below the entry -- was exactly this shape.
    out["RiskValid"] = out["StopLoss"] < close

    passed = (out["PriceIntegrity"] & out["Liquidity"] & out["LongTermTrend"]
              & out["Calm"] & out["Breakout"] & out["VolumeConfirmation"]
              & out["ClosePositionPassed"])
    out["Passed"] = passed & out["Warm"] & out["RiskValid"]
    return out


def _checks(row) -> dict:
    return {
        "PriceIntegrity": bool(row["PriceIntegrity"]),
        "Liquidity": bool(row["Liquidity"]),
        "LongTermTrend": bool(row["LongTermTrend"]),
        "Calm": bool(row["Calm"]),
        "Breakout": bool(row["Breakout"]),
        "VolumeConfirmation": bool(row["VolumeConfirmation"]),
        "ClosePosition": bool(row["ClosePositionPassed"]),
    }


def evaluate(df: pd.DataFrame, i: int, cfg: BreakoutConfig | None = None,
             measured: pd.DataFrame | None = None) -> dict:
    """Decide bar `i` of one symbol's indicator frame.

    `df` is what `indicators.technical.calculate_indicators` produces, which is
    what both the backtest and the live scanner already hold. Pass `measured` to
    reuse a table computed once for the whole frame; omit it and one is built.
    """
    cfg = cfg or load_config()
    table = measure(df, cfg) if measured is None else measured
    row = table.iloc[i]

    if not bool(row["Warm"]):
        return {
            **_blank(cfg),
            "RejectReason": "InsufficientHistory",
            "Reasons": [f"Needs {warmup_bars(cfg)} bars of history; "
                        f"this is bar {i}."],
        }

    checks = _checks(row)
    calm_reference = row["CalmReference"]
    measurements = {
        "Close": _round(row["Close"], cfg),
        "PriorHigh": _round(row["PriorHigh"], cfg),
        "BaseLow": _round(row["BaseLow"], cfg),
        "VolumeRatio": round(float(row["VolumeRatio"]), 2),
        "ClosePosition": round(float(row["ClosePosition"]), 2),
        "TurnoverEGP": round(float(row["TurnoverEGP"]), 0),
        "ATRPercent": round(float(row["ATRPercent"]), 2),
        "CalmReference": (round(float(calm_reference), 2)
                          if pd.notna(calm_reference) else None),
        "EMA200": _round(row["EMA200"], cfg),
        "ReferenceRiskPercent": round(float(row["ReferenceRiskPercent"]), 2),
    }
    reasons = _explain(checks, measurements, cfg)
    stop_loss = float(row["StopLoss"])
    failed = [gate for gate in GATES if not checks[gate]]

    if not failed and not bool(row["RiskValid"]):
        return {
            **_blank(cfg),
            "Checks": checks,
            "Measurements": measurements,
            "StopLoss": stop_loss,
            "RejectReason": "InvalidRisk",
            "Reasons": reasons + [
                f"Stop {stop_loss} is not below the close "
                f"{measurements['Close']}."],
        }

    signal = "BUY" if bool(row["Passed"]) else "AVOID"
    return {
        "Strategy": cfg.strategy_name,
        "Signal": signal,
        "TechnicalSignal": signal,
        "RejectReason": None if signal == "BUY" else (failed[0] if failed else None),
        "Checks": checks,
        "Measurements": measurements,
        "Reasons": reasons,
        "EntryPlan": cfg.entry_mode,
        "StopLoss": stop_loss,
        "HoldingBars": cfg.holding_bars,
        "ReferenceRiskPercent": measurements["ReferenceRiskPercent"],
        "GatesPassed": sum(checks.values()),
        "GatesTotal": len(checks),
    }


def _round(value, cfg: BreakoutConfig) -> float:
    return round(float(value), cfg.price_precision)


def _blank(cfg: BreakoutConfig) -> dict:
    return {
        "Strategy": cfg.strategy_name,
        "Signal": "AVOID",
        "TechnicalSignal": "AVOID",
        "RejectReason": None,
        "Checks": {gate: False for gate in GATES},
        "Measurements": {},
        "Reasons": [],
        "EntryPlan": cfg.entry_mode,
        "StopLoss": 0.0,
        "HoldingBars": cfg.holding_bars,
        "ReferenceRiskPercent": 0.0,
        "GatesPassed": 0,
        "GatesTotal": len(GATES),
    }


def _explain(checks, m, cfg) -> list[str]:
    """One line per gate, carrying the number that decided it.

    A rejection that does not say what the failing value *was* sends the reader
    to the code. That is the whole reason `RR -0.05` reached the dashboard with
    nothing on the row to explain it.
    """
    verdict = {True: "PASS", False: "FAIL"}
    calm = m["CalmReference"]
    return [
        f"{verdict[checks['PriceIntegrity']]} Price integrity: no session in "
        f"the last {max(cfg.breakout_window, cfg.turnover_window, cfg.stop_window)} "
        f"bars moved more than {cfg.maximum_session_move_percent:.0f}% "
        f"(a corporate action in unadjusted prices)",
        f"{verdict[checks['Liquidity']]} Liquidity: "
        f"{m['TurnoverEGP']:,.0f} EGP/day vs {cfg.minimum_turnover_egp:,.0f} minimum",
        f"{verdict[checks['LongTermTrend']]} Long-term trend: "
        f"close {m['Close']} vs EMA200 {m['EMA200']}",
        f"{verdict[checks['Calm']]} Calm: ATR% {m['ATRPercent']} vs its own "
        f"{cfg.calm_window}-bar median {calm if calm is not None else 'n/a'}",
        f"{verdict[checks['Breakout']]} Breakout: close {m['Close']} vs the "
        f"{cfg.breakout_window}-bar high {m['PriorHigh']}",
        f"{verdict[checks['VolumeConfirmation']]} Volume: "
        f"{m['VolumeRatio']}x vs {cfg.minimum_volume_ratio}x minimum",
        f"{verdict[checks['ClosePosition']]} Close position: "
        f"{m['ClosePosition']} of the bar's range vs "
        f"{cfg.minimum_close_position} minimum",
    ]
