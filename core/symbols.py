"""Validated symbol loading shared by live scans, watchlists and backtests."""

from pathlib import Path

import pandas as pd


def load_symbols(source):
    """Return unique, non-empty tickers from a CSV path or iterable.

    Keeping this at the application boundary prevents dashboard, optimizer and
    backtest from silently using different symbol-cleaning rules.
    """
    if isinstance(source, (str, Path)):
        try:
            frame = pd.read_csv(source)
        except (OSError, pd.errors.ParserError) as error:
            raise ValueError(f"Unable to read symbols source: {error}") from error

        if "Ticker" not in frame.columns:
            raise ValueError("Symbols file must contain a 'Ticker' column")
        values = frame["Ticker"].tolist()
    else:
        try:
            values = list(source)
        except TypeError as error:
            raise ValueError("Symbols source must be a CSV path or iterable") from error

    symbols = []
    seen = set()
    for value in values:
        symbol = str(value).strip() if pd.notna(value) else ""
        if symbol and symbol not in seen:
            symbols.append(symbol)
            seen.add(symbol)

    return symbols


def load_active_symbols(source):
    """Return the canonical universe, honoring an optional Active flag.

    Existing Scanner/Backtest callers remain on ``load_symbols`` unchanged.
    The external collector alone uses this additive deployment filter. When
    the canonical CSV has no Active column, its curated rows are treated as
    the active universe and no symbol is silently removed.
    """

    if not isinstance(source, (str, Path)):
        return load_symbols(source)
    try:
        frame = pd.read_csv(source)
    except (OSError, pd.errors.ParserError) as error:
        raise ValueError(f"Unable to read symbols source: {error}") from error
    if "Ticker" not in frame.columns:
        raise ValueError("Symbols file must contain a 'Ticker' column")
    if "Active" in frame.columns:
        active = frame["Active"].astype(str).str.strip().str.lower().isin(
            {"1", "true", "yes", "y", "active"}
        )
        frame = frame[active]
    return load_symbols(frame["Ticker"].tolist())
