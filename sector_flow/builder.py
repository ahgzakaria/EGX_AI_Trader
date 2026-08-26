"""Load the universe's daily candles and persist the sector liquidity history.

Market data is taken through ``core.data_provider.load_history`` only, so the
current-research routing (EODHD / validated local + Rubix Daily Bridge) and its
provenance metadata apply here exactly as they do for Scanner and Dashboard.
Per-symbol failures are isolated and reported: one unavailable ticker must never
abort a universe-wide build, but it must also never vanish silently, because a
missing symbol shifts every sector's share of market turnover.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import sqlite3

import pandas as pd

from core.data_provider import load_history, provider_purpose
from core.universe import UNIVERSE_SOURCE, active_engine_symbols
from decision_support.sector_analysis import load_sector_map
from sector_flow.history import (
    DEFAULT_BASELINE_WINDOW,
    DEFAULT_MIN_COVERAGE,
    DEFAULT_SHARE_LOOKBACK,
    complete_sessions,
    sector_history,
)


logger = logging.getLogger(__name__)

DEFAULT_DATABASE = "data/sector_flow.db"
PROGRESS_EVERY = 25
DEFAULT_PURPOSE = "dashboard"
HISTORY_TABLE = "sector_daily"
METADATA_TABLE = "sector_flow_builds"


def load_universe_frames(symbols, sector_map, purpose=DEFAULT_PURPOSE,
                         period=None, interval=None, min_bars=None):
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
                )
            except Exception as error:  # provider faults must not abort the batch
                logger.warning("sector flow: %s unavailable (%s)", symbol, error)
                outcomes.append({
                    "Ticker": symbol, "Sector": sector, "Status": "UNAVAILABLE",
                    "Provider": None, "Bars": 0, "Detail": str(error),
                })
                continue

            metadata = frame.attrs.get("market_data", {})
            frames[symbol] = frame
            outcomes.append({
                "Ticker": symbol,
                "Sector": sector,
                "Status": "LOADED",
                "Provider": metadata.get("effective_provider"),
                "Bars": len(frame),
                "Detail": metadata.get("freshness_status") or "",
            })
    return frames, pd.DataFrame(outcomes)


def build(universe_path=None, sector_file="data/sectors.csv",
          purpose=DEFAULT_PURPOSE, method="typical", period=None, interval=None,
          min_bars=None, window=DEFAULT_BASELINE_WINDOW,
          share_lookback=DEFAULT_SHARE_LOOKBACK):
    """Build the per-sector daily liquidity history for the whole universe."""

    sector_map = load_sector_map(sector_file)
    if not sector_map:
        raise ValueError(
            f"No sector map at {sector_file}. Run scripts/build_sector_map.py first."
        )
    symbols = list(active_engine_symbols(universe_path))
    frames, outcomes = load_universe_frames(
        symbols, sector_map, purpose=purpose, period=period,
        interval=interval, min_bars=min_bars,
    )
    history = sector_history(
        frames, sector_map, method=method, window=window, share_lookback=share_lookback,
    )
    loaded = outcomes[outcomes["Status"] == "LOADED"]
    metadata = {
        "built_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "purpose": purpose,
        "universe_source": str(universe_path or UNIVERSE_SOURCE),
        "turnover_method": method,
        "baseline_window": window,
        "share_lookback": share_lookback,
        "universe_symbols": len(symbols),
        "classified_symbols": int((outcomes["Status"] != "UNCLASSIFIED").sum()),
        "loaded_symbols": len(loaded),
        "unavailable_symbols": int((outcomes["Status"] == "UNAVAILABLE").sum()),
        "unclassified_symbols": int((outcomes["Status"] == "UNCLASSIFIED").sum()),
        "providers": loaded["Provider"].value_counts().to_dict() if not loaded.empty else {},
        "sectors": int(history["Sector"].nunique()) if not history.empty else 0,
        "sessions": int(history["SessionDate"].nunique()) if not history.empty else 0,
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
