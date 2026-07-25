"""Tests for the three-stage live paper orchestration (durable, restart-safe).

Covers: snapshot timing/status, session classification, horizon maturity (PENDING
vs NEITHER vs UNAVAILABLE_SESSION_END), durable cursor persistence, monitor
restart without duplicate signal, activation-cycle persistence, no-entry-after-
14:15, single-instance lock, transition suppression, finalizer idempotency, and
partial/pilot exclusion from the forward-session count.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import sqlite3
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from scalping_expected_range.config import ExpectedRangeConfig
from scalping_expected_range.expected_range import compute_expected_range
from scalping_expected_range.historical_selector import Provenance, SymbolAnalysis
from scalping_expected_range.liquidity_model import compute_liquidity
from scalping_expected_range.live_monitor import LiveMonitor, SingleInstanceLock
from scalping_expected_range.orchestration import (
    COMPLETE_FORWARD_SESSION,
    HISTORY_STALE,
    MATURED,
    MONITOR_NOT_RUNNING,
    NON_TRADING_DAY,
    PARTIAL_LATE_START,
    PENDING,
    PILOT_SESSION,
    SNAPSHOT_LATE,
    SNAPSHOT_VALID,
    UNAVAILABLE_SESSION_END,
    classify_session,
    horizon_status,
    snapshot_status,
)
from scalping_expected_range.paper_state import PaperStateStore
from scalping_expected_range.volatility_model import compute_volatility

CAIRO = ZoneInfo("Africa/Cairo")
CFG = ExpectedRangeConfig.load()
SESSION = "2026-07-22"


def _analysis():
    idx = pd.bdate_range(end="2026-07-20", periods=40)
    daily = pd.DataFrame([{"Open": 100, "High": 102, "Low": 98, "Close": 100, "Volume": 1e6}
                          for _ in range(40)], index=idx)
    return SymbolAnalysis("COMI.CA", 100.0, compute_liquidity(daily, CFG),
                          compute_volatility(daily, CFG),
                          compute_expected_range(daily, CFG, prev_close=100.0),
                          Provenance(data_status="OK"))


SNAP = {"COMI.CA": {"Symbol": "COMI.CA", "CoreLow": 99, "CoreHigh": 101, "ExpansionLow": 98,
                    "ExpansionHigh": 102, "ExtremeLow": 97, "ExtremeHigh": 103,
                    "CombinedRank": 1, "DatasetHash": "h", "HistoricalDataThrough": "2026-07-20"}}


class _Reader:
    """Returns a queued batch per poll (ignores cursor to exercise suppression)."""
    def __init__(self, batches):
        self.batches = list(batches)
    def new_quotes_since(self, cursor, limit=1):
        return self.batches.pop(0) if self.batches else []


def _q(last, recv, bid=None, ask=None):
    bid = bid if bid is not None else last * 0.999
    ask = ask if ask is not None else last * 1.001
    return {"ticker": "COMI", "last_price": last, "bid": bid, "ask": ask, "volume": 5e5,
            "market_timestamp": recv, "received_at": recv}


def _now(hh, mm=0):
    return datetime(2026, 7, 22, hh, mm, tzinfo=CAIRO)


def _monitor(tmp_path, reader):
    state = PaperStateStore(str(tmp_path / "state.db"))
    return LiveMonitor(CFG, SESSION, {"COMI.CA": _analysis()}, SNAP, state_store=state,
                       rubix_reader=reader, output_root=str(tmp_path / "paper"),
                       reports_root=str(tmp_path / "reports")), state


# --- snapshot status ---------------------------------------------------------

def test_snapshot_valid_before_10():
    assert snapshot_status(time(9, 45), "HISTORY_CURRENT", True) == SNAPSHOT_VALID


def test_snapshot_late_after_10():
    assert snapshot_status(time(10, 30), "HISTORY_CURRENT", True) == SNAPSHOT_LATE


def test_snapshot_stale_history():
    assert snapshot_status(time(9, 45), "HISTORY_STALE", True) == HISTORY_STALE


def test_snapshot_non_trading_day():
    assert snapshot_status(time(9, 45), "HISTORY_CURRENT", False) == NON_TRADING_DAY


# --- session classification --------------------------------------------------

def test_complete_forward_session():
    m = {"snapshot_status": SNAPSHOT_VALID}
    assert classify_session(manifest=m, cursor_last_received_cairo_time=time(14, 16),
                            monitor_ran=True) == COMPLETE_FORWARD_SESSION


def test_partial_late_start():
    m = {"snapshot_status": SNAPSHOT_LATE}
    assert classify_session(manifest=m, cursor_last_received_cairo_time=time(14, 16),
                            monitor_ran=True) == PARTIAL_LATE_START


def test_monitor_not_running():
    m = {"snapshot_status": SNAPSHOT_VALID}
    assert classify_session(manifest=m, cursor_last_received_cairo_time=None,
                            monitor_ran=False) == MONITOR_NOT_RUNNING


def test_pilot_classification_wins():
    m = {"snapshot_status": SNAPSHOT_VALID}
    assert classify_session(manifest=m, cursor_last_received_cairo_time=time(14, 16),
                            monitor_ran=True, is_pilot=True) == PILOT_SESSION


def test_stale_history_snapshot_does_not_count():
    from scalping_expected_range.orchestration import INVALID_SNAPSHOT
    m = {"snapshot_status": HISTORY_STALE}
    # even with full monitor coverage, a stale-history basis is not a counted session
    assert classify_session(manifest=m, cursor_last_received_cairo_time=time(14, 16),
                            monitor_ran=True) == INVALID_SNAPSHOT


# --- horizon maturity --------------------------------------------------------

def _dt(hh, mm):
    return datetime(2026, 7, 22, hh, mm, tzinfo=timezone.utc)


def test_horizon_unavailable_past_continuous_close():
    # signal at 14:05 Cairo (11:05 UTC), 20-min horizon -> 14:25 > 14:15 close.
    decision = datetime(2026, 7, 22, 11, 5, tzinfo=timezone.utc)
    cont_end = datetime(2026, 7, 22, 11, 15, tzinfo=timezone.utc)   # 14:15 Cairo
    assert horizon_status(decision, 20, cont_end, decision + timedelta(minutes=5),
                          now_dt=decision + timedelta(hours=1)) == UNAVAILABLE_SESSION_END


def test_horizon_pending_when_not_elapsed():
    decision = datetime(2026, 7, 22, 10, 5, tzinfo=timezone.utc)
    cont_end = datetime(2026, 7, 22, 11, 15, tzinfo=timezone.utc)
    # now only 2 minutes later -> 5-min horizon still forming -> PENDING (not NEITHER)
    assert horizon_status(decision, 5, cont_end, decision + timedelta(minutes=2),
                          now_dt=decision + timedelta(minutes=2)) == PENDING


def test_horizon_matured():
    decision = datetime(2026, 7, 22, 10, 5, tzinfo=timezone.utc)
    cont_end = datetime(2026, 7, 22, 11, 15, tzinfo=timezone.utc)
    assert horizon_status(decision, 5, cont_end, decision + timedelta(minutes=6),
                          now_dt=decision + timedelta(minutes=30)) == MATURED


# --- durable cursor + restart-no-duplicate -----------------------------------

def test_cursor_persists_across_instances(tmp_path):
    st = PaperStateStore(str(tmp_path / "s.db"))
    st.set_cursor(SESSION, "2026-07-22T10:06:00+00:00")
    st2 = PaperStateStore(str(tmp_path / "s.db"))
    assert st2.get_cursor(SESSION) == "2026-07-22T10:06:00+00:00"


def test_monitor_restart_no_duplicate_signal(tmp_path):
    # cycle 1 READY -> signal. "restart" (new monitor, same DB), still READY -> no dup.
    reader1 = _Reader([[_q(100.0, "2026-07-22T10:06:00+00:00")]])
    m1, st = _monitor(tmp_path, reader1)
    m1.poll_once(_now(11))
    assert len(st.signals(SESSION)) == 1
    # restart: new LiveMonitor + new PaperStateStore on the SAME db file
    reader2 = _Reader([[_q(100.0, "2026-07-22T10:07:00+00:00")]])   # still inside range, READY
    m2 = LiveMonitor(CFG, SESSION, {"COMI.CA": _analysis()}, SNAP,
                     state_store=PaperStateStore(str(tmp_path / "state.db")),
                     rubix_reader=reader2, output_root=str(tmp_path / "paper"),
                     reports_root=str(tmp_path / "reports"))
    m2.poll_once(_now(11, 2))
    assert len(PaperStateStore(str(tmp_path / "state.db")).signals(SESSION)) == 1   # no dup


def test_reset_and_reactivation_new_cycle_persists(tmp_path):
    reader = _Reader([
        [_q(100.0, "2026-07-22T10:06:00+00:00")],   # READY cycle 1
        [_q(90.0, "2026-07-22T10:07:00+00:00")],    # falls below -> WAIT (reset)
        [_q(100.0, "2026-07-22T10:08:00+00:00")],   # READY again -> cycle 2
    ])
    m, st = _monitor(tmp_path, reader)
    m.poll_once(_now(11)); m.poll_once(_now(11, 1)); m.poll_once(_now(11, 2))
    sigs = st.signals(SESSION)
    cont = [s for s in sigs if s["Scenario"] == "TREND_CONTINUATION"]
    assert len(cont) == 2
    assert max(int(s["ActivationCycle"]) for s in cont) == 2


# --- no new entry after 14:15 ------------------------------------------------

def test_no_new_entry_after_1415(tmp_path):
    reader = _Reader([[_q(100.0, "2026-07-22T11:20:00+00:00")]])
    m, st = _monitor(tmp_path, reader)
    m.poll_once(_now(14, 20))
    assert len(st.signals(SESSION)) == 0
    notes = {t["note"] for t in st.transitions(SESSION)}
    assert "NO_NEW_ENTRY_AFTER_1415" in notes


# --- transition suppression --------------------------------------------------

def test_unchanged_evaluations_suppressed(tmp_path):
    reader = _Reader([
        [_q(100.0, "2026-07-22T10:06:00+00:00")],
        [_q(100.0, "2026-07-22T10:07:00+00:00")],   # same state -> suppressed
    ])
    m, st = _monitor(tmp_path, reader)
    m.poll_once(_now(11)); m.poll_once(_now(11, 1))
    counters = st.counters(SESSION)
    assert counters["unchanged_suppressed"] >= 7        # 2nd poll's 7 scenarios unchanged
    assert counters["transitions_recorded"] == 7        # only the 1st poll changed states


# --- single-instance lock ----------------------------------------------------

def test_single_instance_lock(tmp_path):
    a = SingleInstanceLock(tmp_path / "m.lock")
    b = SingleInstanceLock(tmp_path / "m.lock")
    assert a.acquire() is True
    assert b.acquire() is False           # second instance blocked
    a.release()
    assert b.acquire() is True            # released -> now available
    b.release()


# --- finalizer idempotency + partial exclusion -------------------------------

def test_finalizer_outcomes_idempotent(tmp_path):
    # Two record_outcomes calls must not duplicate an outcome row.
    from scalping_expected_range.paper_recorder import PaperRecorder
    db = tmp_path / "rubix.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE quotes (ticker TEXT, last_price REAL, bid REAL, ask REAL, "
                 "volume REAL, market_timestamp TEXT, received_at TEXT)")
    conn.execute("INSERT INTO quotes VALUES('COMI',100.5,100.4,100.6,1000,"
                 "'2026-07-22T10:06:00+00:00','2026-07-22T10:06:00+00:00')")
    conn.commit(); conn.close()
    rec = PaperRecorder(CFG, SESSION, rubix_db_path=str(db), now=_now(15),
                        output_root=str(tmp_path / "paper"), reports_root=str(tmp_path / "reports"))
    sig = [{"SignalUUID": "u1", "SessionDate": SESSION, "Symbol": "COMI.CA",
            "Scenario": "TREND_CONTINUATION", "ActivationCycle": 1,
            "DecisionTimestampCairo": "2026-07-22T10:05:00+00:00", "EntryPrice": 100.0,
            "Last": 100.0, "FixedTargetPrice": 102.0, "FixedStopPrice": 98.0}]
    # seed rolling signals csv so record_outcomes() (no arg) can read it
    rec._append(rec.signals_csv, __import__("scalping_expected_range.paper_recorder",
                fromlist=["SIGNAL_FIELDS"]).SIGNAL_FIELDS, sig)
    first = rec.record_outcomes()
    second = rec.record_outcomes()
    assert len(first) == 1 and len(second) == 0        # idempotent


def test_partial_and_pilot_excluded_from_forward_count(tmp_path, monkeypatch):
    import scalping_expected_range.paper_evidence as pe
    summary = tmp_path / "daily.csv"
    summary.write_text(
        "SessionDate,Classification\n"
        "2026-07-22,PILOT_SESSION\n2026-07-23,PARTIAL_LATE_START\n2026-07-26,COMPLETE_FORWARD_SESSION\n",
        encoding="utf-8")
    monkeypatch.setattr(pe, "SUMMARY_CSV", str(summary))
    classes = pe._session_classifications(str(summary))
    complete = [d for d, c in classes.items() if c == COMPLETE_FORWARD_SESSION]
    assert complete == ["2026-07-26"]                  # only the complete session counts


def test_evidence_counts_complete_sessions_only(tmp_path, monkeypatch):
    import scalping_expected_range.paper_evidence as pe
    summary = tmp_path / "daily.csv"
    summary.write_text(
        "SessionDate,Classification\n"
        "2026-07-22,PILOT_SESSION\n2026-07-23,PARTIAL_LATE_START\n"
        "2026-07-26,COMPLETE_FORWARD_SESSION\n2026-07-27,COMPLETE_FORWARD_SESSION\n",
        encoding="utf-8")
    monkeypatch.setattr(pe, "SUMMARY_CSV", str(summary))
    monkeypatch.setattr(pe, "SIGNALS_CSV", str(tmp_path / "none.csv"))   # no signals yet
    ev = pe.scenario_evidence(cfg=CFG)
    overall = ev["overall"]
    assert overall["complete_forward_sessions"] == 2   # only COMPLETE count
    assert overall["pilot_sessions"] == 1
    assert overall["partial_sessions"] == 1
    assert overall["production_recommended"] is False
