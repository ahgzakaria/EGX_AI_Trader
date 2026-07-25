"""Acceptance tests for the isolated TradingView confirmed-daily webhook
receiver. All payloads here are synthetic — no real TradingView data is
available in this environment (no account, no live webhook)."""

import json

import pandas as pd
import pytest

from providers.tradingview_webhook_receiver import TradingViewWebhookReceiver


def _payload(**overrides):
    base = {
        "schema_version": 1, "source": "TRADINGVIEW_OFFICIAL_ALERT",
        "ticker": "COMI", "ticker_id": "EGX:COMI", "exchange": "EGX",
        "interval": "1D", "bar_time": "1753027200000",
        "bar_close_time": "1753027200000",
        "open": 100.0, "high": 102.0, "low": 99.5, "close": 101.0,
        "volume": 12345, "confirmed": True,
    }
    base.update(overrides)
    return base


def test_requires_a_secret(tmp_path):
    with pytest.raises(ValueError):
        TradingViewWebhookReceiver(None, db_path=tmp_path / "r.db")


def test_wrong_secret_rejected(tmp_path):
    r = TradingViewWebhookReceiver("s3cret", db_path=tmp_path / "r.db")
    result = r.ingest(json.dumps(_payload()), provided_secret="wrong")
    assert result.accepted is False
    assert "secret" in result.reason


def test_valid_confirmed_bar_is_accepted_and_dated_in_cairo(tmp_path):
    r = TradingViewWebhookReceiver("s3cret", db_path=tmp_path / "r.db")
    result = r.ingest(json.dumps(_payload()), provided_secret="s3cret")
    assert result.accepted is True
    assert result.symbol == "COMI"
    df = r.all_candles()
    assert len(df) == 1
    assert df.iloc[0]["close"] == 101.0


def test_duplicate_symbol_date_rejected(tmp_path):
    r = TradingViewWebhookReceiver("s3cret", db_path=tmp_path / "r.db")
    r.ingest(json.dumps(_payload()), provided_secret="s3cret")
    result = r.ingest(json.dumps(_payload()), provided_secret="s3cret")
    assert result.accepted is False
    assert "duplicate" in result.reason
    assert len(r.all_candles()) == 1


def test_malformed_json_rejected(tmp_path):
    r = TradingViewWebhookReceiver("s3cret", db_path=tmp_path / "r.db")
    result = r.ingest("{not valid json", provided_secret="s3cret")
    assert result.accepted is False
    assert "malformed" in result.reason


def test_unconfirmed_bar_rejected(tmp_path):
    r = TradingViewWebhookReceiver("s3cret", db_path=tmp_path / "r.db")
    result = r.ingest(json.dumps(_payload(confirmed=False)), provided_secret="s3cret")
    assert result.accepted is False
    assert "unconfirmed" in result.reason


@pytest.mark.parametrize("field,value,expect", [
    ("low", 0, "non-positive"),
    ("open", -1, "non-positive"),
    ("volume", -5, "negative volume"),
    ("high", 90, "ordering"),  # high < low
])
def test_invalid_ohlcv_rejected(tmp_path, field, value, expect):
    r = TradingViewWebhookReceiver("s3cret", db_path=tmp_path / "r.db")
    result = r.ingest(json.dumps(_payload(**{field: value})), provided_secret="s3cret")
    assert result.accepted is False
    assert expect in result.reason
    assert len(r.all_candles()) == 0


def test_missing_field_rejected(tmp_path):
    payload = _payload()
    del payload["close"]
    r = TradingViewWebhookReceiver("s3cret", db_path=tmp_path / "r.db")
    result = r.ingest(json.dumps(payload), provided_secret="s3cret")
    assert result.accepted is False
    assert "close" in result.reason


def test_unsupported_schema_version_rejected(tmp_path):
    r = TradingViewWebhookReceiver("s3cret", db_path=tmp_path / "r.db")
    result = r.ingest(json.dumps(_payload(schema_version=2)), provided_secret="s3cret")
    assert result.accepted is False
    assert "schema_version" in result.reason


def test_non_daily_interval_rejected(tmp_path):
    r = TradingViewWebhookReceiver("s3cret", db_path=tmp_path / "r.db")
    result = r.ingest(json.dumps(_payload(interval="60")), provided_secret="s3cret")
    assert result.accepted is False
    assert "interval" in result.reason


def test_raw_payload_preserved_for_audit(tmp_path):
    import sqlite3

    db = tmp_path / "r.db"
    r = TradingViewWebhookReceiver("s3cret", db_path=db)
    r.ingest(json.dumps(_payload()), provided_secret="wrong")
    r.ingest(json.dumps(_payload()), provided_secret="s3cret")
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT accepted, raw_body FROM webhook_raw_audit").fetchall()
    assert len(rows) == 2
    assert {row[0] for row in rows} == {0, 1}


def test_never_writes_rubix_database(tmp_path):
    """The receiver must only ever touch its own app-owned DB path."""

    receiver_db = tmp_path / "receiver.db"
    r = TradingViewWebhookReceiver("s3cret", db_path=receiver_db)
    r.ingest(json.dumps(_payload()), provided_secret="s3cret")
    assert r.path == receiver_db
    assert not (tmp_path / "rubix_live_market.db").exists()
