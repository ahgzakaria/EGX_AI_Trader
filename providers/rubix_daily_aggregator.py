"""Phase C: safe Rubix 1-minute → completed-daily aggregation with validation.

This module is read-only and isolated from trading logic.  It reads the adapter
SQLite (``mode=ro``), groups validated one-minute rows by Cairo trading date,
clips to the regular EGX session window, and emits a *completed* daily OHLCV
candle only when every validation check passes.  Any failure rejects the entire
symbol/session candle — values are never repaired and bars are never fabricated.

It never writes to the external Rubix database and never imports strategy,
indicator, ranking, backtest, or AI code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import os
from pathlib import Path
import sqlite3

import pandas as pd

from core.egx_session import (
    CAIRO,
    REGULAR_CLOSE,
    REGULAR_OPEN,
    session_close_datetime,
    session_is_completed,
)
from providers.symbol_mapping import to_rubix_symbol


# Source labels used by provenance (Phase E) and disclosure (Phase H).
YAHOO_DAILY = "YAHOO_DAILY"
RUBIX_COMPLETED_DAILY = "RUBIX_COMPLETED_DAILY"
RUBIX_REJECTED = "RUBIX_REJECTED"
CURRENT_SESSION_EXCLUDED = "CURRENT_SESSION_EXCLUDED"


@dataclass(frozen=True)
class DailyCandleResult:
    """One symbol/session aggregation outcome with full provenance evidence."""

    symbol: str
    mapped_symbol: str
    trading_date: date
    valid: bool
    source: str
    reason: str | None = None
    ohlcv: dict | None = None
    aggregation_method: str = "cairo_session_first_max_min_last"
    source_row_count: int = 0
    valid_row_count: int = 0
    expected_minutes: int = 0
    coverage_ratio: float = 0.0
    first_source_timestamp: str | None = None
    last_source_timestamp: str | None = None
    volume_candle_sum: float = 0.0
    volume_quote_cumulative: float | None = None
    volume_reliability: float | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).astimezone().isoformat()
    )

    def as_provenance_row(self) -> dict:
        return {
            "symbol": self.symbol,
            "mapped_symbol": self.mapped_symbol,
            "trading_date": self.trading_date.isoformat(),
            "source": self.source,
            "valid": int(bool(self.valid)),
            "reason": self.reason,
            "aggregation_method": self.aggregation_method,
            "source_row_count": self.source_row_count,
            "valid_row_count": self.valid_row_count,
            "expected_minutes": self.expected_minutes,
            "coverage_ratio": round(self.coverage_ratio, 4),
            "first_source_timestamp": self.first_source_timestamp,
            "last_source_timestamp": self.last_source_timestamp,
            "open": None if not self.ohlcv else self.ohlcv.get("Open"),
            "high": None if not self.ohlcv else self.ohlcv.get("High"),
            "low": None if not self.ohlcv else self.ohlcv.get("Low"),
            "close": None if not self.ohlcv else self.ohlcv.get("Close"),
            "volume": None if not self.ohlcv else self.ohlcv.get("Volume"),
            "volume_candle_sum": self.volume_candle_sum,
            "volume_quote_cumulative": self.volume_quote_cumulative,
            "volume_reliability": self.volume_reliability,
            "created_at": self.created_at,
        }


class RubixDailyAggregator:
    """Build validated completed-daily candles from adapter 1-minute rows."""

    def __init__(
        self,
        db_path=None,
        *,
        now=None,
        close_safety_minutes=15,
        minimum_coverage_ratio=0.60,
        minimum_volume_reliability=0.90,
        holidays=(),
        early_closes=None,
        session_open=REGULAR_OPEN,
        session_close=REGULAR_CLOSE,
    ):
        configured = db_path or os.getenv("RUBIX_DB_PATH", "")
        self.db_path = Path(configured).expanduser() if configured else None
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.close_safety_minutes = float(close_safety_minutes)
        self.minimum_coverage_ratio = float(minimum_coverage_ratio)
        self.minimum_volume_reliability = float(minimum_volume_reliability)
        self.holidays = tuple(holidays or ())
        self.early_closes = dict(early_closes or {})
        self.session_open = session_open
        self.session_close = session_close

    # -- public API --------------------------------------------------------

    def available(self) -> bool:
        return self.db_path is not None and self.db_path.is_file()

    def build_completed_daily(self, symbol, *, after=None):
        """Return one :class:`DailyCandleResult` per Cairo session for ``symbol``.

        ``after`` (a ``date``) restricts output to sessions strictly newer than
        the caller's existing history, which is how the bridge appends only
        Yahoo-missing dates.  A currently-forming session is always excluded.
        """

        mapped = to_rubix_symbol(symbol)
        minutes = self._load_symbol_minutes(mapped, after=after)
        results: list[DailyCandleResult] = []
        if minutes.empty:
            return results

        quote_cumulative = self._load_symbol_quote_volume(mapped, after=after)
        for trading_date, session_rows in minutes.groupby(minutes.index.map(lambda t: t.date())):
            if after is not None and trading_date <= after:
                continue
            results.append(
                self._aggregate_session(
                    symbol, mapped, trading_date, session_rows,
                    quote_cumulative.get(trading_date),
                )
            )
        return results

    # -- aggregation + validation -----------------------------------------

    def _aggregate_session(
        self, symbol, mapped, trading_date, session_rows, quote_cumulative,
    ):
        base = dict(symbol=symbol, mapped_symbol=mapped, trading_date=trading_date)

        # 1) Never treat a forming / not-yet-closed session as completed.
        if not session_is_completed(
            trading_date,
            value=self._now(),
            close_safety_minutes=self.close_safety_minutes,
            holidays=self.holidays,
            early_closes=self.early_closes,
        ):
            return DailyCandleResult(
                valid=False, source=CURRENT_SESSION_EXCLUDED,
                reason="session not completed (forming, or before close+safety delay)",
                source_row_count=int(len(session_rows)), **base,
            )

        # 2) Clip to the regular Cairo session window.
        open_dt = datetime.combine(trading_date, self.session_open, tzinfo=CAIRO)
        close_dt = session_close_datetime(trading_date, early_closes=self.early_closes)
        in_window = session_rows[
            (session_rows.index >= open_dt) & (session_rows.index <= close_dt)
        ]
        candle_sum = float(session_rows["volume"].fillna(0).clip(lower=0).sum())
        source_count = int(len(session_rows))

        if in_window.empty:
            return DailyCandleResult(
                valid=False, source=RUBIX_REJECTED,
                reason="no one-minute bars inside the regular Cairo session window",
                source_row_count=source_count, volume_candle_sum=candle_sum, **base,
            )

        # 3) Drop invalid bars (never repair). OHLC must be strictly positive
        #    and internally ordered.
        valid_bars = in_window[
            (in_window[["open", "high", "low", "close"]] > 0).all(axis=1)
            & (in_window["high"] >= in_window["low"])
            & (in_window["high"] >= in_window["open"])
            & (in_window["high"] >= in_window["close"])
            & (in_window["low"] <= in_window["open"])
            & (in_window["low"] <= in_window["close"])
        ]
        expected_minutes = self._expected_minutes(trading_date)
        valid_count = int(len(valid_bars))
        coverage = valid_count / expected_minutes if expected_minutes else 0.0

        common = dict(
            source_row_count=source_count,
            valid_row_count=valid_count,
            expected_minutes=expected_minutes,
            coverage_ratio=coverage,
            first_source_timestamp=(
                in_window.index.min().isoformat() if not in_window.empty else None
            ),
            last_source_timestamp=(
                in_window.index.max().isoformat() if not in_window.empty else None
            ),
            volume_candle_sum=candle_sum,
            volume_quote_cumulative=quote_cumulative,
            **base,
        )

        if valid_bars.empty:
            return DailyCandleResult(
                valid=False, source=RUBIX_REJECTED,
                reason="all in-window bars failed positive-OHLC / ordering validation",
                **common,
            )

        # Best-effort OHLCV is computed even when a production gate later fails,
        # so diagnostics (shadow + reconciliation) can measure Rubix-vs-Yahoo
        # divergence. The strict `valid` flag below is what gates production
        # appends; `merge()` never appends a candle whose `valid` is False.
        reliability = (candle_sum / quote_cumulative) if quote_cumulative else None
        common["volume_reliability"] = reliability
        ordered = valid_bars.sort_index()
        daily_volume = float(quote_cumulative) if quote_cumulative else candle_sum
        ohlcv = {
            "Open": float(ordered["open"].iloc[0]),
            "High": float(ordered["high"].max()),
            "Low": float(ordered["low"].min()),
            "Close": float(ordered["close"].iloc[-1]),
            "Volume": daily_volume,
        }

        # 4) Minimum coverage gate.
        if coverage < self.minimum_coverage_ratio:
            return DailyCandleResult(
                valid=False, source=RUBIX_REJECTED, ohlcv=ohlcv,
                reason=(
                    f"coverage {coverage:.1%} below minimum "
                    f"{self.minimum_coverage_ratio:.0%} "
                    f"({valid_count}/{expected_minutes} valid session minutes)"
                ),
                **common,
            )

        # 5) Volume: the only defensible daily total is the cumulative quote
        #    MAX. Summed candle volume is compared against it as a reliability
        #    gate (the audit showed candle-sum is ~10-75% of truth).
        if quote_cumulative is None or quote_cumulative <= 0:
            return DailyCandleResult(
                valid=False, source=RUBIX_REJECTED, ohlcv=ohlcv,
                reason="no cumulative quote volume available to establish a daily total",
                **common,
            )
        if reliability < self.minimum_volume_reliability:
            return DailyCandleResult(
                valid=False, source=RUBIX_REJECTED, ohlcv=ohlcv,
                reason=(
                    f"volume reliability {reliability:.1%} below minimum "
                    f"{self.minimum_volume_reliability:.0%} "
                    "(candle-summed volume undercounts cumulative quote volume)"
                ),
                **common,
            )

        # 6) Final candle validity — reject rather than repair.
        failure = _validate_daily_ohlcv(ohlcv)
        if failure is not None:
            return DailyCandleResult(
                valid=False, source=RUBIX_REJECTED, ohlcv=ohlcv,
                reason=f"aggregated candle failed final validation: {failure}",
                **common,
            )

        return DailyCandleResult(
            valid=True, source=RUBIX_COMPLETED_DAILY, ohlcv=ohlcv, **common,
        )

    def _expected_minutes(self, trading_date):
        close_dt = session_close_datetime(trading_date, early_closes=self.early_closes)
        open_dt = datetime.combine(trading_date, self.session_open, tzinfo=CAIRO)
        return max(1, int((close_dt - open_dt).total_seconds() // 60))

    # -- read-only DB access ----------------------------------------------

    def _load_symbol_minutes(self, mapped, *, after=None):
        """Minute bars for one symbol, optionally only after a Cairo date.

        Two things keep this off a full table scan of a multi-gigabyte table:

        * the ticker is compared bare, not through ``UPPER()``. Wrapping the
          column in a function makes SQLite ignore the ``(ticker, minute)``
          index and read every row — which cost ~45-60s per symbol, roughly
          four hours for the universe. Rubix stores tickers upper-cased (all
          265 in both tables), so the comparison is equivalent.
        * ``after`` is pushed into SQL rather than filtered in Python, so an
          incremental append reads only the sessions it is going to keep.

        The bound is deliberately loose — ``minute`` is stored as text and the
        caller re-derives the Cairo date anyway — so a timezone edge can never
        drop a session that belongs in the result.
        """

        if not self.available():
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        sql = ("SELECT minute, open, high, low, close, volume FROM candles_1m "
               "WHERE ticker=?")
        params = [mapped.upper()]
        if after is not None:
            sql += " AND minute >= ?"
            params.append(after.isoformat())
        sql += " ORDER BY minute"
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        frame = pd.DataFrame(
            [tuple(r) for r in rows],
            columns=["minute", "open", "high", "low", "close", "volume"],
        )
        frame.index = _to_cairo_index(frame.pop("minute"))
        frame = frame[~frame.index.isna()]
        return frame.sort_index()

    def _load_symbol_quote_volume(self, mapped, *, after=None):
        """Return {Cairo date -> MAX cumulative quote volume} for the symbol.

        Same index and date-bound reasoning as :meth:`_load_symbol_minutes`;
        ``quotes`` is the larger of the two tables, so the scan mattered more
        here.
        """

        if not self.available():
            return {}
        sql = ("SELECT market_timestamp, volume FROM quotes "
               "WHERE ticker=? AND volume IS NOT NULL")
        params = [mapped.upper()]
        if after is not None:
            sql += " AND market_timestamp >= ?"
            params.append(after.isoformat())
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        result: dict[date, float] = {}
        for stamp, volume in rows:
            ts = _to_cairo_timestamp(stamp)
            if ts is None:
                continue
            day = ts.date()
            value = float(volume)
            if value > result.get(day, 0.0):
                result[day] = value
        return result

    def _connect(self):
        uri = f"file:{self.db_path.resolve().as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection


def _validate_daily_ohlcv(ohlcv):
    """Return a failure reason string, or None when the candle is valid."""

    o, h, l, c, v = (
        ohlcv["Open"], ohlcv["High"], ohlcv["Low"], ohlcv["Close"], ohlcv["Volume"]
    )
    if any(pd.isna(x) for x in (o, h, l, c, v)):
        return "NaN OHLCV value"
    if not (o > 0 and h > 0 and l > 0 and c > 0):
        return "non-positive OHLC"
    if v < 0:
        return "negative volume"
    if not (h >= l and h >= o and h >= c and l <= o and l <= c):
        return "OHLC ordering violation"
    return None


def _to_cairo_index(values):
    parsed = pd.to_datetime(values, utc=True, errors="coerce")
    return pd.DatetimeIndex(parsed).tz_convert(CAIRO)


def _to_cairo_timestamp(value):
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    if ts is pd.NaT or pd.isna(ts):
        return None
    return ts.tz_convert(CAIRO)
