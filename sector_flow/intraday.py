"""Intraday sector liquidity: where the rest of today's turnover is heading.

Phase 3 established that next-*day* sector share is not predictable beyond a
5-session mean. This module answers the nearer question instead -- given the
opening window, how will the remainder of today's turnover be distributed -- and
it answers it with an explicit blend rather than a learned model.

The blend exists because neither input is sufficient alone. Over the clean
sessions available, the opening 30 minutes scores no better than yesterday's
full session, but the two combined beat both: the information is complementary.
An even weight is used deliberately and is not tuned, and the measured optimum
is flat between roughly 30% and 50% opening weight, so nothing here rests on a
knife edge.

Session completeness is not assumed. This collector runs on a desktop that is
not always switched on, so missing and half-captured sessions are the normal
condition, not an incident. A day is admitted on measured minute coverage or it
is excluded and reported.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from providers.symbol_mapping import to_engine_symbol


# EGX continuous trading in UTC, as stored by the Rubix collector
# (10:00-14:30 Africa/Cairo).
SESSION_OPEN = "07:00"
SESSION_CLOSE = "11:30"
OPENING_END = "07:30"

# A session is admitted only if it was observed for most of its length, was
# still being observed at the close, and covers the opening window the forecast
# is built from. The thresholds are deliberately blunt: they separate "the
# machine was running" from "it was not", nothing finer.
MIN_SESSION_MINUTES = 200
MIN_OPENING_MINUTES = 20
CLOSE_OBSERVED_BY = "11:25"

DEFAULT_BLEND_WEIGHT = 0.5
DEFAULT_TOP_K = 3
MINUTE_TABLE = "candles_1m"


def load_minute_turnover(database_path, sector_map):
    """Return per-minute sector turnover for the continuous session only."""

    query = (
        f"SELECT substr(minute, 1, 10) AS SessionDate, "
        f"substr(minute, 12, 5) AS Minute, ticker, high, low, close, volume "
        f"FROM {MINUTE_TABLE} WHERE volume > 0"
    )
    with sqlite3.connect(database_path) as connection:
        frame = pd.read_sql(query, connection)

    frame = frame[(frame["Minute"] >= SESSION_OPEN) & (frame["Minute"] <= SESSION_CLOSE)]
    frame["Sector"] = frame["ticker"].map(lambda value: sector_map.get(to_engine_symbol(value)))
    frame = frame.dropna(subset=["Sector"]).copy()
    # Same VWAP proxy as the daily history, so intraday and daily shares mean
    # the same thing and can be blended without a unit mismatch.
    frame["Turnover"] = (
        (frame["high"] + frame["low"] + frame["close"]) / 3.0 * frame["volume"]
    ).astype("float64")
    return frame[["SessionDate", "Minute", "ticker", "Sector", "Turnover"]]


def session_coverage(minutes):
    """Return per-session observation stats and whether the session is usable."""

    if minutes is None or minutes.empty:
        return pd.DataFrame()

    coverage = minutes.groupby("SessionDate").agg(
        SessionMinutes=("Minute", "nunique"),
        LastMinute=("Minute", "max"),
        FirstMinute=("Minute", "min"),
        Symbols=("ticker", "nunique"),
    )
    opening = minutes[minutes["Minute"] < OPENING_END]
    coverage["OpeningMinutes"] = (
        opening.groupby("SessionDate")["Minute"].nunique().reindex(coverage.index).fillna(0).astype(int)
    )
    coverage["Complete"] = (
        (coverage["SessionMinutes"] >= MIN_SESSION_MINUTES)
        & (coverage["LastMinute"] >= CLOSE_OBSERVED_BY)
        & (coverage["OpeningMinutes"] >= MIN_OPENING_MINUTES)
    )
    return coverage.reset_index()


def complete_sessions(minutes):
    """Drop sessions the collector did not observe end to end."""

    coverage = session_coverage(minutes)
    if coverage.empty:
        return minutes
    usable = set(coverage.loc[coverage["Complete"], "SessionDate"])
    return minutes[minutes["SessionDate"].isin(usable)]


def sector_shares(minutes, sectors=None):
    """Return a session x sector matrix of turnover shares summing to one."""

    if minutes is None or minutes.empty:
        return pd.DataFrame()
    grouped = minutes.groupby(["SessionDate", "Sector"])["Turnover"].sum().reset_index()
    total = grouped.groupby("SessionDate")["Turnover"].transform("sum")
    grouped["Share"] = (grouped["Turnover"] / total).where(total > 0)
    matrix = grouped.pivot(index="SessionDate", columns="Sector", values="Share")
    if sectors is not None:
        matrix = matrix.reindex(columns=sectors)
    return matrix.fillna(0.0).sort_index()


def opening_shares(minutes, sectors=None):
    """Sector shares of the opening window only."""

    return sector_shares(minutes[minutes["Minute"] < OPENING_END], sectors)


def rest_of_day_shares(minutes, sectors=None):
    """Sector shares after the opening window -- the quantity being forecast.

    The opening window is excluded from the target on purpose. It is part of the
    full session, so scoring an opening-based forecast against the full day is
    partly circular and flatters it.
    """

    return sector_shares(minutes[minutes["Minute"] >= OPENING_END], sectors)


def previous_daily_shares(daily_history, sessions, sectors=None):
    """Return each session's *previous* daily sector shares, keyed by session.

    The daily panel is used rather than the previous intraday session because it
    exists for every trading day, including the ones the collector missed.
    """

    if daily_history is None or daily_history.empty:
        return pd.DataFrame()

    panel = daily_history.pivot_table(
        index="SessionDate", columns="Sector", values="TurnoverShare"
    ).sort_index()
    panel.index = pd.to_datetime(panel.index).strftime("%Y-%m-%d")

    rows = {}
    for session in sessions:
        position = panel.index.get_indexer([session])[0]
        if position > 0:
            rows[session] = panel.iloc[position - 1]
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).T
    if sectors is not None:
        frame = frame.reindex(columns=sectors)
    return frame.fillna(0.0)


def blend(opening, previous, weight=DEFAULT_BLEND_WEIGHT):
    """Combine the opening window with yesterday, renormalised to sum to one."""

    if not 0.0 <= weight <= 1.0:
        raise ValueError(f"Blend weight must be within [0, 1]: {weight!r}")
    sessions = opening.index.intersection(previous.index)
    if sessions.empty:
        return pd.DataFrame()
    combined = (
        weight * opening.loc[sessions] + (1.0 - weight) * previous.loc[sessions]
    ).clip(lower=0.0)
    totals = combined.sum(axis=1)
    return combined.div(totals.where(totals > 0), axis=0)


def score(predicted, actual, top_k=DEFAULT_TOP_K):
    """Rank correlation, top-k overlap and MAE over the sessions both cover."""

    from scipy.stats import spearmanr

    sessions = predicted.index.intersection(actual.index)
    if sessions.empty:
        return {"sessions": 0, "rank_correlation": np.nan,
                f"top{top_k}_hit_rate": np.nan, "mae": np.nan}

    correlations, hits = [], []
    for session in sessions:
        row, target = predicted.loc[session], actual.loc[session]
        # A constant row has no ranking to correlate; skip it rather than let
        # scipy warn and return NaN.
        if row.nunique() < 2 or target.nunique() < 2:
            correlation = np.nan
        else:
            correlation = spearmanr(row, target).statistic
        if not np.isnan(correlation):
            correlations.append(float(correlation))
        hits.append(
            len(set(predicted.loc[session].nlargest(top_k).index)
                & set(actual.loc[session].nlargest(top_k).index)) / top_k
        )
    return {
        "sessions": len(sessions),
        "rank_correlation": round(float(np.mean(correlations)), 4) if correlations else np.nan,
        f"top{top_k}_hit_rate": round(float(np.mean(hits)), 4),
        "mae": round(float(np.abs(predicted.loc[sessions] - actual.loc[sessions]).stack().mean()), 6),
    }


def evaluate(minutes, daily_history, weight=DEFAULT_BLEND_WEIGHT, top_k=DEFAULT_TOP_K):
    """Score the blend against both of its own inputs on complete sessions."""

    usable = complete_sessions(minutes)
    if usable.empty:
        return pd.DataFrame()

    target = rest_of_day_shares(usable)
    sectors = target.columns
    opening = opening_shares(usable, sectors)
    previous = previous_daily_shares(daily_history, list(target.index), sectors)
    if previous.empty:
        return pd.DataFrame()

    rows = [
        {"predictor": "opening_window", **score(opening, target, top_k)},
        {"predictor": "previous_session", **score(previous, target, top_k)},
        {"predictor": f"blend_{weight:.0%}_opening",
         **score(blend(opening, previous, weight), target, top_k)},
    ]
    return pd.DataFrame(rows)


def weight_sweep(minutes, daily_history, weights=(0.0, 0.3, 0.5, 0.7, 1.0), top_k=DEFAULT_TOP_K):
    """Score a range of blend weights so the optimum's flatness is visible."""

    usable = complete_sessions(minutes)
    if usable.empty:
        return pd.DataFrame()
    target = rest_of_day_shares(usable)
    sectors = target.columns
    opening = opening_shares(usable, sectors)
    previous = previous_daily_shares(daily_history, list(target.index), sectors)
    if previous.empty:
        return pd.DataFrame()

    rows = []
    for value in weights:
        rows.append({"weight": value,
                     **score(blend(opening, previous, value), target, top_k)})
    return pd.DataFrame(rows)


def forecast_rest_of_day(minutes, daily_history, session=None,
                         weight=DEFAULT_BLEND_WEIGHT):
    """Return today's blended forecast of the remaining session's sector shares.

    Unlike ``evaluate``, this deliberately does *not* require a complete
    session: the point is to run while the session is still open, when the
    close has not happened yet. It requires only that the opening window was
    observed, and it reports how much of it was captured.
    """

    if minutes is None or minutes.empty:
        return pd.DataFrame()

    session = session or minutes["SessionDate"].max()
    today = minutes[minutes["SessionDate"] == session]
    coverage = session_coverage(today)
    observed = int(coverage["OpeningMinutes"].iloc[0]) if not coverage.empty else 0
    if observed < MIN_OPENING_MINUTES:
        return pd.DataFrame()

    opening = opening_shares(today)
    previous = previous_daily_shares(daily_history, [session], opening.columns)
    if previous.empty:
        return pd.DataFrame()

    combined = blend(opening, previous, weight)
    forecast = pd.DataFrame({
        "Sector": combined.columns,
        "OpeningShare": opening.loc[session].to_numpy(),
        "PreviousShare": previous.loc[session].to_numpy(),
        "Forecast": combined.loc[session].to_numpy(),
    })
    forecast["SessionDate"] = session
    forecast["OpeningMinutesObserved"] = observed
    forecast["Change"] = forecast["Forecast"] - forecast["PreviousShare"]
    return forecast.sort_values("Forecast", ascending=False).reset_index(drop=True)
