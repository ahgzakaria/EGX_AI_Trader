"""Acceptance tests for the TradingView Completed-Daily-Candle Bridge.

Synthetic data only -- proves the append-only / never-overwrite / reject-not-
repair contract mirrors the already-proven Rubix bridge.
"""

import pandas as pd

from providers.base_provider import normalize_history
from providers.tradingview_completed_daily_bridge import (
    TRADINGVIEW_REJECTED,
    TradingViewCompletedDailyBridge,
)


def _yahoo_frame(dates, *, symbol="COMI", volume=1000.0):
    rows = {"Date": [], "Open": [], "High": [], "Low": [], "Close": [], "Volume": []}
    for d in dates:
        rows["Date"].append(pd.Timestamp(d))
        rows["Open"].append(50.0)
        rows["High"].append(51.0)
        rows["Low"].append(49.0)
        rows["Close"].append(50.5)
        rows["Volume"].append(volume)
    return normalize_history(pd.DataFrame(rows), symbol, "yahoo")


def _tv_frame(rows_by_date):
    index = pd.DatetimeIndex([pd.Timestamp(d) for d in rows_by_date])
    return pd.DataFrame(list(rows_by_date.values()), index=index)


def test_appends_only_newer_validated_session():
    bridge = TradingViewCompletedDailyBridge()
    yahoo = _yahoo_frame(["2026-07-14", "2026-07-15", "2026-07-16"])
    tv = _tv_frame({
        "2026-07-20": {"Open": 60, "High": 61, "Low": 59, "Close": 60.5, "Volume": 2000},
    })
    result = bridge.merge("COMI.CA", yahoo, tv, tv_method="tradingview_csv")
    assert result.sessions_appended == 1
    assert result.appended_dates == ["2026-07-20"]
    assert result.data_valid is True
    assert pd.Timestamp("2026-07-20") in result.frame.index


def test_never_overwrites_existing_yahoo_date():
    bridge = TradingViewCompletedDailyBridge()
    yahoo = _yahoo_frame(["2026-07-15", "2026-07-16"], volume=1234.0)
    tv = _tv_frame({
        "2026-07-16": {"Open": 999, "High": 999, "Low": 999, "Close": 999, "Volume": 999},
    })
    result = bridge.merge("COMI.CA", yahoo, tv, tv_method="tradingview_csv")
    assert result.sessions_appended == 0
    row = result.frame.loc[pd.Timestamp("2026-07-16")]
    assert float(row["Close"]) == 50.5  # Yahoo value retained, not TradingView's
    assert float(row["Volume"]) == 1234.0


def test_invalid_tv_row_is_rejected_not_repaired():
    bridge = TradingViewCompletedDailyBridge()
    yahoo = _yahoo_frame(["2026-07-15", "2026-07-16"])
    tv = _tv_frame({
        "2026-07-20": {"Open": 60, "High": 61, "Low": 0, "Close": 60.5, "Volume": 2000},
    })
    result = bridge.merge("COMI.CA", yahoo, tv, tv_method="tradingview_csv")
    assert result.sessions_appended == 0
    assert result.data_valid is False
    assert "non-positive OHLC" in result.rejection_reason
    prov = result.provenance_rows[0]
    assert prov["source"] == TRADINGVIEW_REJECTED


def test_no_tradingview_data_returns_yahoo_unchanged():
    bridge = TradingViewCompletedDailyBridge()
    yahoo = _yahoo_frame(["2026-07-15", "2026-07-16"])
    result = bridge.merge("COMI.CA", yahoo, None, tv_method="none")
    assert result.sessions_appended == 0
    assert result.tradingview_available is False
    pd.testing.assert_frame_equal(result.frame, yahoo)


def test_reconcile_reports_overlap_without_mutating_yahoo():
    bridge = TradingViewCompletedDailyBridge()
    yahoo = _yahoo_frame(["2026-07-15", "2026-07-16"])
    snapshot = yahoo.copy()
    tv = _tv_frame({
        "2026-07-16": {"Open": 50.1, "High": 51.2, "Low": 48.9, "Close": 50.6, "Volume": 1050},
    })
    records = bridge.reconcile("COMI.CA", yahoo, tv, tv_method="tradingview_csv")
    assert len(records) == 1
    assert records[0].trading_date == "2026-07-16"
    assert records[0].close_diff_pct is not None
    pd.testing.assert_frame_equal(yahoo, snapshot)


def test_multiple_newer_sessions_all_appended_in_order():
    bridge = TradingViewCompletedDailyBridge()
    yahoo = _yahoo_frame(["2026-07-14"])
    tv = _tv_frame({
        "2026-07-15": {"Open": 55, "High": 56, "Low": 54, "Close": 55.5, "Volume": 1500},
        "2026-07-16": {"Open": 56, "High": 57, "Low": 55, "Close": 56.5, "Volume": 1600},
    })
    result = bridge.merge("COMI.CA", yahoo, tv, tv_method="tradingview_webhook")
    assert result.sessions_appended == 2
    assert result.appended_dates == ["2026-07-15", "2026-07-16"]
    assert list(result.frame.index) == sorted(result.frame.index)
