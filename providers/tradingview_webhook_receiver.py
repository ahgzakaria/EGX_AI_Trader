"""Phase D: isolated TradingView confirmed-daily-bar webhook receiver.

This is a LIBRARY module only. It does not open a socket, bind a port, or run
a server by itself — the user must deploy it behind their own HTTPS endpoint
(their own WSGI/ASGI app calls :meth:`TradingViewWebhookReceiver.ingest`).
Nothing here is started automatically anywhere in this codebase, and
``tradingview_webhook_enabled`` defaults to ``false`` in settings.

Security model (per task requirements):
  * TradingView does not support custom request headers on the vanilla webhook
    UI, so the shared secret must travel in the URL the user configures in
    TradingView (e.g. ``https://host/tradingview/webhook?secret=...``), never
    in the JSON body ("Do not include credentials in the payload").
  * Malformed JSON is rejected.
  * Unconfirmed bars (``confirmed`` != true) are rejected.
  * Duplicate (symbol, trading_date) records are rejected (idempotent replay).
  * OHLCV is fully validated; invalid rows are rejected, never repaired.
  * Data is stored only in an application-owned SQLite database. The external
    Rubix adapter database is never touched by this module.
  * The raw payload is preserved alongside the parsed row for audit.
  * This feed is Decision Support Only: nothing here is wired into
    core/data_provider, indicators, strategy, ranking, or backtest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hmac
import json
from pathlib import Path
import sqlite3

import pandas as pd

from core.egx_session import CAIRO


DEFAULT_RECEIVER_DB = "data/tradingview_webhook.db"
REQUIRED_FIELDS = (
    "schema_version", "source", "ticker", "ticker_id", "exchange", "interval",
    "bar_time", "bar_close_time", "open", "high", "low", "close", "volume",
    "confirmed",
)


@dataclass(frozen=True)
class IngestResult:
    accepted: bool
    reason: str | None = None
    symbol: str | None = None
    trading_date: str | None = None


class TradingViewWebhookReceiver:
    """Validate and persist official TradingView confirmed-bar alert payloads."""

    def __init__(self, shared_secret, db_path=DEFAULT_RECEIVER_DB):
        if not shared_secret:
            raise ValueError(
                "A shared secret is required; refuse to run an unauthenticated receiver"
            )
        self._secret = str(shared_secret)
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS webhook_candles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    ticker_id TEXT,
                    exchange TEXT,
                    interval TEXT NOT NULL,
                    trading_date TEXT NOT NULL,
                    bar_time TEXT NOT NULL,
                    bar_close_time TEXT NOT NULL,
                    open REAL NOT NULL, high REAL NOT NULL,
                    low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL,
                    schema_version INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    UNIQUE(ticker, trading_date)
                );
                CREATE TABLE IF NOT EXISTS webhook_raw_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    received_at TEXT NOT NULL,
                    accepted INTEGER NOT NULL,
                    reason TEXT,
                    raw_body TEXT NOT NULL
                );
                """
            )

    # -- public API ---------------------------------------------------------

    def ingest(self, raw_body: bytes | str, *, provided_secret: str) -> IngestResult:
        """Validate one webhook delivery and persist it if (and only if) valid.

        ``provided_secret`` must come from the URL query string the caller's
        own HTTPS endpoint parsed (TradingView cannot send custom headers).
        """

        body_text = raw_body.decode("utf-8") if isinstance(raw_body, bytes) else str(raw_body)

        if not hmac.compare_digest(str(provided_secret or ""), self._secret):
            return self._audit_and_return(body_text, False, "invalid or missing shared secret")

        try:
            payload = json.loads(body_text)
        except (json.JSONDecodeError, TypeError):
            return self._audit_and_return(body_text, False, "malformed JSON body")

        missing = [f for f in REQUIRED_FIELDS if f not in payload]
        if missing:
            return self._audit_and_return(
                body_text, False, f"missing required fields: {', '.join(missing)}"
            )

        if payload.get("schema_version") != 1:
            return self._audit_and_return(
                body_text, False, f"unsupported schema_version: {payload.get('schema_version')!r}"
            )

        if payload.get("confirmed") is not True:
            return self._audit_and_return(
                body_text, False, "unconfirmed bar rejected (confirmed != true)"
            )

        error = _validate_ohlcv(payload)
        if error is not None:
            return self._audit_and_return(body_text, False, f"OHLCV validation failed: {error}")

        trading_date = _trading_date(payload)
        if trading_date is None:
            return self._audit_and_return(body_text, False, "unparseable bar_close_time")

        ticker = str(payload["ticker"])
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM webhook_candles WHERE ticker=? AND trading_date=?",
                (ticker, trading_date),
            ).fetchone()
            if existing is not None:
                self._audit(body_text, False, "duplicate (ticker, trading_date) rejected")
                return IngestResult(False, "duplicate (ticker, trading_date) rejected", ticker, trading_date)

            connection.execute(
                """
                INSERT INTO webhook_candles
                (ticker, ticker_id, exchange, interval, trading_date, bar_time,
                 bar_close_time, open, high, low, close, volume, schema_version,
                 source, received_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    ticker, payload.get("ticker_id"), payload.get("exchange"),
                    payload.get("interval"), trading_date, str(payload["bar_time"]),
                    str(payload["bar_close_time"]), float(payload["open"]),
                    float(payload["high"]), float(payload["low"]),
                    float(payload["close"]), float(payload["volume"]),
                    int(payload["schema_version"]), str(payload["source"]),
                    datetime.now(timezone.utc).astimezone().isoformat(),
                ),
            )
        self._audit(body_text, True, None)
        return IngestResult(True, None, ticker, trading_date)

    def all_candles(self):
        """Return every accepted candle as a DataFrame (audit/shadow use only)."""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM webhook_candles ORDER BY ticker, trading_date"
            ).fetchall()
        return pd.DataFrame([dict(row) for row in rows])

    # -- internals ------------------------------------------------------------

    def _audit_and_return(self, body_text, accepted, reason):
        self._audit(body_text, accepted, reason)
        return IngestResult(accepted, reason)

    def _audit(self, body_text, accepted, reason):
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO webhook_raw_audit(received_at, accepted, reason, raw_body) "
                "VALUES (?,?,?,?)",
                (
                    datetime.now(timezone.utc).astimezone().isoformat(),
                    int(bool(accepted)), reason, body_text,
                ),
            )


def _validate_ohlcv(payload):
    try:
        o, h, l, c, v = (
            float(payload["open"]), float(payload["high"]), float(payload["low"]),
            float(payload["close"]), float(payload["volume"]),
        )
    except (TypeError, ValueError):
        return "non-numeric OHLCV field"
    if any(pd.isna(x) for x in (o, h, l, c, v)):
        return "NaN OHLCV value"
    if not (o > 0 and h > 0 and l > 0 and c > 0):
        return "non-positive OHLC"
    if v < 0:
        return "negative volume"
    if not (h >= l and h >= o and h >= c and l <= o and l <= c):
        return "OHLC ordering violation"
    if str(payload.get("interval")) != "1D":
        return f"unsupported interval {payload.get('interval')!r} (only 1D accepted)"
    return None


def _trading_date(payload):
    value = payload["bar_close_time"]
    try:
        if _looks_like_epoch(value):
            # TradingView `time`/`time_close` are millisecond epoch integers.
            numeric = float(value)
            unit = "ms" if numeric > 10_000_000_000 else "s"
            ts = pd.to_datetime(numeric, utc=True, unit=unit)
        else:
            ts = pd.to_datetime(value, utc=True)
    except (ValueError, TypeError):
        return None
    if ts is pd.NaT or pd.isna(ts):
        return None
    return ts.tz_convert(CAIRO).date().isoformat()


def _looks_like_epoch(value):
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False
