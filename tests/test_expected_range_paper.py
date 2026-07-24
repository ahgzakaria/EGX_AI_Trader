"""Tests for EXPECTED_RANGE_SCALPER safe paper forward recording.

Synthetic + isolated (tmp output, in-memory-style temp Rubix DB). Covers the full
validation list: immutable snapshot, WAIT/rejection recording, READY transition,
duplicate suppression, reset+reactivation, fixed +2%/-2%, chronological
target/stop ordering, no daily-bar inference, no entry after 14:15, auction
separation, stale/wide-spread/range-consumed rejection, paper-on-production-off.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from scalping_expected_range.config import ExpectedRangeConfig
from scalping_expected_range.expected_range import compute_expected_range
from scalping_expected_range.historical_selector import Provenance, SymbolAnalysis
from scalping_expected_range.liquidity_model import compute_liquidity
from scalping_expected_range.paper_recorder import PaperRecorder, normalize_state
from scalping_expected_range.scenario_engine import LiveQuote
from scalping_expected_range.volatility_model import compute_volatility

CAIRO = ZoneInfo("Africa/Cairo")
CFG = ExpectedRangeConfig.load()
SESSION = "2026-07-22"


def _daily(n=40, close=100.0, vol=1_000_000, rng=0.04):
    idx = pd.bdate_range(end="2026-07-20", periods=n)
    rows = [{"Open": close, "High": close * (1 + rng / 2), "Low": close * (1 - rng / 2),
             "Close": close, "Volume": vol} for _ in range(n)]
    return pd.DataFrame(rows, index=idx)


def _analysis(symbol="X", prev_close=100.0, status="OK"):
    daily = _daily(close=prev_close)
    return SymbolAnalysis(
        symbol, prev_close, compute_liquidity(daily, CFG), compute_volatility(daily, CFG),
        compute_expected_range(daily, CFG, prev_close=prev_close),
        Provenance(data_status=status, latest_completed_session="2026-07-20",
                   historical_provider="local_cache:yahoo"))


def _universe_row(symbol="X"):
    a = _analysis(symbol)
    row = {"Symbol": symbol, "Rank": 1, "EXPECTED_RANGE_SCALPING_SCORE": 80,
           "AvgVolumeScore": 90, "VolatilityScore": 70, "liq_status": "LIQUIDITY_VALID",
           "liq_avg_volume_20": 1e6, "liq_median_volume_20": 1e6,
           "liq_avg_turnover_egp_20": 1e8, "liq_median_turnover_egp_20": 1e8,
           "liq_volume_consistency": 0.9, "vol_adr_percent_20": 4.0, "vol_atr_percent_14": 3.5,
           "vol_target_2pct_frequency": 0.8, "vol_upside_2pct_frequency": 0.5,
           "er_base_expected_low": a.expected.base.expected_low,
           "er_base_expected_high": a.expected.base.expected_high,
           "er_high_volatility_expected_low": a.expected.high_volatility.expected_low,
           "er_high_volatility_expected_high": a.expected.high_volatility.expected_high,
           "er_extreme_expected_low": a.expected.extreme.expected_low,
           "er_extreme_expected_high": a.expected.extreme.expected_high,
           "prov_latest_completed_session": "2026-07-20",
           "prov_historical_provider": "local_cache:yahoo"}
    return pd.DataFrame([row])


def _recorder(tmp_path, now=None, rubix=None):
    return PaperRecorder(CFG, SESSION, rubix_db_path=str(rubix) if rubix else "missing.db",
                         now=now, output_root=str(tmp_path / "paper"),
                         reports_root=str(tmp_path / "reports"))


def _ready_quote(prev_close=100.0):
    # inside the range, two-sided, fresh -> continuation READY
    return LiveQuote(last=prev_close, bid=prev_close * 0.999, ask=prev_close * 1.001,
                     spread_percent=0.2, quote_age_seconds=5.0, volume=500_000,
                     session_low=prev_close * 0.99, available=True,
                     received_at="2026-07-22T10:05:00+00:00",
                     exchange_timestamp="2026-07-22T10:04:50+00:00")


def _now(hh, mm=0):
    return datetime(2026, 7, 22, hh, mm, tzinfo=CAIRO)


# --- config ------------------------------------------------------------------

def test_paper_enabled_production_disabled():
    assert CFG.paper_enabled is True
    assert CFG.production_enabled is False
    assert CFG.automatic_execution is False
    assert CFG.broker_orders_enabled is False


# --- immutable pre-session snapshot -----------------------------------------

def test_pre_session_snapshot_is_immutable(tmp_path):
    rec = _recorder(tmp_path, now=_now(9))
    uni = _universe_row()
    path, h1 = rec.write_presession_snapshot(uni, "2026-07-20")
    first = path.read_text(encoding="utf-8")
    # attempt to rewrite with different data -> must not overwrite
    uni2 = uni.copy(); uni2.loc[0, "er_base_expected_high"] = 999
    path2, h2 = rec.write_presession_snapshot(uni2, "2026-07-20")
    assert path2.read_text(encoding="utf-8") == first     # unchanged
    assert h1 == h2


# --- READY + WAIT + rejection recording -------------------------------------

def test_ready_transition_and_wait_and_rejection_recorded(tmp_path):
    rec = _recorder(tmp_path, now=_now(11))
    analyses = {"X": _analysis("X")}
    live_fn = lambda s: _ready_quote()
    rec.write_presession_snapshot(_universe_row(), "2026-07-20")
    res = rec.evaluate_cycle(analyses, _universe_row(), live_fn, "hash", "2026-07-20")
    scns = {t["Scenario"]: t["ToState"] for t in res["transitions"]}
    assert scns.get("TREND_CONTINUATION") == "READY"          # READY recorded
    sig_scenarios = {s["Scenario"] for s in res["signals"]}
    assert "TREND_CONTINUATION" in sig_scenarios               # decision snapshot made
    # exactly one decision snapshot per READY scenario this cycle (no duplication)
    assert len(res["signals"]) == len(sig_scenarios)
    # non-ready states (WAIT etc.) were recorded too (no selection bias)
    states = {r["State"] for r in res["rejected"]}
    assert "WAIT" in states


def test_fixed_two_percent_target_and_stop_in_signal(tmp_path):
    rec = _recorder(tmp_path, now=_now(11))
    res = rec.evaluate_cycle({"X": _analysis("X")}, _universe_row(),
                             lambda s: _ready_quote(), "hash", "2026-07-20")
    sig = res["signals"][0]
    assert sig["FixedTargetPrice"] == pytest.approx(sig["EntryPrice"] * 1.02, rel=1e-6)
    assert sig["FixedStopPrice"] == pytest.approx(sig["EntryPrice"] * 0.98, rel=1e-6)
    assert sig["ReceiptTimestamp"] and sig["ExchangeTimestamp"]   # timestamps captured


# --- duplicate suppression + reset/reactivation -----------------------------

def test_duplicate_ready_suppressed_same_cycle(tmp_path):
    rec = _recorder(tmp_path, now=_now(11))
    uni = _universe_row()
    rec.evaluate_cycle({"X": _analysis("X")}, uni, lambda s: _ready_quote(), "h", "2026-07-20")
    # second cycle, still READY -> no new signal
    rec2 = _recorder(tmp_path, now=_now(11, 5))
    res2 = rec2.evaluate_cycle({"X": _analysis("X")}, uni, lambda s: _ready_quote(), "h", "2026-07-20")
    assert len(res2["signals"]) == 0


def test_reset_and_new_activation_creates_new_signal(tmp_path):
    uni = _universe_row()
    # cycle 1: READY
    _recorder(tmp_path, now=_now(11)).evaluate_cycle(
        {"X": _analysis("X")}, uni, lambda s: _ready_quote(), "h", "2026-07-20")
    # cycle 2: goes to WAIT (price below lower zone, not confirmed) -> resets
    wait_q = LiveQuote(last=90.0, bid=89.9, ask=90.1, spread_percent=0.2,
                       quote_age_seconds=5, available=True, session_low=95,
                       received_at="t", exchange_timestamp="t")
    _recorder(tmp_path, now=_now(11, 5)).evaluate_cycle(
        {"X": _analysis("X")}, uni, lambda s: wait_q, "h", "2026-07-20")
    # cycle 3: READY again -> new activation cycle -> new signal
    res3 = _recorder(tmp_path, now=_now(11, 10)).evaluate_cycle(
        {"X": _analysis("X")}, uni, lambda s: _ready_quote(), "h", "2026-07-20")
    cont = [s for s in res3["signals"] if s["Scenario"] == "TREND_CONTINUATION"]
    assert len(cont) == 1                                        # a genuinely new signal
    assert int(cont[0]["ActivationCycle"]) == 2                  # second activation


# --- 14:15 no-new-entry ------------------------------------------------------

def test_no_new_entry_after_1415(tmp_path):
    rec = _recorder(tmp_path, now=_now(14, 20))          # inside auction window
    res = rec.evaluate_cycle({"X": _analysis("X")}, _universe_row(),
                             lambda s: _ready_quote(), "h", "2026-07-20")
    assert len(res["signals"]) == 0                       # blocked
    notes = {t.get("Note") for t in res["transitions"]}
    assert "NO_NEW_ENTRY_AFTER_1415" in notes


# --- rejection states --------------------------------------------------------

def test_stale_quote_rejected(tmp_path):
    stale = LiveQuote(last=100, bid=99.9, ask=100.1, spread_percent=0.2,
                      quote_age_seconds=999, available=True, received_at="t", exchange_timestamp="t")
    res = _recorder(tmp_path, now=_now(11)).evaluate_cycle(
        {"X": _analysis("X")}, _universe_row(), lambda s: stale, "h", "2026-07-20")
    assert len(res["signals"]) == 0
    assert any(r["State"] == "DATA_STALE" for r in res["rejected"])


def test_wide_spread_rejected(tmp_path):
    wide = LiveQuote(last=100, bid=99, ask=101, spread_percent=2.0,
                     quote_age_seconds=5, available=True, received_at="t", exchange_timestamp="t")
    res = _recorder(tmp_path, now=_now(11)).evaluate_cycle(
        {"X": _analysis("X")}, _universe_row(), lambda s: wide, "h", "2026-07-20")
    assert any(r["State"] == "SPREAD_TOO_WIDE" for r in res["rejected"])
    assert len(res["signals"]) == 0


def test_range_consumed_rejected(tmp_path):
    a = _analysis("X")
    high = a.expected.high_volatility.expected_high
    consumed = LiveQuote(last=high * 1.01, bid=high * 1.005, ask=high * 1.015,
                         spread_percent=0.2, quote_age_seconds=5, available=True,
                         received_at="t", exchange_timestamp="t")
    res = _recorder(tmp_path, now=_now(11)).evaluate_cycle(
        {"X": a}, _universe_row(), lambda s: consumed, "h", "2026-07-20")
    assert any(r["State"] == "RANGE_CONSUMED" for r in res["rejected"])


def test_normalize_state_vocabulary():
    assert normalize_state("BOUNCE_READY") == "READY"
    assert normalize_state("BOUNCE_WAIT") == "WAIT"
    assert normalize_state("SPREAD_TOO_WIDE") == "SPREAD_TOO_WIDE"
    assert normalize_state("RANGE_CONSUMED") == "RANGE_CONSUMED"


# --- outcome engine: chronological ordering, no daily-bar inference ----------

def _temp_rubix(tmp_path, ticker, decision_iso, series):
    """series: list of (offset_seconds, last, bid, ask) after decision."""
    db = tmp_path / "rubix.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE quotes (ticker TEXT, last_price REAL, bid REAL, ask REAL, "
                 "volume REAL, market_timestamp TEXT, received_at TEXT)")
    base = pd.to_datetime(decision_iso, utc=True)
    for off, last, bid, ask in series:
        ts = (base + timedelta(seconds=off)).isoformat()
        conn.execute("INSERT INTO quotes VALUES (?,?,?,?,?,?,?)",
                     (ticker, last, bid, ask, 1000, ts, ts))
    conn.commit(); conn.close()
    return db


def test_outcome_target_before_stop_chronological(tmp_path):
    decision = "2026-07-22T10:05:00+00:00"
    # price rises to +2% (target) at 60s, would hit stop only later -> TARGET first
    db = _temp_rubix(tmp_path, "X", decision, [
        (10, 100.5, 100.4, 100.6), (60, 102.1, 102.0, 102.2), (120, 97.5, 97.4, 97.6)])
    rec = _recorder(tmp_path, now=_now(11), rubix=db)
    sig = {"SignalUUID": "u1", "SessionDate": SESSION, "Symbol": "X",
           "Scenario": "TREND_CONTINUATION", "ActivationCycle": 1,
           "DecisionTimestampCairo": decision, "EntryPrice": 100.0, "Last": 100.0,
           "FixedTargetPrice": 102.0, "FixedStopPrice": 98.0}
    oc = rec.record_outcomes([sig])[0]
    assert oc["FirstHit"] == "TARGET"
    assert oc["TargetHit"] is True
    assert oc["TimeToTargetSec"] == 60.0


def test_outcome_stop_before_target_chronological(tmp_path):
    decision = "2026-07-22T10:05:00+00:00"
    db = _temp_rubix(tmp_path, "X", decision, [
        (10, 99.5, 99.4, 99.6), (40, 97.9, 97.8, 98.0), (120, 102.5, 102.4, 102.6)])
    rec = _recorder(tmp_path, now=_now(11), rubix=db)
    sig = {"SignalUUID": "u2", "SessionDate": SESSION, "Symbol": "X",
           "Scenario": "TREND_CONTINUATION", "ActivationCycle": 1,
           "DecisionTimestampCairo": decision, "EntryPrice": 100.0, "Last": 100.0,
           "FixedTargetPrice": 102.0, "FixedStopPrice": 98.0}
    oc = rec.record_outcomes([sig])[0]
    assert oc["FirstHit"] == "STOP"                  # stop occurred first chronologically
    assert oc["TimeToStopSec"] == 40.0


def test_outcome_auction_stored_separately(tmp_path):
    decision = "2026-07-22T10:05:00+00:00"   # 13:05 Cairo
    # a continuous quote, then an auction quote at 14:20 Cairo (11:20 UTC)
    db = tmp_path / "rubix.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE quotes (ticker TEXT, last_price REAL, bid REAL, ask REAL, "
                 "volume REAL, market_timestamp TEXT, received_at TEXT)")
    conn.execute("INSERT INTO quotes VALUES (?,?,?,?,?,?,?)",
                 ("X", 100.5, 100.4, 100.6, 1000, "2026-07-22T10:06:00+00:00",
                  "2026-07-22T10:06:00+00:00"))
    conn.execute("INSERT INTO quotes VALUES (?,?,?,?,?,?,?)",
                 ("X", 103.0, 102.9, 103.1, 1000, "2026-07-22T11:20:00+00:00",
                  "2026-07-22T11:20:00+00:00"))   # auction (14:20 Cairo)
    conn.commit(); conn.close()
    rec = _recorder(tmp_path, now=_now(15), rubix=db)
    sig = {"SignalUUID": "u3", "SessionDate": SESSION, "Symbol": "X",
           "Scenario": "TREND_CONTINUATION", "ActivationCycle": 1,
           "DecisionTimestampCairo": decision, "EntryPrice": 100.0, "Last": 100.0,
           "FixedTargetPrice": 102.0, "FixedStopPrice": 98.0}
    oc = rec.record_outcomes([sig])[0]
    # auction (+3%) is stored separately and NOT counted as the continuous target hit
    assert oc["AuctionResult%"] == pytest.approx(3.0, abs=0.01)
    assert oc["FirstHit"] == "NEITHER"       # continuous window never hit +2%


def test_outcome_uses_quotes_only_not_daily_bars(tmp_path):
    # No quotes after decision -> outcome cannot be computed from daily H/L.
    db = _temp_rubix(tmp_path, "X", "2026-07-22T10:05:00+00:00", [])
    rec = _recorder(tmp_path, now=_now(11), rubix=db)
    sig = {"SignalUUID": "u4", "SessionDate": SESSION, "Symbol": "X",
           "Scenario": "TREND_CONTINUATION", "ActivationCycle": 1,
           "DecisionTimestampCairo": "2026-07-22T10:05:00+00:00", "EntryPrice": 100.0,
           "Last": 100.0, "FixedTargetPrice": 102.0, "FixedStopPrice": 98.0}
    oc = rec.record_outcomes([sig])[0]
    assert oc["DataQualityWarning"] == "NO_QUOTES_AFTER_DECISION"
    assert oc["ExecutableEntryAvailable"] == "No"
