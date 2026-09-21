"""The forward record for Swing Breakout and Breakout Watch.

Two pages rendered and forgot, so neither rule could be scored on its live
record. These pin the parts that decide what the record will say later: when a
candidate may be resolved, what it is entered at, what it is compared with, and
that nothing written can be rewritten after a disappointing week.
"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from services import swing_breakout_forward as forward


SESSION = "2026-09-01"


def store(tmp_path):
    return forward.SwingForwardStore(tmp_path / "swing_forward.db")


def candidate(symbol="COMI.CA", session=SESSION, held=20):
    return {
        "candidate_id": forward._identifier(forward.SWING, session, symbol),
        "dedupe_key": f"{forward.SWING}|{session}|{symbol}",
        "session_date": session, "recorded_at": "2026-09-01T16:00:00+03:00",
        "symbol": symbol, "close": 100.0, "breakout_level": 95.0,
        "volume_ratio": 3.0, "momentum_12_1": 0.4, "momentum_rank": 0.9,
        "atr_percent": 2.0, "extension_percent": 5.26,
        "holding_sessions": held, "config_hash": "abc123",
    }


def watch_row(symbol="ABUK.CA", session=SESSION, window=None):
    return {
        "watch_id": forward._identifier(forward.WATCH, session, symbol),
        "dedupe_key": f"{forward.WATCH}|{session}|{symbol}",
        "session_date": session, "recorded_at": "2026-09-01T16:00:00+03:00",
        "symbol": symbol, "close": 90.0, "prior_high": 95.0,
        "distance_percent": 5.5, "distance_atr": 0.8, "turnover_egp": 9e6,
        "atr_percent": 2.4,
        "window_sessions": forward.WATCH_WINDOW if window is None else window,
        "config_hash": "abc123",
    }


def session_row(source=forward.SWING, session=SESSION, count=1):
    return {
        "session_id": forward._identifier(source, session),
        "session_date": session, "recorded_at": "2026-09-01T16:00:00+03:00",
        "source": source, "symbols_considered": 140, "symbols_unreadable": 2,
        "candidate_count": count, "funnel_json": "{}", "config_hash": "abc123",
        "config_json": "{}",
    }


def closes(first=SESSION, values=(100.0,)):
    index = pd.bdate_range(first, periods=len(values))
    return pd.Series(list(values), index=index, dtype="float64")


# --- the record cannot be rewritten --------------------------------------------------

def test_recording_the_same_session_twice_writes_nothing_new(tmp_path):
    keeper = store(tmp_path)
    first = keeper.record_session(session_row(), candidates=[candidate()],
                                  watch_candidates=[watch_row()])
    second = keeper.record_session(session_row(), candidates=[candidate()],
                                   watch_candidates=[watch_row()])
    assert (first["candidates"], first["watch"]) == (1, 1)
    assert (second["candidates"], second["watch"]) == (0, 0)
    assert len(keeper.rows("SELECT * FROM candidates")) == 1


def test_a_recorded_candidate_can_never_be_edited_or_deleted(tmp_path):
    import sqlite3

    keeper = store(tmp_path)
    keeper.record_session(session_row(), candidates=[candidate()])
    with pytest.raises(sqlite3.IntegrityError):
        with keeper._connect() as connection:
            connection.execute("UPDATE candidates SET close = 1.0")
    with pytest.raises(sqlite3.IntegrityError):
        with keeper._connect() as connection:
            connection.execute("DELETE FROM candidates")


# --- when a candidate may be scored ---------------------------------------------------

def resolver(tmp_path, monkeypatch, series, panel_return=1.0):
    test = forward.SwingForwardTest(store=store(tmp_path))
    test.fingerprint = "abc123"
    monkeypatch.setattr(forward, "measured_closes", lambda symbol: series)
    monkeypatch.setattr("core.measured_benchmark.close_panel", lambda *a, **k: "panel")
    monkeypatch.setattr("core.measured_benchmark.median_return",
                        lambda panel, session, horizon: panel_return)
    monkeypatch.setattr("core.effective_cost.load_symbol_costs", lambda *a, **k: {})
    monkeypatch.setattr("core.effective_cost.round_trip_for", lambda costs, symbol: 0.5)
    return test


def test_a_window_that_has_not_finished_is_left_running(tmp_path, monkeypatch):
    # The session itself plus 20 more: entry is the next close, so the exit
    # bar has not happened yet.
    series = closes(values=[100.0] * 21)
    test = resolver(tmp_path, monkeypatch, series)
    test.store.record_session(session_row(), candidates=[candidate()])

    counts = test.resolve()
    assert counts["still_running"] == 1 and counts["resolved"] == 0
    assert test.store.rows("SELECT * FROM outcomes") == []


def test_a_finished_window_is_entered_at_the_next_close_and_scored(tmp_path, monkeypatch):
    # Session, then 21 more bars: entry at bar 1 (110), exit 20 later (132).
    series = closes(values=[100.0, 110.0] + [120.0] * 19 + [132.0])
    test = resolver(tmp_path, monkeypatch, series, panel_return=2.0)
    test.store.record_session(session_row(), candidates=[candidate()])

    counts = test.resolve()
    assert counts["resolved"] == 1
    [outcome] = test.store.rows("SELECT * FROM outcomes")
    assert outcome["entry_price"] == pytest.approx(110.0)
    assert outcome["exit_price"] == pytest.approx(132.0)
    # 20% gross, less the measured 0.5% round trip, less the median symbol's 2%.
    assert outcome["net_percent"] == pytest.approx(19.5, abs=0.01)
    assert outcome["lift_percent"] == pytest.approx(17.5, abs=0.01)
    assert outcome["status"] == "CLOSED"


def test_a_candidate_from_another_calibration_is_not_pooled(tmp_path, monkeypatch):
    series = closes(values=[100.0, 110.0] + [120.0] * 19 + [132.0])
    test = resolver(tmp_path, monkeypatch, series)
    stale = {**candidate(), "config_hash": "a-different-calibration"}
    test.store.record_session(session_row(), candidates=[stale])

    counts = test.resolve()
    assert counts["stale_config"] == 1 and counts["resolved"] == 0


def test_a_symbol_the_measured_record_cannot_answer_for_is_counted(tmp_path, monkeypatch):
    test = resolver(tmp_path, monkeypatch, None)
    test.store.record_session(session_row(), candidates=[candidate()])

    counts = test.resolve()
    assert counts["unreadable"] == 1 and counts["resolved"] == 0


# --- the watch list is a conversion question, not a return ----------------------------

def test_a_watch_name_that_clears_its_high_inside_the_window_triggered(tmp_path, monkeypatch):
    series = closes(values=[90.0, 92.0, 96.0] + [97.0] * 9)
    test = resolver(tmp_path, monkeypatch, series)
    test.store.record_session(session_row(source=forward.WATCH),
                              watch_candidates=[watch_row()])

    counts = test.resolve()
    assert counts["watch_resolved"] == 1
    [outcome] = test.store.rows("SELECT * FROM watch_outcomes")
    assert outcome["status"] == "TRIGGERED"
    assert outcome["sessions_to_trigger"] == 2


def test_a_watch_name_that_never_clears_it_expires(tmp_path, monkeypatch):
    series = closes(values=[90.0] + [91.0] * 11)
    test = resolver(tmp_path, monkeypatch, series)
    test.store.record_session(session_row(source=forward.WATCH),
                              watch_candidates=[watch_row()])

    counts = test.resolve()
    assert counts["watch_resolved"] == 1
    [outcome] = test.store.rows("SELECT * FROM watch_outcomes")
    assert outcome["status"] == "EXPIRED"
    assert outcome["sessions_to_trigger"] is None


def test_a_watch_window_still_open_is_not_called_expired(tmp_path, monkeypatch):
    series = closes(values=[90.0, 91.0, 92.0])
    test = resolver(tmp_path, monkeypatch, series)
    test.store.record_session(session_row(source=forward.WATCH),
                              watch_candidates=[watch_row()])

    counts = test.resolve()
    assert counts["watch_still_running"] == 1
    assert test.store.rows("SELECT * FROM watch_outcomes") == []


# --- the recorder calls the pages' own entry points ------------------------------------

def test_recording_writes_what_the_scan_named(tmp_path, monkeypatch):
    scanned = SimpleNamespace(
        candidates=(SimpleNamespace(
            symbol="COMI.CA", session_date="2026-09-18", close=100.0,
            breakout_level=95.0, volume_ratio=3.0, momentum_12_1=0.4,
            momentum_rank=0.9, atr_percent=2.0, extension_percent=5.26),),
        symbols_considered=140, symbols_skipped={"X.CA": "VOLUME_BELOW_GATE"})
    watched = SimpleNamespace(
        candidates=[SimpleNamespace(
            symbol="ABUK.CA", close=90.0, prior_high=95.0,
            distance_percent=5.5, distance_atr=0.8, turnover_egp=9e6,
            atr_percent=2.4)],
        considered=140, unreadable={}, funnel={"Calm": 3})

    monkeypatch.setattr("services.swing_breakout.most_traded", lambda h, *a, **k: h)
    monkeypatch.setattr("services.swing_breakout.scan", lambda *a, **k: scanned)

    test = forward.SwingForwardTest(store=store(tmp_path))
    histories = {"COMI.CA": pd.DataFrame(
        {"Close": [1.0]}, index=pd.to_datetime(["2026-09-18"]))}
    result = test.record(histories=histories, watch_result=watched)

    assert result["session"] == "2026-09-18"
    assert (result["candidates"], result["watch"]) == (1, 1)
    [row] = test.store.rows("SELECT * FROM candidates")
    assert row["symbol"] == "COMI.CA" and row["holding_sessions"] == 20
    [row] = test.store.rows("SELECT * FROM watch_candidates")
    assert row["symbol"] == "ABUK.CA" and row["window_sessions"] == forward.WATCH_WINDOW
    assert {r["source"] for r in test.store.rows("SELECT source FROM sessions")} == {
        forward.SWING, forward.WATCH}
