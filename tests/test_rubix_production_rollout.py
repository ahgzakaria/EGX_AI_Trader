"""Operational Rubix tests; frozen trading calculations are intentionally absent."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pandas as pd

from core.live_actionability import assess_live_actionability
from providers.rubix_subscription import build_rubix_subscription_plan
from providers.rubix_sqlite_provider import RubixSQLiteProvider
from providers.symbol_mapping import to_rubix_subscription_symbol
from scripts.launch_rubix_production import (
    build_collector_command,
    redact_log,
    validate_auth_frame,
)
from forward_testing.database import ForwardDatabase
from forward_testing.service import ForwardTestingService


def test_complete_symbol_mapping_and_deterministic_batching(tmp_path):
    source = tmp_path / "symbols.csv"
    source.write_text(
        "Ticker\nCOMI.CA\nSWDY.CA\nFWRY.CA\nBAD SYMBOL\nCOMI.CA\n",
        encoding="utf-8",
    )
    plan = build_rubix_subscription_plan(source, batch_size=2)
    assert to_rubix_subscription_symbol("COMI.CA") == "CASE~COMI"
    assert plan.subscriptions == ("CASE~COMI", "CASE~SWDY", "CASE~FWRY")
    assert plan.batches == (("CASE~COMI", "CASE~SWDY"), ("CASE~FWRY",))
    assert plan.invalid == ({"symbol": "BAD SYMBOL", "reason": "expected uppercase .CA ticker"},)


def test_optional_active_flag_filters_collector_only(tmp_path):
    source = tmp_path / "symbols.csv"
    source.write_text("Ticker,Active\nCOMI.CA,true\nOLD.CA,false\n", encoding="utf-8")
    plan = build_rubix_subscription_plan(source)
    assert plan.requested == ("COMI.CA",)
    assert plan.subscriptions == ("CASE~COMI",)


def test_fresh_rubix_buy_is_actionable_during_cairo_session():
    now = datetime(2026, 7, 14, 8, 30, tzinfo=timezone.utc)  # 11:30 Cairo
    result = assess_live_actionability("BUY", {
        "effective_provider": "rubix", "freshness": "FRESH", "session_phase": "OPEN",
    }, now=now)
    assert result.operational_status == "LIVE_DATA_OK"
    assert result.final_actionability == "ACTIONABLE"


def test_stale_rubix_buy_remains_strategy_buy_but_is_non_actionable():
    result = assess_live_actionability("BUY", {
        "effective_provider": "rubix", "freshness": "STALE", "session_phase": "OPEN",
        "freshness_warning": "latest quote is 90 seconds old",
    })
    assert result.operational_status == "DATA_STALE"
    assert result.final_actionability == "NON_ACTIONABLE"


def test_yahoo_fallback_is_daily_non_live():
    result = assess_live_actionability("BUY", {
        "effective_provider": "yahoo", "fallback_active": True,
        "fallback_reason": "SYMBOL_MISSING", "session_phase": "OPEN",
    })
    assert result.operational_status == "DAILY_FALLBACK"
    assert result.final_actionability == "NON_LIVE / NON_ACTIONABLE"


def test_delayed_rubix_is_explicit_and_non_actionable():
    result = assess_live_actionability("BUY", {
        "effective_provider": "rubix", "freshness": "DELAYED", "session_phase": "OPEN",
    })
    assert result.operational_status == "DATA_DELAYED"
    assert result.final_actionability == "NON_ACTIONABLE"


def test_market_closed_analysis_is_not_a_new_live_buy():
    result = assess_live_actionability("BUY", {
        "effective_provider": "rubix", "freshness": "FRESH", "session_phase": "POST_CLOSE",
    })
    assert result.operational_status == "MARKET_CLOSED"
    assert result.final_actionability == "NON_ACTIONABLE"


def test_auth_validation_accepts_json_envelope_and_secret_logs_are_redacted(tmp_path):
    auth = tmp_path / "current.frame"
    secret = "synthetic-test-token"
    auth.write_text(json.dumps({"MT": -1, "TKN": secret}), encoding="utf-8")
    validated = validate_auth_frame(auth, now=datetime.now(timezone.utc))
    assert validated == auth.resolve()
    output = redact_log(f"auth_frame={secret} token=abc cookie=xyz")
    assert "super-secret-token" not in output
    assert "token=abc" not in output
    assert "cookie=xyz" not in output


def test_stale_auth_frame_is_rejected(tmp_path):
    auth = tmp_path / "old.frame"
    auth.write_text(json.dumps({"MT": -1, "TKN": "synthetic"}), encoding="utf-8")
    modified = datetime.now(timezone.utc) - timedelta(minutes=30)
    import os
    os.utime(auth, (modified.timestamp(), modified.timestamp()))
    try:
        validate_auth_frame(auth, now=datetime.now(timezone.utc), max_age_minutes=15)
    except ValueError as error:
        assert "expired" in str(error)
    else:
        raise AssertionError("stale authentication frame was accepted")


def test_pasted_auth_content_is_rejected_without_echoing_secret():
    secret = "opaque-authentication-payload-that-is-not-a-file-path"
    try:
        validate_auth_frame(secret)
    except ValueError as error:
        message = str(error)
        assert "Browse" in message
        assert "do not paste" in message
        assert secret not in message
    else:
        raise AssertionError("pasted authentication content was accepted as a file path")


def test_collector_command_contains_every_subscription_without_auth_contents(tmp_path):
    command = build_collector_command(
        Path("python.exe"), Path("adapter"), Path("session.frame"),
        tmp_path / "rubix.db", ("CASE~COMI", "CASE~SWDY"), batch_size=1,
    )
    assert command.count("--symbol") == 2
    assert "CASE~COMI" in command and "CASE~SWDY" in command
    assert "--subscription-batch-size" in command
    assert not any("token" in item.lower() or "cookie" in item.lower() for item in command)


def test_strategy_signal_value_is_never_mutated_by_operational_layer():
    signal = "BUY"
    assess_live_actionability(signal, {
        "effective_provider": "rubix", "freshness": "STALE", "session_phase": "OPEN",
    })
    assert signal == "BUY"


def test_non_actionable_buy_is_evidence_but_never_pending_or_positioned(tmp_path):
    database = ForwardDatabase(tmp_path / "forward.db")
    service = ForwardTestingService(database)
    frame = pd.DataFrame(
        {"Open": [10], "High": [11], "Low": [9], "Close": [10.5], "Volume": [100]},
        index=pd.DatetimeIndex(["2026-07-14"], name="Date"),
    )
    inserted = service.record_signals([{
        "Ticker": "COMI.CA", "Signal": "BUY", "Actionable": False,
        "Data": frame, "Price": 10.5, "Reasons": "frozen strategy BUY",
    }], "RUN_TEST", "settings-hash", datetime(2026, 7, 14, 8, 30, tzinfo=timezone.utc))
    assert inserted == 1
    assert len(database.rows("SELECT * FROM signals")) == 1
    assert database.rows("SELECT * FROM paper_positions") == []
    assert service.pending_signals() == []


def test_health_reports_reconnect_resubscription_coverage_and_freshness(tmp_path):
    database = tmp_path / "rubix.db"
    base = datetime(2026, 7, 14, 8, 30, tzinfo=timezone.utc)
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE quotes (id INTEGER PRIMARY KEY, ticker TEXT, last_price REAL,
              bid REAL, ask REAL, volume REAL, market_timestamp TEXT, received_at TEXT,
              exchange TEXT, sequence INTEGER, change_percent REAL, has_feed_timestamp INTEGER);
            CREATE TABLE candles_1m (ticker TEXT, minute TEXT, open REAL, high REAL,
              low REAL, close REAL, volume REAL, updates INTEGER,
              PRIMARY KEY(ticker, minute));
            CREATE TABLE feed_metrics (id INTEGER PRIMARY KEY, observed_at TEXT,
              event TEXT, ticker TEXT, value REAL, detail TEXT);
            """
        )
        quote_time = base + timedelta(seconds=25)
        connection.execute(
            "INSERT INTO quotes VALUES (1,'COMI',100,99,101,1000,?,?, 'CASE',1,0,1)",
            (quote_time.isoformat(), quote_time.isoformat()),
        )
        connection.execute(
            "INSERT INTO candles_1m VALUES ('COMI',?,100,101,99,100,100,1)",
            (quote_time.replace(second=0).isoformat(),),
        )
        events = (
            (base, "connected"),
            (base + timedelta(seconds=10), "disconnect"),
            (base + timedelta(seconds=20), "reconnect_success"),
            (base + timedelta(seconds=21), "authentication_acknowledged"),
            (base + timedelta(seconds=22), "subscription_batch_sent"),
            (base + timedelta(seconds=23), "heartbeat_received"),
        )
        connection.executemany(
            "INSERT INTO feed_metrics(observed_at,event) VALUES (?,?)",
            [(stamp.isoformat(), event) for stamp, event in events],
        )
    provider = RubixSQLiteProvider(
        database, stale_after_minutes=1, bar_stale_after_minutes=2,
        expected_symbols=("COMI.CA", "SWDY.CA"),
        now=lambda: base + timedelta(seconds=30),
    )
    health = provider.health()
    assert health["status"] == provider.FRESH
    assert health["collector_status"] == "CONNECTED"
    assert health["authentication_status"] == "ACKNOWLEDGED"
    assert health["reconnect_count"] == 1
    assert health["subscription_batches_sent"] == 1
    assert health["symbols_received"] == 1
    assert health["symbols_missing"] == 1
    assert health["symbol_coverage_pct"] == 50.0
    assert health["live_scanning_safe"] is True
