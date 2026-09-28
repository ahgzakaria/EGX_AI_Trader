"""What the Daily Dashboard leads with: breakouts, the one rule measured as real.

The dashboard's own rule was measured on its live calls (DASHBOARD_CALLS_VS_OUTCOMES.md):
ten sessions after each call, its BUYs had a median lift of -2.06% over the
median symbol and beat the market 44% of the time -- below the stocks it said to
avoid. A plain first close above the prior twenty-session high was the best
group in the same sessions (+0.96%, 55%), as it was over ten years of backtest
(CHART_PATTERNS.md, +2.67% and +1.27% lift in the two eras).

So the page leads with two lists built from the histories its scan already
loaded, and shows the old rule below them as a reference:

* **Buy signals** -- CONFIRMED_VOLUME_BREAKOUT, the breakout confirmed by volume
  with a measured stop and holding period. Evaluated by that strategy's own
  ``scan``: this module adds no second definition of it.
* **Watch list** -- every first close above the prior twenty-session high in a
  name liquid enough to trade, ranked by how strongly volume came in. A list to
  watch, not a buy signal: it is the unconfirmed version of the rule above.

Nothing here places an order.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

#: The prior high a close has to clear, in sessions.
BREAKOUT_WINDOW = 20

#: The volume ratio's baseline, in sessions before the breakout day.
VOLUME_WINDOW = 20


def liquidity_floor():
    """Median daily turnover a name must clear -- Swing Breakout's measured floor."""
    from services.swing_breakout import SwingConfig

    return SwingConfig().minimum_daily_turnover_egp


@dataclass(frozen=True)
class FreshBreakout:
    """One name whose close cleared its prior twenty-session high for the first time."""

    symbol: str
    session_date: str
    close: float
    prior_high: float
    above_percent: float
    volume_ratio: float
    median_turnover_egp: float


def histories_from_results(results):
    """``{ticker: daily frame}`` from the scan rows that carry one."""
    histories = {}
    for row in results or ():
        frame = row.get("Data") if hasattr(row, "get") else None
        ticker = row.get("Ticker") if hasattr(row, "get") else None
        if ticker and isinstance(frame, pd.DataFrame) and len(frame):
            histories[str(ticker)] = frame
    return histories


def confirmed_breakouts(histories, cfg=None):
    """CONFIRMED_VOLUME_BREAKOUT on these histories, by its own ``scan``."""
    from strategy_momentum_breakout.scan import scan

    return scan(histories=histories, cfg=cfg)


def fresh_breakout(frame, minimum_turnover):
    """A :class:`FreshBreakout` if the last bar is one, else ``None``.

    Reads bars up to the last one only. The level excludes today, or every close
    would break out of itself; "first" means yesterday's close had not cleared
    yesterday's level.
    """
    if frame is None or len(frame) < BREAKOUT_WINDOW + 2:
        return None
    close = frame["Close"].astype(float)
    high = frame["High"].astype(float)
    volume = frame["Volume"].astype(float)
    level = high.iloc[-BREAKOUT_WINDOW - 1:-1].max()
    previous_level = high.iloc[-BREAKOUT_WINDOW - 2:-2].max()
    today, yesterday = close.iloc[-1], close.iloc[-2]
    if not (pd.notna(today) and pd.notna(level) and today > level):
        return None
    if pd.notna(yesterday) and pd.notna(previous_level) and yesterday > previous_level:
        return None                                       # not the first close through
    turnover = (close * volume).iloc[-VOLUME_WINDOW - 1:-1]
    median_turnover = float(turnover.median())
    if not median_turnover >= float(minimum_turnover):
        return None
    average = float(turnover.mean())
    ratio = float(close.iloc[-1] * volume.iloc[-1] / average) if average > 0 else float("nan")
    return FreshBreakout(
        symbol="", session_date=str(frame.index[-1])[:10], close=float(today),
        prior_high=float(level), above_percent=float((today / level - 1.0) * 100.0),
        volume_ratio=ratio, median_turnover_egp=median_turnover)


def fresh_breakouts(histories, *, minimum_turnover=None, exclude=()):
    """Every liquid first close above the prior high, strongest volume first.

    ``exclude`` removes names already listed as buy signals, so one name is not
    shown twice. The order is a reading order: volume ratio was measured as the
    gate that makes a breakout hold, not as a ranking inside the ones that pass.
    """
    floor = liquidity_floor() if minimum_turnover is None else minimum_turnover
    skip = {str(s).upper() for s in exclude}
    found = []
    for symbol, frame in (histories or {}).items():
        if str(symbol).upper() in skip:
            continue
        hit = fresh_breakout(frame, floor)
        if hit is not None:
            found.append(FreshBreakout(**{**hit.__dict__, "symbol": str(symbol)}))
    found.sort(key=lambda b: (-(b.volume_ratio if pd.notna(b.volume_ratio) else -1.0),
                              b.symbol))
    return found


def as_frame(breakouts):
    return pd.DataFrame([b.__dict__ for b in breakouts])


__all__ = [
    "BREAKOUT_WINDOW",
    "FreshBreakout",
    "as_frame",
    "confirmed_breakouts",
    "fresh_breakout",
    "fresh_breakouts",
    "histories_from_results",
    "liquidity_floor",
]
