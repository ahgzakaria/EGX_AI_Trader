"""What a live quote may and may not authorise, and what it may never change.

The collector, the launcher and the subscription plan these used to cover went
with the feed on 2026-09-10. What survived is the rule they existed to protect:
a strategy signal is one thing and permission to act on it is another, and the
freshness of a quote decides only the second. That distinction outlives the
feed that motivated it, and the next live source will need it unchanged.
"""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pandas as pd

from core.live_actionability import assess_live_actionability
from forward_testing.database import ForwardDatabase
from forward_testing.service import ForwardTestingService


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


