"""Per-sector daily liquidity history built from completed daily candles.

Turnover is a *proxy* for EGX value traded: the exchange reports value from
intraday VWAP, while the daily candle contract carries only OHLCV. The proxy is
stated explicitly so every downstream number stays auditable -- nothing here
claims to be the exchange's own value-traded figure.

Rolling baselines are strictly trailing (they exclude the current session), so a
row describes today measured against the sessions before it and never against
itself.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


TURNOVER_METHODS = ("typical", "close")
DEFAULT_BASELINE_WINDOW = 20
DEFAULT_SHARE_LOOKBACK = 5
# A session reporting far fewer symbols than usual is still in progress (or was
# a partial trading day). Its turnover shares are computed over a fraction of
# the panel and must not be read as a sector ranking.
DEFAULT_MIN_COVERAGE = 0.6


def turnover_series(frame, method="typical"):
    """Return the per-session turnover proxy for one symbol's daily candles."""

    if method not in TURNOVER_METHODS:
        raise ValueError(f"Unknown turnover method: {method!r}")
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.Series(dtype="float64")
    missing = {"High", "Low", "Close", "Volume"} - set(frame.columns)
    if missing:
        raise ValueError(f"Daily frame is missing required columns: {sorted(missing)}")

    if method == "typical":
        price = (frame["High"] + frame["Low"] + frame["Close"]) / 3.0
    else:
        price = frame["Close"]
    return (price.astype("float64") * frame["Volume"].astype("float64")).rename("Turnover")


def symbol_rows(symbol, sector, frame, method="typical"):
    """Return tidy per-session rows for one symbol, or an empty frame."""

    turnover = turnover_series(frame, method)
    if turnover.empty:
        return pd.DataFrame()

    close = frame["Close"].astype("float64")
    rows = pd.DataFrame({
        "SessionDate": pd.to_datetime(frame.index).normalize(),
        "Sector": sector,
        "Ticker": symbol,
        "Turnover": turnover.to_numpy(),
        "Return": close.pct_change().to_numpy(),
    })
    return rows.dropna(subset=["Turnover"])


def aggregate_sectors(symbol_frames, sector_map, method="typical"):
    """Return one tidy row per (SessionDate, Sector) with turnover and breadth.

    ``symbol_frames`` maps an engine ticker to its completed daily candles.
    Symbols absent from ``sector_map`` are dropped rather than bucketed into a
    synthetic sector -- an unclassified ticker must not distort sector shares.
    """

    collected = []
    for symbol, frame in (symbol_frames or {}).items():
        sector = sector_map.get(str(symbol).upper())
        if not sector:
            continue
        rows = symbol_rows(symbol, sector, frame, method)
        if not rows.empty:
            collected.append(rows)

    if not collected:
        return pd.DataFrame(columns=[
            "SessionDate", "Sector", "Turnover", "TurnoverShare", "MarketTurnover",
            "MarketSymbols", "Symbols", "Advancers", "Decliners", "Breadth", "MeanReturn",
        ])

    tidy = pd.concat(collected, ignore_index=True)
    grouped = tidy.groupby(["SessionDate", "Sector"], sort=True).agg(
        Turnover=("Turnover", "sum"),
        Symbols=("Ticker", "nunique"),
        Advancers=("Return", lambda values: int((values > 0).sum())),
        Decliners=("Return", lambda values: int((values < 0).sum())),
        MeanReturn=("Return", "mean"),
    ).reset_index()

    market = grouped.groupby("SessionDate")["Turnover"].transform("sum")
    grouped["MarketTurnover"] = market
    grouped["MarketSymbols"] = grouped.groupby("SessionDate")["Symbols"].transform("sum")
    grouped["TurnoverShare"] = (grouped["Turnover"] / market).where(market > 0)
    moved = grouped["Advancers"] + grouped["Decliners"]
    grouped["Breadth"] = ((grouped["Advancers"] - grouped["Decliners"]) / moved).where(moved > 0)
    return grouped


def add_flow_features(aggregated, window=DEFAULT_BASELINE_WINDOW,
                      share_lookback=DEFAULT_SHARE_LOOKBACK):
    """Attach trailing liquidity features to a tidy sector history.

    Every baseline is shifted by one session, so ``TurnoverZ`` and ``RVOL``
    compare the current session against the ``window`` sessions that preceded
    it and never against a window containing the value being scored.
    """

    if aggregated is None or aggregated.empty:
        return aggregated

    # A unique index is required: the rolling baselines below are re-aligned by
    # label after grouping, and duplicate labels would scatter them.
    frame = aggregated.sort_values(["Sector", "SessionDate"]).reset_index(drop=True)
    positive = frame["Turnover"].where(frame["Turnover"] > 0)
    frame["LogTurnover"] = np.log(positive.astype("float64"))

    grouped = frame.groupby("Sector", sort=False)["LogTurnover"]
    baseline = grouped.shift(1)
    mean = baseline.groupby(frame["Sector"], sort=False).rolling(window, min_periods=window).mean()
    std = baseline.groupby(frame["Sector"], sort=False).rolling(window, min_periods=window).std()
    median = baseline.groupby(frame["Sector"], sort=False).rolling(window, min_periods=window).median()
    frame["BaselineLogTurnover"] = mean.reset_index(level=0, drop=True)
    baseline_std = std.reset_index(level=0, drop=True)
    baseline_median = median.reset_index(level=0, drop=True)

    frame["TurnoverZ"] = (
        (frame["LogTurnover"] - frame["BaselineLogTurnover"]) / baseline_std.where(baseline_std > 0)
    )
    frame["RVOL"] = np.exp(frame["LogTurnover"] - baseline_median)

    frame["SessionCoverage"] = _session_coverage(frame, window)

    share = frame.groupby("Sector", sort=False)["TurnoverShare"]
    frame["ShareChange"] = frame["TurnoverShare"] - share.shift(share_lookback)
    frame["SharePrev"] = share.shift(1)
    frame["ShareRank"] = frame.groupby("SessionDate", sort=False)["TurnoverShare"].rank(
        ascending=False, method="min"
    )
    return frame.reset_index(drop=True)


def _session_coverage(frame, window):
    """Return each row's session coverage: reporting symbols vs the recent norm."""

    sessions = frame.groupby("SessionDate", sort=True)["MarketSymbols"].first()
    baseline = sessions.shift(1).rolling(window, min_periods=1).median()
    coverage = sessions / baseline.where(baseline > 0)
    if not coverage.empty:
        # The first session has nothing to be measured against.
        coverage.iloc[0] = 1.0
    return frame["SessionDate"].map(coverage)


def complete_sessions(history, min_coverage=DEFAULT_MIN_COVERAGE):
    """Drop sessions whose reporting panel is far below the recent norm."""

    if history is None or history.empty or "SessionCoverage" not in history:
        return history
    return history[history["SessionCoverage"].fillna(1.0) >= float(min_coverage)]


def sector_history(symbol_frames, sector_map, method="typical",
                   window=DEFAULT_BASELINE_WINDOW, share_lookback=DEFAULT_SHARE_LOOKBACK):
    """Aggregate daily candles into a feature-bearing per-sector history."""

    return add_flow_features(
        aggregate_sectors(symbol_frames, sector_map, method),
        window=window,
        share_lookback=share_lookback,
    )


def latest_snapshot(history, min_coverage=DEFAULT_MIN_COVERAGE):
    """Return the newest *complete* session's rows, ranked by turnover share.

    An in-progress session is skipped by default: with only part of the panel
    reporting, its shares describe the symbols that happen to have printed so
    far, not where the day's liquidity actually went.
    """

    if history is None or history.empty:
        return pd.DataFrame()
    usable = complete_sessions(history, min_coverage)
    if usable is None or usable.empty:
        return pd.DataFrame()
    snapshot = usable[usable["SessionDate"] == usable["SessionDate"].max()]
    return snapshot.sort_values("TurnoverShare", ascending=False).reset_index(drop=True)


def rotation_matrix(history, value="TurnoverShare", sessions=60,
                    min_coverage=DEFAULT_MIN_COVERAGE):
    """Return a session x sector matrix, newest sessions last, for heatmaps."""

    if history is None or history.empty:
        return pd.DataFrame()
    history = complete_sessions(history, min_coverage)
    if history is None or history.empty:
        return pd.DataFrame()
    matrix = history.pivot_table(
        index="SessionDate", columns="Sector", values=value, aggfunc="last"
    ).sort_index()
    return matrix.tail(int(sessions)) if sessions else matrix
