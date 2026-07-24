"""Read-only operational bridge to the audited TickerChart SQLite adapter.

The external collector owns the private wire protocol.  This provider only
consumes its documented SQLite output in read-only mode, so EGX AI Trader never
duplicates authentication, WebSocket, or protocol parsing code.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import pandas as pd

from core.egx_session import assess_quote_freshness, egx_session_phase
from providers.base_provider import (
    MarketDataProvider,
    ProviderConfigurationError,
    ProviderConnectionError,
    ProviderDataError,
    ProviderSchemaError,
    normalize_history,
)
from providers.symbol_mapping import to_tickerchart_symbol


CAIRO = ZoneInfo("Africa/Cairo")


class TickerChartProvider(MarketDataProvider):
    """Serve delayed TickerChart candles from the adapter's SQLite database."""

    name = "tickerchart"
    delayed = True
    delay_minutes = 15

    ACTIVE = "TICKERCHART_ACTIVE"
    DELAYED = "TICKERCHART_DELAYED"
    STALE = "TICKERCHART_STALE"
    UNAVAILABLE = "TICKERCHART_UNAVAILABLE"
    WAITING = "TICKERCHART_WAITING_FOR_SESSION"

    def __init__(
        self,
        db_path=None,
        adapter_path=None,
        local_url=None,
        stale_after_minutes=1440,
        history_loader=None,
        now=None,
        **_legacy,
    ):
        self.db_path = Path(
            db_path or os.getenv("TICKERCHART_DB_PATH", "")
        ).expanduser() if (db_path or os.getenv("TICKERCHART_DB_PATH")) else None
        self.adapter_path = Path(
            adapter_path or os.getenv("TICKERCHART_ADAPTER_PATH", "")
        ).expanduser() if (adapter_path or os.getenv("TICKERCHART_ADAPTER_PATH")) else None
        # Reserved for a future documented localhost service.  The audited
        # adapter currently exposes SQLite, not HTTP.
        self.local_url = local_url or os.getenv("TICKERCHART_LOCAL_URL", "")
        self.stale_after_minutes = max(1, int(stale_after_minutes))
        self._history_loader = history_loader
        self._now = now or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def map_symbol(symbol):
        """Compatibility alias backed by the centralized mapping layer."""

        return to_tickerchart_symbol(symbol)

    def discover_adapter(self):
        """Validate the collector layout without importing or executing it."""

        if self.adapter_path is None:
            return {
                "available": False,
                "reason": "TICKERCHART_ADAPTER_PATH is not configured",
            }
        required = ("adapter.py", "protocol.py", "storage.py")
        missing = [name for name in required if not (self.adapter_path / name).is_file()]
        return {
            "available": not missing,
            "path": str(self.adapter_path),
            "missing": missing,
            "reason": None if not missing else f"missing adapter files: {', '.join(missing)}",
        }

    def health(self):
        """Return a truthful state; never report active without usable quotes."""

        base = {
            "provider": self.name,
            "delayed": True,
            "delay_minutes": 15,
            "adapter": self.discover_adapter(),
            "database_path": str(self.db_path) if self.db_path else None,
        }
        try:
            snapshot = self._database_snapshot()
        except (ProviderConfigurationError, ProviderConnectionError, ProviderSchemaError) as error:
            waiting = self._waiting_session_snapshot()
            if waiting is not None:
                base.update(waiting)
                base.update({
                    "status": self.WAITING,
                    "operational_state": self.WAITING,
                    "connection_state": self.WAITING,
                    "authenticated_session_available": True,
                    "schema_valid": True,
                    "reason": (
                        f"collector connected; EGX phase is {waiting['session_phase']}; "
                        "waiting for first quote"
                    ),
                })
                return base
            base.update({
                "status": self.UNAVAILABLE,
                "operational_state": self.UNAVAILABLE,
                "connection_state": self.UNAVAILABLE,
                "authenticated_session_available": False,
                "schema_valid": False,
                "reason": str(error),
            })
            return base

        freshness = assess_quote_freshness(
            snapshot["latest_received_timestamp"],
            snapshot["latest_exchange_timestamp"],
            value=self._now(),
            open_stale_after_minutes=self.stale_after_minutes,
        )
        stale = not freshness.usable
        base.update(snapshot)
        base.update({
            "status": self.STALE if stale else self.DELAYED,
            "operational_state": self.STALE if stale else self.DELAYED,
            "connection_state": self.STALE if stale else self.ACTIVE,
            "authenticated_session_available": bool(snapshot["symbols_available"]),
            "schema_valid": True,
            "session_phase": freshness.phase,
            "session_lag": freshness.session_lag,
            "reason": freshness.reason,
        })
        return base

    def _waiting_session_snapshot(self):
        """Recognize a connected collector outside session without claiming data."""

        if self.db_path is None or not self.db_path.is_file():
            return None
        phase = egx_session_phase(self._now())
        if phase == "OPEN":
            return None
        try:
            with self._connect() as connection:
                tables = {
                    row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if not {"quotes", "candles", "metrics"}.issubset(tables):
                    return None
                row = connection.execute(
                    """SELECT MAX(recorded_at) FROM metrics
                       WHERE name IN ('connect_success', 'reconnect_success')"""
                ).fetchone()
        except (ProviderConnectionError, sqlite3.Error):
            return None
        if not row or not row[0]:
            return None
        return {
            "session_phase": phase,
            "latest_connection_timestamp": _utc_datetime(row[0]).astimezone().isoformat(),
            "symbols_available": [],
            "symbol_count": 0,
            "quote_count": 0,
        }

    def load_history(self, symbol, period, interval):
        health = self.health()
        state = health.get("operational_state")
        if state == self.UNAVAILABLE:
            raise ProviderConnectionError(health.get("reason") or self.UNAVAILABLE)
        if state == self.STALE:
            raise ProviderDataError(health.get("reason") or self.STALE)

        mapped = self.map_symbol(symbol)
        interval_key = str(interval).strip().lower()
        if interval_key in {"1d", "1day", "day"}:
            source = self._load_daily_quotes(mapped)
            frame = self._merge_historical_seed(symbol, period, interval, source)
        elif interval_key in {"1m", "1min", "5m", "5min"}:
            minutes = 1 if interval_key in {"1m", "1min"} else 5
            frame = self._load_intraday_candles(mapped, minutes)
        else:
            raise ProviderDataError(
                f"TickerChart adapter does not store interval {interval!r}; "
                "supported operational intervals are 1m, 5m, and 1d overlay"
            )

        normalized = normalize_history(frame, symbol, self.name)
        metadata = dict(normalized.attrs.get("market_data", {}))
        metadata.update({
            "mapped_symbol": mapped,
            "delayed": True,
            "delay_minutes": 15,
            "status": self.DELAYED,
            "operational_state": self.DELAYED,
            "connection_state": self.ACTIVE,
            "exchange_timestamp": health.get("latest_exchange_timestamp"),
            "received_timestamp": health.get("latest_received_timestamp"),
            "symbols_available": health.get("symbols_available"),
            "schema_valid": True,
            "adapter_interface": "sqlite_read_only",
        })
        if self._history_loader and interval_key in {"1d", "1day", "day"}:
            metadata.update({
                "historical_seed_provider": "yahoo",
                "tickerchart_overlay_active": True,
            })
        normalized.attrs["market_data"] = metadata
        return normalized

    def _database_snapshot(self):
        if self.db_path is None:
            raise ProviderConfigurationError("TICKERCHART_DB_PATH is not configured")
        if not self.db_path.is_file():
            raise ProviderConnectionError(f"TickerChart adapter database not found: {self.db_path}")

        try:
            with self._connect() as connection:
                tables = {
                    row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                missing = {"quotes", "candles", "metrics"} - tables
                if missing:
                    raise ProviderSchemaError(
                        f"TickerChart adapter database missing tables: {', '.join(sorted(missing))}"
                    )
                row = connection.execute(
                    """SELECT MAX(exchange_timestamp), MAX(received_at),
                              COUNT(DISTINCT ticker), COUNT(*) FROM quotes"""
                ).fetchone()
                symbols = [
                    item[0] for item in connection.execute(
                        "SELECT DISTINCT ticker FROM quotes ORDER BY ticker"
                    ).fetchall()
                ]
        except sqlite3.Error as error:
            raise ProviderConnectionError(f"cannot read TickerChart database: {error}") from error

        if not row or not row[1] or not row[3]:
            raise ProviderConnectionError("TickerChart adapter database contains no quotes")
        received = _utc_datetime(row[1])
        exchange = _utc_datetime(row[0])
        age = max(0.0, (self._now().astimezone(timezone.utc) - received).total_seconds() / 60)
        return {
            "latest_exchange_timestamp": exchange.astimezone().isoformat(),
            "latest_received_timestamp": received.astimezone().isoformat(),
            "age_minutes": age,
            "symbols_available": symbols,
            "symbol_count": len(symbols),
            "quote_count": int(row[3]),
        }

    def _load_daily_quotes(self, mapped):
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT exchange_timestamp, received_at, last_price, volume, raw_json
                   FROM quotes WHERE ticker=? ORDER BY exchange_timestamp""",
                (mapped,),
            ).fetchall()
        if not rows:
            raise ProviderDataError(f"TickerChart adapter has no data for {mapped}")

        daily = {}
        for row in rows:
            raw = _json_object(row[4])
            # Session grouping must use the exchange calendar date regardless
            # of the host machine's local timezone.
            timestamp = _utc_datetime(row[0]).astimezone(CAIRO)
            record = {
                "Date": timestamp,
                "Open": _number(raw.get("open")),
                "High": _number(raw.get("high")),
                "Low": _number(raw.get("low")),
                "Close": _number(raw.get("lasttradeprice")) or _number(row[2]),
                "Volume": _number(raw.get("volume")) or _number(row[3]),
            }
            if all(record[name] is not None for name in ("Open", "High", "Low", "Close", "Volume")):
                daily[timestamp.date()] = record
        if not daily:
            raise ProviderSchemaError(
                f"TickerChart {mapped} quotes do not contain complete daily OHLCV fields"
            )
        return pd.DataFrame([daily[key] for key in sorted(daily)])

    def _load_intraday_candles(self, mapped, minutes):
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT bucket_start AS Date, open AS Open, high AS High,
                          low AS Low, close AS Close, volume AS Volume
                   FROM candles WHERE ticker=? AND interval_minutes=?
                   ORDER BY bucket_start""",
                (mapped, minutes),
            ).fetchall()
        if not rows:
            raise ProviderDataError(
                f"TickerChart adapter has no {minutes}-minute candles for {mapped}"
            )
        return pd.DataFrame([dict(row) for row in rows])

    def _merge_historical_seed(self, symbol, period, interval, overlay):
        if self._history_loader is None:
            return overlay
        seed = normalize_history(
            self._history_loader(symbol, period, interval), symbol, "yahoo"
        )
        update = normalize_history(overlay, symbol, self.name)
        if "Adj Close" in seed.columns and "Adj Close" not in update.columns:
            update["Adj Close"] = update["Close"]
        combined = pd.concat([seed, update])
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        return combined

    def _connect(self):
        try:
            path = self.db_path.resolve().as_posix()
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
            connection.row_factory = sqlite3.Row
            return connection
        except (OSError, sqlite3.Error) as error:
            raise ProviderConnectionError(f"cannot open TickerChart database read-only: {error}") from error


def _utc_datetime(value):
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.to_pydatetime()


def _json_object(value):
    try:
        payload = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError) as error:
        raise ProviderSchemaError("TickerChart quote raw_json is invalid") from error
    return payload if isinstance(payload, dict) else {}


def _number(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
