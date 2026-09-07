"""Load the universe's daily candles and persist the sector liquidity history.

Market data is taken through ``core.data_provider.load_history`` only, so the
current-research routing (EODHD / validated local + Rubix Daily Bridge) and its
provenance metadata apply here exactly as they do for Scanner and Dashboard.
Per-symbol failures are isolated and reported: one unavailable ticker must never
abort a universe-wide build, but it must also never vanish silently, because a
missing symbol shifts every sector's share of market turnover.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import sqlite3

import pandas as pd

from core.data_provider import load_history, provider_purpose
from sector_flow import DEFAULT_DATABASE, HISTORY_TABLE, METADATA_TABLE
from core.universe import UNIVERSE_SOURCE, active_engine_symbols
from decision_support.sector_analysis import load_sector_map
from sector_flow.history import (
    DEFAULT_BASELINE_WINDOW,
    DEFAULT_MIN_COVERAGE,
    DEFAULT_MIN_COVERAGE,
    DEFAULT_SHARE_LOOKBACK,
    complete_sessions,
    sector_history,
)


from core.symbols import foreign_quoted_symbols
from sector_flow.measured_turnover import attach as attach_measured_turnover

logger = logging.getLogger(__name__)

PROGRESS_EVERY = 25
DEFAULT_PURPOSE = "dashboard"
# The configured 250-bar minimum exists because *indicators* need history --
# moving averages and momentum cannot be computed without it. Turnover
# aggregation needs none: a symbol listed three months ago still traded real
# value on those days, and excluding it understates its sector's share on
# exactly the sessions a new listing is busiest. Measured on 2026-08-26, the
# 250-bar rule was dropping 2.28% of the market's turnover over 30 sessions,
# 3.07bn EGP of it from a single new listing. 20 bars is enough to be a real
# trading record.
TURNOVER_MIN_BARS = 20


def load_universe_frames(symbols, sector_map, purpose=DEFAULT_PURPOSE,
                         period=None, interval=None, min_bars=TURNOVER_MIN_BARS):
    """Return ``{ticker: daily frame}`` plus per-symbol load outcomes."""

    frames = {}
    outcomes = []
    total = len(symbols)
    with provider_purpose(purpose):
        for position, symbol in enumerate(symbols, start=1):
            if position % PROGRESS_EVERY == 0 or position == total:
                logger.info("sector flow: %s/%s symbols processed", position, total)
            sector = sector_map.get(str(symbol).upper())
            if not sector:
                outcomes.append({
                    "Ticker": symbol, "Sector": None, "Status": "UNCLASSIFIED",
                    "Provider": None, "Bars": 0, "Detail": "Absent from the sector map.",
                })
                continue
            try:
                frame = load_history(
                    symbol, period=period, interval=interval, purpose=purpose,
                    min_bars=min_bars,
                    # This history describes where turnover went; it places no
                    # order and feeds no automatic entry. A symbol held for
                    # manual review still traded real value, and omitting it
                    # understates its sector's share of the market.
                    allow_held=True,
                )
            except Exception as error:  # provider faults must not abort the batch
                logger.warning("sector flow: %s unavailable (%s)", symbol, error)
                outcomes.append({
                    "Ticker": symbol, "Sector": sector, "Status": "UNAVAILABLE",
                    "Provider": None, "Bars": 0, "Detail": str(error),
                })
                continue

            metadata = frame.attrs.get("market_data", {})
            # Where the exchange's own turnover is on hand, use it instead of
            # deriving one from price and volume. Enrichment only: a symbol or
            # a session the store does not cover keeps the estimate, and a
            # machine with no store at all builds exactly as before.
            frame = attach_measured_turnover(frame, symbol)
            measured = int(frame["Turnover"].gt(0).sum()) if "Turnover" in frame else 0
            frames[symbol] = frame
            outcomes.append({
                "Ticker": symbol,
                "Sector": sector,
                "Status": "LOADED_HELD" if not metadata.get(
                    "automatic_use_permitted", True) else "LOADED",
                "Provider": metadata.get("effective_provider"),
                "Bars": len(frame),
                "MeasuredTurnoverBars": measured,
                "Detail": metadata.get("freshness_status") or "",
            })
    return frames, pd.DataFrame(outcomes)


def build(universe_path=None, sector_file="data/sectors.csv",
          purpose=DEFAULT_PURPOSE, method="typical", period=None, interval=None,
          min_bars=TURNOVER_MIN_BARS, window=DEFAULT_BASELINE_WINDOW,
          share_lookback=DEFAULT_SHARE_LOOKBACK):
    """Build the per-sector daily liquidity history for the whole universe."""

    sector_map = load_sector_map(sector_file)
    if not sector_map:
        raise ValueError(
            f"No sector map at {sector_file}. Run scripts/build_sector_map.py first."
        )
    # The tradeable set, not the listed one.
    #
    # Twelve symbols are quoted in dollars or euros while their turnover is
    # reported in pounds, so the estimate this build used to rely on counted a
    # dollar figure as pounds and understated them by about fifty. The measured
    # turnover fixes the number -- but a sector ranking exists to answer which
    # sector to trade, and a rank driven by a symbol this account cannot settle
    # answers a question nobody asked.
    #
    # active_engine_symbols stays what it is: the exchange's listing, held to a
    # validated snapshot, and what the collector subscribes to.
    excluded = {str(symbol).upper() for symbol in foreign_quoted_symbols()}
    symbols = [symbol for symbol in active_engine_symbols(universe_path)
               if str(symbol).upper() not in excluded]
    frames, outcomes = load_universe_frames(
        symbols, sector_map, purpose=purpose, period=period,
        interval=interval, min_bars=min_bars,
    )
    history = sector_history(
        frames, sector_map, method=method, window=window, share_lookback=share_lookback,
    )
    loaded = outcomes[outcomes["Status"].isin(["LOADED", "LOADED_HELD"])]
    metadata = {
        "built_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "purpose": purpose,
        "universe_source": str(universe_path or UNIVERSE_SOURCE),
        "turnover_method": method,
        "min_bars": min_bars,
        "baseline_window": window,
        "share_lookback": share_lookback,
        "universe_symbols": len(symbols),
        "classified_symbols": int((outcomes["Status"] != "UNCLASSIFIED").sum()),
        "loaded_symbols": len(loaded),
        "unavailable_symbols": int((outcomes["Status"] == "UNAVAILABLE").sum()),
        # Symbols whose tier holds them for manual review but whose turnover is
        # real and belongs in the market total. Counted separately so a reader
        # can always see how much of a sector's share rests on held data.
        "held_symbols": int((outcomes["Status"] == "LOADED_HELD").sum()),
        "held_tickers": sorted(outcomes.loc[outcomes["Status"] == "LOADED_HELD", "Ticker"]),
        "unclassified_symbols": int((outcomes["Status"] == "UNCLASSIFIED").sum()),
        "providers": loaded["Provider"].value_counts().to_dict() if not loaded.empty else {},
        "sectors": int(history["Sector"].nunique()) if not history.empty else 0,
        "sessions": int(history["SessionDate"].nunique()) if not history.empty else 0,
        # Which completed session this build was reaching for. EODHD publishes a
        # session a day late and sometimes two, so a build can be correct and
        # still not advance. Recording the target lets a scheduled refresh tell
        # "the provider has not published yet" from "nobody has rebuilt".
        "attempted_for_session": str(_expected_session() or ""),
        "first_session": str(history["SessionDate"].min().date()) if not history.empty else None,
        "last_session": str(history["SessionDate"].max().date()) if not history.empty else None,
    }
    # Sessions where only part of the panel reported (an in-progress day, or a
    # partial trading day) are kept but marked, never silently ranked.
    usable = complete_sessions(history, DEFAULT_MIN_COVERAGE)
    metadata["min_coverage"] = DEFAULT_MIN_COVERAGE
    metadata["complete_sessions"] = int(usable["SessionDate"].nunique()) if not history.empty else 0
    metadata["last_complete_session"] = (
        str(usable["SessionDate"].max().date()) if usable is not None and not usable.empty else None
    )
    return history, outcomes, metadata


def save(history, metadata, database_path=DEFAULT_DATABASE):
    """Replace the stored history and append this build's provenance row."""

    stored = history.copy()
    stored["SessionDate"] = pd.to_datetime(stored["SessionDate"]).dt.strftime("%Y-%m-%d")
    with sqlite3.connect(database_path) as connection:
        stored.to_sql(HISTORY_TABLE, connection, if_exists="replace", index=False)
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS {METADATA_TABLE} ("
            "built_at TEXT PRIMARY KEY, metadata_json TEXT NOT NULL)"
        )
        connection.execute(
            f"INSERT OR REPLACE INTO {METADATA_TABLE} VALUES (?, ?)",
            (metadata["built_at"], json.dumps(metadata, sort_keys=True)),
        )
    return database_path


def _expected_session():
    from core.research_router import _expected_completed_session
    return _expected_completed_session()


def stored_latest_session(database_path=DEFAULT_DATABASE, min_coverage=DEFAULT_MIN_COVERAGE):
    """Return the newest complete session in the store, or ``None``.

    Reads one row rather than the whole history: a scheduled refresh asks this
    on every run, including the many runs where the answer is "nothing to do".
    """

    query = (
        f"SELECT MAX(SessionDate) FROM {HISTORY_TABLE} "
        f"WHERE COALESCE(SessionCoverage, 1) >= ?"
    )
    try:
        with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True) as connection:
            row = connection.execute(query, (min_coverage,)).fetchone()
    except sqlite3.Error:
        return None
    return pd.Timestamp(row[0]).date() if row and row[0] else None


def sessions_behind(database_path=DEFAULT_DATABASE):
    """How many completed EGX sessions the store is missing.

    Zero means a rebuild would re-derive what is already there. The daily
    refresh uses this to skip a twenty-minute rebuild on the many days -- every
    weekend and holiday among them -- when nothing has completed since the last
    one.

    The empty store is checked first, and the order is the whole point. "I could
    not work out which session is expected" and "there is nothing stored" are
    different answers, and asking them the other way round reported an empty
    store as zero sessions behind whenever the expected session was unavailable
    -- a full build required, announced as nothing to do.
    """

    from core.egx_calendar import effective_holidays
    from core.egx_session import is_regular_trading_day

    stored = stored_latest_session(database_path)
    if stored is None:
        return None                      # nothing stored: a full build is required
    expected = _expected_session()
    if expected is None:
        return 0                         # nothing known to be missing
    holidays = effective_holidays()
    missing, cursor = 0, stored + timedelta(days=1)
    while cursor <= expected:
        if is_regular_trading_day(cursor, holidays):
            missing += 1
        cursor += timedelta(days=1)
    return missing


def load_saved(database_path=DEFAULT_DATABASE):
    """Return the stored sector history, or an empty frame when absent."""

    try:
        with sqlite3.connect(database_path) as connection:
            frame = pd.read_sql(f"SELECT * FROM {HISTORY_TABLE}", connection)
    except (sqlite3.DatabaseError, pd.errors.DatabaseError):
        return pd.DataFrame()
    if not frame.empty:
        frame["SessionDate"] = pd.to_datetime(frame["SessionDate"])
    return frame


def latest_build_metadata(database_path=DEFAULT_DATABASE):
    """Return the provenance of the most recent build, or ``{}``."""

    try:
        with sqlite3.connect(database_path) as connection:
            row = connection.execute(
                f"SELECT metadata_json FROM {METADATA_TABLE} ORDER BY built_at DESC LIMIT 1"
            ).fetchone()
    except sqlite3.DatabaseError:
        return {}
    return json.loads(row[0]) if row else {}
