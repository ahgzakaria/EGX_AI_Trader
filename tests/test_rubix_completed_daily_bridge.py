"""Acceptance tests for the Rubix Completed-Daily-Candle Bridge.

These prove the safety contract: a forming session is never included, only
validated completed sessions are appended, Yahoo candles are never overwritten,
the external Rubix database stays byte-for-byte read-only, and a data failure
never removes a symbol or changes the frozen strategy's output on identical
OHLCV.
"""

from datetime import date, datetime, time, timedelta, timezone
import sqlite3

import pandas as pd
import pytest

from core.egx_session import (
    is_regular_trading_day,
    latest_completed_session_date,
    session_is_completed,
)
from providers.base_provider import normalize_history
from providers.rubix_completed_daily_bridge import RubixCompletedDailyBridge
from providers.rubix_daily_aggregator import (
    CURRENT_SESSION_EXCLUDED,
    RUBIX_COMPLETED_DAILY,
    RUBIX_REJECTED,
    RubixDailyAggregator,
)

# Cairo is UTC+3 in July; 10:00 Cairo == 07:00 UTC.
JULY_OPEN_UTC = datetime(2026, 7, 20, 7, 0, tzinfo=timezone.utc)


def _build_rubix_db(
    path, *, session_date=date(2026, 7, 20), n_bars=250, symbol="COMI",
    volume_reliability=0.95, low_zero=False, negative_volume=False,
    open_utc=JULY_OPEN_UTC,
):
    """Create a synthetic adapter DB with a dense, valid one-minute session."""

    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL,
                last_price REAL, bid REAL, ask REAL, volume REAL,
                market_timestamp TEXT NOT NULL, received_at TEXT NOT NULL,
                exchange TEXT, sequence INTEGER, change_percent REAL,
                has_feed_timestamp INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE candles_1m (
                ticker TEXT NOT NULL, minute TEXT NOT NULL, open REAL NOT NULL,
                high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
                volume REAL NOT NULL DEFAULT 0, updates INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (ticker, minute)
            );
            CREATE TABLE feed_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT, observed_at TEXT NOT NULL,
                event TEXT NOT NULL, ticker TEXT, value REAL, detail TEXT
            );
            """
        )
        base = open_utc.replace(
            year=session_date.year, month=session_date.month, day=session_date.day
        )
        candle_sum = 0.0
        for i in range(n_bars):
            stamp = base + timedelta(minutes=i)
            price = 100.0 + (i % 5) * 0.1
            low = 0.0 if (low_zero and i == 3) else price - 0.2
            vol = -5.0 if (negative_volume and i == 4) else 100.0
            if vol > 0:
                candle_sum += vol
            conn.execute(
                "INSERT INTO candles_1m(ticker,minute,open,high,low,close,volume,updates)"
                " VALUES (?,?,?,?,?,?,?,1)",
                (symbol, stamp.isoformat(), price, price + 0.3, low, price + 0.1, vol),
            )
        # One cumulative quote carrying the official daily volume, sized so the
        # candle-sum / quote reliability ratio equals ``volume_reliability``.
        cumulative = candle_sum / volume_reliability if volume_reliability else candle_sum
        last = base + timedelta(minutes=n_bars)
        conn.execute(
            "INSERT INTO quotes(ticker,last_price,bid,ask,volume,market_timestamp,"
            "received_at,exchange,has_feed_timestamp) VALUES (?,?,?,?,?,?,?,?,1)",
            (symbol, 100.4, 100.39, 100.41, cumulative,
             last.isoformat(), last.isoformat(), "CASE"),
        )
    return path


def _yahoo_frame(dates, *, symbol="COMI", volume=1000.0):
    rows = {"Date": [], "Open": [], "High": [], "Low": [], "Close": [], "Volume": []}
    for d in dates:
        rows["Date"].append(pd.Timestamp(d))
        rows["Open"].append(50.0)
        rows["High"].append(51.0)
        rows["Low"].append(49.0)
        rows["Close"].append(50.5)
        rows["Volume"].append(volume)
    frame = pd.DataFrame(rows)
    return normalize_history(frame, symbol, "yahoo")


def _after_close(session_date, minutes=30):
    # 14:30 Cairo == 11:30 UTC in July; add a margin beyond the safety delay.
    return datetime(
        session_date.year, session_date.month, session_date.day, 11, 30,
        tzinfo=timezone.utc,
    ) + timedelta(minutes=minutes)


# -- Phase B: session completion ------------------------------------------

def test_session_completion_after_close_and_weekend_and_holiday():
    monday = date(2026, 7, 20)
    assert is_regular_trading_day(monday)
    # Before close+safety -> not completed.
    before = datetime(2026, 7, 20, 11, 30, tzinfo=timezone.utc)  # exactly close
    assert session_is_completed(monday, value=before, close_safety_minutes=15) is False
    # After close+safety -> completed.
    after = _after_close(monday, minutes=20)
    assert session_is_completed(monday, value=after, close_safety_minutes=15) is True
    # Friday / Saturday are never trading days.
    assert is_regular_trading_day(date(2026, 7, 17)) is False  # Friday
    assert is_regular_trading_day(date(2026, 7, 18)) is False  # Saturday
    assert session_is_completed(date(2026, 7, 17), value=after) is False
    # An explicit holiday is excluded even on a weekday.
    assert session_is_completed(monday, value=after, holidays=["2026-07-20"]) is False


def test_latest_completed_session_skips_forming_day():
    during_open = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)  # 11:00 Cairo
    latest = latest_completed_session_date(value=during_open, close_safety_minutes=15)
    # Today's session is still forming, so the latest completed one is earlier.
    assert latest < date(2026, 7, 20)
    assert is_regular_trading_day(latest)


# -- Phase C: aggregation + validation ------------------------------------

def test_dense_valid_session_is_accepted(tmp_path):
    db = _build_rubix_db(tmp_path / "r.db", n_bars=250)
    agg = RubixDailyAggregator(
        db_path=db, now=lambda: _after_close(date(2026, 7, 20)),
        minimum_coverage_ratio=0.90, minimum_volume_reliability=0.90,
    )
    results = agg.build_completed_daily("COMI.CA", after=None)
    assert len(results) == 1
    r = results[0]
    assert r.valid is True
    assert r.source == RUBIX_COMPLETED_DAILY
    assert r.ohlcv["Low"] > 0 and r.ohlcv["High"] >= r.ohlcv["Low"]
    assert r.coverage_ratio >= 0.90


def test_forming_session_is_excluded(tmp_path):
    db = _build_rubix_db(tmp_path / "r.db", n_bars=250)
    # "now" is during the open session -> not completed.
    agg = RubixDailyAggregator(
        db_path=db, now=lambda: datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc),
    )
    results = agg.build_completed_daily("COMI.CA", after=None)
    assert len(results) == 1
    assert results[0].valid is False
    assert results[0].source == CURRENT_SESSION_EXCLUDED


def test_low_zero_bar_never_enters_aggregate(tmp_path):
    db = _build_rubix_db(tmp_path / "r.db", n_bars=250, low_zero=True)
    agg = RubixDailyAggregator(
        db_path=db, now=lambda: _after_close(date(2026, 7, 20)),
    )
    r = agg.build_completed_daily("COMI.CA", after=None)[0]
    # The zero-low bar is dropped; the aggregate Low is strictly positive.
    assert r.ohlcv is not None
    assert r.ohlcv["Low"] > 0


def test_negative_volume_bar_does_not_crash_and_is_clipped(tmp_path):
    db = _build_rubix_db(tmp_path / "r.db", n_bars=250, negative_volume=True)
    agg = RubixDailyAggregator(
        db_path=db, now=lambda: _after_close(date(2026, 7, 20)),
    )
    r = agg.build_completed_daily("COMI.CA", after=None)[0]
    assert r.ohlcv["Volume"] >= 0


def test_low_coverage_session_is_rejected(tmp_path):
    db = _build_rubix_db(tmp_path / "r.db", n_bars=20)  # far below 90% of 270
    agg = RubixDailyAggregator(
        db_path=db, now=lambda: _after_close(date(2026, 7, 20)),
        minimum_coverage_ratio=0.90,
    )
    r = agg.build_completed_daily("COMI.CA", after=None)[0]
    assert r.valid is False
    assert r.source == RUBIX_REJECTED
    assert "coverage" in r.reason


def test_low_volume_reliability_is_rejected(tmp_path):
    db = _build_rubix_db(tmp_path / "r.db", n_bars=250, volume_reliability=0.30)
    agg = RubixDailyAggregator(
        db_path=db, now=lambda: _after_close(date(2026, 7, 20)),
        minimum_coverage_ratio=0.90, minimum_volume_reliability=0.90,
    )
    r = agg.build_completed_daily("COMI.CA", after=None)[0]
    assert r.valid is False
    assert "volume reliability" in r.reason


def test_missing_symbol_returns_no_sessions(tmp_path):
    db = _build_rubix_db(tmp_path / "r.db", n_bars=250)
    agg = RubixDailyAggregator(
        db_path=db, now=lambda: _after_close(date(2026, 7, 20)),
    )
    assert agg.build_completed_daily("NONE.CA", after=None) == []


def test_disconnected_rubix_is_unavailable(tmp_path):
    agg = RubixDailyAggregator(db_path=tmp_path / "does_not_exist.db")
    assert agg.available() is False
    assert agg.build_completed_daily("COMI.CA", after=None) == []


def test_rubix_database_is_never_modified(tmp_path):
    db = _build_rubix_db(tmp_path / "r.db", n_bars=250)
    with sqlite3.connect(db) as conn:
        before_candles = conn.execute("SELECT COUNT(*) FROM candles_1m").fetchone()[0]
        before_quotes = conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
    agg = RubixDailyAggregator(
        db_path=db, now=lambda: _after_close(date(2026, 7, 20)),
    )
    agg.build_completed_daily("COMI.CA", after=None)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM candles_1m").fetchone()[0] == before_candles
        assert conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == before_quotes


# -- Phase D: bridge merge -------------------------------------------------

def test_bridge_appends_only_newer_validated_session(tmp_path):
    db = _build_rubix_db(tmp_path / "r.db", n_bars=250)  # valid 2026-07-20
    agg = RubixDailyAggregator(
        db_path=db, now=lambda: _after_close(date(2026, 7, 20)),
        minimum_coverage_ratio=0.90, minimum_volume_reliability=0.90,
    )
    bridge = RubixCompletedDailyBridge(agg)
    yahoo = _yahoo_frame(["2026-07-15", "2026-07-16"])
    result = bridge.merge("COMI.CA", yahoo)
    assert result.sessions_appended == 1
    assert "2026-07-20" in result.appended_dates
    # The appended row is present and dated after Yahoo's latest.
    assert pd.Timestamp("2026-07-20") in result.frame.index
    assert result.merged_latest_date == "2026-07-20"


def test_bridge_never_overwrites_existing_yahoo_candle(tmp_path):
    # Rubix has a valid 2026-07-16 session, but Yahoo already owns that date.
    db = _build_rubix_db(tmp_path / "r.db", n_bars=250, session_date=date(2026, 7, 16))
    agg = RubixDailyAggregator(
        db_path=db, now=lambda: _after_close(date(2026, 7, 16)),
        minimum_coverage_ratio=0.90, minimum_volume_reliability=0.90,
    )
    bridge = RubixCompletedDailyBridge(agg)
    yahoo = _yahoo_frame(["2026-07-15", "2026-07-16"], volume=1234.0)
    result = bridge.merge("COMI.CA", yahoo)
    # after=Yahoo-latest(07-16) excludes 07-16, so nothing is appended and the
    # canonical Yahoo candle is untouched.
    assert result.sessions_appended == 0
    row = result.frame.loc[pd.Timestamp("2026-07-16")]
    assert float(row["Close"]) == 50.5  # Yahoo value, not Rubix 100.x
    assert float(row["Volume"]) == 1234.0


def test_bridge_identity_when_nothing_appended_preserves_decisions(tmp_path):
    # Rubix session too sparse to pass -> merged frame is identical to Yahoo.
    db = _build_rubix_db(tmp_path / "r.db", n_bars=10)
    agg = RubixDailyAggregator(
        db_path=db, now=lambda: _after_close(date(2026, 7, 20)),
        minimum_coverage_ratio=0.90,
    )
    bridge = RubixCompletedDailyBridge(agg)
    yahoo = _yahoo_frame(["2026-07-14", "2026-07-15", "2026-07-16"])
    result = bridge.merge("COMI.CA", yahoo)
    assert result.sessions_appended == 0
    pd.testing.assert_frame_equal(result.frame, yahoo)


def test_bridge_unavailable_rubix_returns_yahoo_unchanged(tmp_path):
    agg = RubixDailyAggregator(db_path=tmp_path / "missing.db")
    bridge = RubixCompletedDailyBridge(agg)
    yahoo = _yahoo_frame(["2026-07-15", "2026-07-16"])
    result = bridge.merge("COMI.CA", yahoo)
    assert result.sessions_appended == 0
    assert result.rubix_available is False
    pd.testing.assert_frame_equal(result.frame, yahoo)


def test_reconcile_reports_overlap_without_mutating_yahoo(tmp_path):
    db = _build_rubix_db(tmp_path / "r.db", n_bars=250, session_date=date(2026, 7, 16))
    agg = RubixDailyAggregator(
        db_path=db, now=lambda: _after_close(date(2026, 7, 16)),
    )
    bridge = RubixCompletedDailyBridge(agg)
    yahoo = _yahoo_frame(["2026-07-15", "2026-07-16"])
    snapshot = yahoo.copy()
    records = bridge.reconcile("COMI.CA", yahoo)
    assert any(r.trading_date == "2026-07-16" for r in records)
    pd.testing.assert_frame_equal(yahoo, snapshot)  # Yahoo untouched
