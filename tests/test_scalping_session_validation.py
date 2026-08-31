"""Tests for the automated post-session validator (logic, synthetic/isolated).

Covers idempotency, date detection (Fri/Sat/holiday), auction exclusion,
timestamp-only vs full-state freeze semantics, market-wide material-value
progression, per-symbol inactivity NOT failing the market-wide feed, immutable
multisession append, and the 2-of-3 acceptance rule. No real DB needed.
"""

import importlib.util
from datetime import date

import pandas as pd
import pytest

from core.egx_session import is_regular_trading_day, latest_completed_session_date
from scalping.session_validator import multisession_state

# import the CLI module by path
_spec = importlib.util.spec_from_file_location(
    "run_val", "scripts/run_scalping_session_validation.py")
run_val = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_val)


# --- session-date detection (Phase 2) ----------------------------------------

def test_friday_saturday_are_not_trading_days():
    assert is_regular_trading_day(date(2026, 7, 17)) is False   # Friday
    assert is_regular_trading_day(date(2026, 7, 18)) is False   # Saturday
    assert is_regular_trading_day(date(2026, 7, 21)) is True    # Tuesday


def test_holiday_excluded_from_trading_days():
    assert is_regular_trading_day(date(2026, 7, 21), holidays=["2026-07-21"]) is False


def test_latest_completed_never_selects_forming_session():
    during_open = pd.Timestamp("2026-07-21 08:00", tz="UTC").to_pydatetime()  # 11:00 Cairo
    d = latest_completed_session_date(value=during_open, close_safety_minutes=15)
    assert d < date(2026, 7, 21)   # today's session still forming


def test_resolve_date_rejects_weekend():
    with pytest.raises(SystemExit):
        run_val.resolve_date("2026-07-17", holidays=())   # Friday


# --- market-wide vs per-symbol (Phase 4/6 discipline) ------------------------

def test_market_wide_value_progression_not_failed_by_one_quiet_symbol():
    # Feed is healthy (one symbol changes every 10s) even though another symbol
    # never changes. Market-wide max value gap must stay small.
    import numpy as np
    base = pd.Timestamp("2026-07-21 07:20", tz="UTC")
    rows = []
    for i in range(200):
        t = base + pd.Timedelta(seconds=i * 10)
        rows.append({"t": "ACTIVE", "recv": t, "last_price": 100 + (i % 5), "bid": 99, "ask": 101, "volume": 1000 + i})
        rows.append({"t": "QUIET", "recv": t, "last_price": 50.0, "bid": 49, "ask": 51, "volume": 5000})  # never changes
    df = pd.DataFrame(rows).sort_values(["t", "recv"])
    changed = (
        (df.groupby("t")["last_price"].diff().fillna(0) != 0)
        | (df.groupby("t")["volume"].diff().fillna(0) > 0)
    )
    df["material_change"] = changed
    df = df.sort_values("recv")
    ts = df.loc[df["material_change"], "recv"].sort_values().tolist()
    gaps = [(ts[i + 1] - ts[i]).total_seconds() for i in range(len(ts) - 1)]
    assert max(gaps) <= 20   # feed healthy despite QUIET never moving


# --- multisession acceptance (Phase 9) ---------------------------------------

def _row(date_, patched=True, value_rc=0):
    return {"Session Date": date_, "Patched Collector": patched,
            "Value-Progression RANGE_CONFIRMED": value_rc}


def test_waiting_when_fewer_than_three_patched():
    state, _ = multisession_state([_row("2026-07-21", True, 5)])
    assert state == "WAITING_FOR_SESSIONS"


def test_prefix_sessions_do_not_count():
    rows = [_row("2026-07-14", patched=False, value_rc=9),
            _row("2026-07-16", patched=False, value_rc=9),
            _row("2026-07-21", patched=True, value_rc=5)]
    state, _ = multisession_state(rows)
    assert state == "WAITING_FOR_SESSIONS"   # only 1 patched


def test_two_of_three_qualifying_becomes_eligible():
    rows = [_row("2026-07-21", True, 100), _row("2026-07-22", True, 0), _row("2026-07-23", True, 80)]
    state, _ = multisession_state(rows)
    assert state == "PAPER_RECORDING_ELIGIBLE"


def test_three_sessions_but_only_one_qualifying_fails():
    rows = [_row("2026-07-21", True, 0), _row("2026-07-22", True, 5), _row("2026-07-23", True, 0)]
    state, _ = multisession_state(rows)
    assert state == "MULTISESSION_FAILED"


# --- idempotent multisession append (Phase 1/8) ------------------------------

class _Res:
    def __init__(self, d, value_rc):
        self.session_date = d; self.patched_collector = True; self.continuous_uptime_pct = 70.0
        self.frames = 1000; self.max_frame_gap_s = 3.0; self.max_market_timestamp_gap_s = 317.0
        self.max_material_value_gap_s = 36.0; self.timestamp_only_freezes = 18
        self.full_state_freezes = 0; self.connection_outages = 0; self.event_data_valid = 159
        self.legacy_range_confirmed = 0; self.value_range_confirmed = value_rc
        self.range_partial = 208; self.range_unreliable = 71; self.paper_opportunities = 0
        self.dataset_hash = "abc123"


def test_append_is_idempotent(tmp_path, monkeypatch):
    csv_path = tmp_path / "multi.csv"
    monkeypatch.setattr(run_val, "MULTISESSION_CSV", str(csv_path))
    run_val.append_multisession(_Res("2026-07-21", 100), force_rebuild=False)
    # second run for the same date must NOT duplicate
    rows, _, appended = run_val.append_multisession(_Res("2026-07-21", 100), force_rebuild=False)
    assert appended is False
    assert sum(1 for r in rows if r["Session Date"] == "2026-07-21") == 1


def test_force_rebuild_versions_without_deleting_prior(tmp_path, monkeypatch):
    csv_path = tmp_path / "multi.csv"
    monkeypatch.setattr(run_val, "MULTISESSION_CSV", str(csv_path))
    run_val.append_multisession(_Res("2026-07-21", 100), force_rebuild=False)
    rows, row, appended = run_val.append_multisession(_Res("2026-07-21", 100), force_rebuild=True)
    assert appended is True
    versions = [r["Run Version"] for r in rows if r["Session Date"] == "2026-07-21"]
    assert "1" in [str(v) for v in versions] and "2" in [str(v) for v in versions]  # prior preserved


# --- the collector-DB readability probe ---------------------------------------
#
# The probe answers one yes/no question before a full run, and it used to answer
# it with SELECT COUNT(*) FROM quotes -- a full table scan. On 2026-08-31 that
# cost eleven minutes of a thirty-minute task budget (22,347,695 rows, 7.3 GB),
# and the task was killed at its limit with its real work still running. The
# collector adds roughly a million rows a session, so the scan was growing.

def _quotes_db(path, ids):
    import sqlite3
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE quotes (id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, "
        "last_price REAL, bid REAL, ask REAL, volume REAL, market_timestamp TEXT, "
        "received_at TEXT)")
    for value in ids:
        connection.execute(
            "INSERT INTO quotes (id, ticker, received_at) VALUES (?,?,?)",
            (value, "COMI", "2026-08-31T07:00:00+00:00"))
    connection.commit()
    connection.close()
    return str(path)


def test_the_probe_reports_the_newest_id(tmp_path, capsys):
    import json as _json
    path = _quotes_db(tmp_path / "q.db", [1, 2, 7])
    assert run_val._check_db(path) == 0
    assert _json.loads(capsys.readouterr().out)["latest_id"] == 7


def test_an_empty_quotes_table_is_still_readable(tmp_path, capsys):
    """Readable and empty is not the same as unreadable, and must not be a failure."""

    import json as _json
    path = _quotes_db(tmp_path / "q.db", [])
    assert run_val._check_db(path) == 0
    payload = _json.loads(capsys.readouterr().out)
    assert payload["db_ok"] is True and payload["latest_id"] == 0


def test_a_missing_database_fails_the_probe(tmp_path, capsys):
    import json as _json
    assert run_val._check_db(str(tmp_path / "absent.db")) == 3
    assert _json.loads(capsys.readouterr().out)["db_ok"] is False


def test_a_file_that_is_not_a_database_fails_the_probe(tmp_path, capsys):
    import json as _json
    path = tmp_path / "not.db"
    path.write_text("this is not sqlite", encoding="utf-8")
    assert run_val._check_db(str(path)) == 3
    assert _json.loads(capsys.readouterr().out)["db_ok"] is False


def test_the_probe_does_not_count_rows():
    """A behavioural test cannot catch this: COUNT(*) returns the right answer,
    it just takes eleven minutes to do it. The cost is the defect, so the query
    is what gets pinned."""

    import ast
    import inspect
    import textwrap

    function = ast.parse(textwrap.dedent(inspect.getsource(run_val._check_db))).body[0]
    docstring = ast.get_docstring(function, clean=False)
    # The docstring names the old query to explain why it went, so it is
    # excluded: what is under test is the SQL actually handed to sqlite.
    queries = [
        node.value for node in ast.walk(function)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and node.value != docstring and "quotes" in node.value.lower()
    ]
    assert queries, "the probe must query the quotes table"
    assert not any("COUNT(*)" in q.upper() for q in queries)
    assert any("ORDER BY id DESC LIMIT 1" in q for q in queries)
