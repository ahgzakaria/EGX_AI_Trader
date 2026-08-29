"""Daily closes for the symbols a run traded, so a drawdown can be honest.

`backtesting/equity.py` was given only the trade list, so the only curve it could
build was one that steps at each exit and books profit there. That is not a
portfolio's equity: it is the realised part of one, and it cannot fall while a
position is open and losing.

Marking to market needs prices, and prices are the one thing the statistics layer
never had. This is the smallest thing that gives it them: the same
`load_history(purpose="backtest")` the engine itself reads, for the traded
symbols only, pivoted into one frame of closes.

It is deliberately tolerant. A symbol that cannot be loaded is left out and the
positions in it are carried at cost rather than the whole curve being refused —
a partial mark is closer to the truth than no mark, and `EquityCurve` reports
which basis it used either way.
"""

from __future__ import annotations

import pandas as pd


def daily_closes(symbols, loader=None) -> pd.DataFrame:
    """One frame of daily closes: rows are sessions, columns are symbols.

    `loader` exists so a test can supply prices without a provider; production
    passes nothing and gets the backtest provider.
    """
    symbols = sorted({str(symbol) for symbol in symbols if symbol})
    if not symbols:
        return pd.DataFrame()

    if loader is None:
        from core.data_provider import load_history, provider_purpose

        def loader(symbol):
            with provider_purpose("backtest"):
                return load_history(symbol, purpose="backtest")

    columns = {}
    for symbol in symbols:
        try:
            frame = loader(symbol)
        except Exception:                              # noqa: BLE001 - skipped
            continue
        if frame is None or len(frame) == 0 or "Close" not in frame:
            continue
        series = frame["Close"]
        series.index = pd.to_datetime(series.index)
        # A symbol can carry duplicate session rows after a provider merge;
        # the last one written for a date is the one the engine used.
        columns[symbol] = series[~series.index.duplicated(keep="last")]

    if not columns:
        return pd.DataFrame()
    return pd.DataFrame(columns).sort_index()


def closes_for_trades(trades, loader=None) -> pd.DataFrame:
    """Closes for exactly the symbols in `trades`, and nothing else."""
    return daily_closes({getattr(t, "symbol", "") for t in trades or ()}, loader)
