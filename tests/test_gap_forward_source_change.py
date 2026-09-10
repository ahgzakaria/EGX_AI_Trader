"""The gap forward test changed the source it measures, and must say so.

v1 read the Rubix feed: minute bars a collector captured, and a quoted bid and
ask. v2 reads MubasherTrade PRO's minute store, which has a real 10:00 open, an
auction close, the exchange's own turnover -- and no quotes at all.

Three of those numbers are better. One is gone. A forward test whose rule
silently changed under it is worth nothing, so what these hold is that the
change is declared and that the two eras can never be pooled by accident.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "record_gap_forward", ROOT / "scripts" / "research" / "record_gap_forward.py")
recorder = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(recorder)

V1_RULE = "top20pct-intraday-spread0.3-turnover20M-v1"


def test_the_rule_version_changed_with_the_source():
    """Pooling a spread-filtered rule with one that has no spread is invalid."""

    assert recorder.RULE_VERSION != V1_RULE
    assert "mubasher" in recorder.RULE_VERSION
    assert "spread" not in recorder.RULE_VERSION, (
        "v2 has no spread term; naming one would misdescribe every row it writes")


def test_the_rule_no_longer_reads_a_spread():
    """A filter reading None every time selects nothing at all."""

    import inspect

    source = inspect.getsource(recorder.record)
    selection = [line for line in source.splitlines()
                 if line.strip().startswith("selected = ")]
    assert len(selection) == 1, "the rule is one line, so a test can pin it"
    assert "spread" not in selection[0], (
        "a filter reading None every time selects nothing at all")
    assert "turnover" in selection[0] and "20_000_000" in selection[0]
    # The column is still written -- as NULL -- because a v2 row has to be
    # visibly spread-less rather than silently missing the field.
    assert 'shape["spread"]' in source


def test_the_recorded_spread_is_null_rather_than_a_substitute():
    """Roll's estimate is recorded beside the prediction, never as the spread.

    Against the quoted spread it ranks well and is biased low -- median
    0.26-0.28% against 0.39-0.46% -- so at the rule's own 0.3% cutoff it
    admits about twice as many symbols as tight. Writing it into
    spread_percent would make v1 and v2 rows look poolable and be wrong on the
    symbols the rule is least sure about.
    """

    import inspect

    source = inspect.getsource(recorder.session_shapes)
    assert '"spread": None' in source
    assert '"roll": rolls.get(ticker)' in source


def test_nothing_in_the_recorder_still_reads_the_retired_feed():
    text = (ROOT / "scripts" / "research" / "record_gap_forward.py").read_text(
        encoding="utf-8")
    for gone in ("rubix_live_market", "candles_1m", "MARKET_DB", "from quotes"):
        assert gone not in text, f"the recorder still reads {gone}"


def _store(tmp_path, rows):
    """A gap store holding exactly the predictions a test declares."""
    path = tmp_path / "gap_forward.db"
    conn = sqlite3.connect(path)
    conn.executescript(recorder.SCHEMA)
    conn.executemany(
        "INSERT INTO gap_predictions (session, ticker, recorded_at, rule_version, "
        "selected, rank_in_session, session_count, intraday_return, session_range, "
        "close_position, turnover, close_price, broker_round_trip) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(session, ticker, "2026-09-09T15:00:00+00:00", version, 1, 0, 100,
          1.0, 2.0, 0.9, 5e7, 10.0, 0.3638) for session, ticker, version in rows])
    conn.commit()
    conn.close()
    return path


def test_a_v1_prediction_is_never_graded_from_the_new_source(tmp_path, monkeypatch, capsys):
    """The two sources do not define an opening price the same way.

    On 2026-09-10 the feed's first captured bar and Mubasher's first traded
    minute agreed for 87.6% of symbols and to 0.0000% at the median, but 14 of
    217 differed by more than 0.5% and one by 5.4% -- the thin names, where the
    feed had a bar before the symbol had a trade. Those are exactly the
    symbols a selection rule is least certain about.
    """

    path = _store(tmp_path, [("2026-09-09", "COMI", V1_RULE),
                             ("2026-09-09", "SWDY", V1_RULE)])
    monkeypatch.setattr(recorder, "STORE", path)

    recorder.grade("2026-09-09")

    out = capsys.readouterr().out
    assert "earlier rule version" in out
    with sqlite3.connect(path) as connection:
        graded = connection.execute(
            "SELECT COUNT(*) FROM gap_predictions WHERE graded_at IS NOT NULL"
        ).fetchone()[0]
    assert graded == 0, "a v1 row must stay ungraded rather than change measure"


def test_a_v2_prediction_is_graded_from_the_minute_store(tmp_path, monkeypatch):
    import sector_flow.mubasher_local as local

    path = _store(tmp_path, [("2026-09-10", "COMI", recorder.RULE_VERSION)])
    monkeypatch.setattr(recorder, "STORE", path)
    monkeypatch.setattr(local, "available_sessions",
                        lambda *a, **k: ["2026-09-10", "2026-09-13"])
    monkeypatch.setattr(local, "session_opens", lambda *a, **k: {"COMI": 11.0})

    recorder.grade("2026-09-10")

    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT next_session, next_open, gap_percent FROM gap_predictions"
        ).fetchone()
    assert row[0] == "2026-09-13"
    assert row[1] == pytest.approx(11.0)
    assert row[2] == pytest.approx(10.0), "(11.0 - 10.0) / 10.0"


def test_an_existing_store_gains_the_new_column_rather_than_being_rebuilt(tmp_path, monkeypatch):
    """The 1,768 standing predictions are the experiment.

    CREATE TABLE IF NOT EXISTS leaves an existing table's columns alone, so a
    store written before v2 would reject every insert with "no column named
    roll_spread_estimate".
    """

    path = tmp_path / "gap_forward.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE gap_predictions (session TEXT NOT NULL, ticker TEXT NOT NULL, "
            "recorded_at TEXT NOT NULL, rule_version TEXT NOT NULL, selected INTEGER, "
            "rank_in_session INTEGER, session_count INTEGER, intraday_return REAL, "
            "session_range REAL, close_position REAL, turnover REAL, spread_percent REAL, "
            "close_price REAL, broker_round_trip REAL, graded_at TEXT, next_session TEXT, "
            "next_open REAL, gap_percent REAL, net_percent REAL, "
            "PRIMARY KEY (session, ticker))")
        connection.execute(
            "INSERT INTO gap_predictions (session, ticker, recorded_at, rule_version) "
            "VALUES ('2026-09-01', 'COMI', 'x', ?)", (V1_RULE,))
    monkeypatch.setattr(recorder, "STORE", path)

    conn = recorder.connect()
    held = {row[1] for row in conn.execute("PRAGMA table_info(gap_predictions)")}
    standing = conn.execute("SELECT COUNT(*) FROM gap_predictions").fetchone()[0]
    conn.close()

    assert "roll_spread_estimate" in held
    assert standing == 1, "the standing prediction survived the migration"


def test_the_bar_minimum_was_recalibrated_for_a_store_that_only_writes_trades():
    """The old feed wrote a bar a minute; this one writes a bar per traded minute.

    Keeping 160 would have dropped 90 of the 194 symbols the rule had always
    accepted, which is a different experiment wearing the same name.
    """

    assert recorder.DEFAULT_MIN_BARS == 60
    text = (ROOT / "scripts" / "research" / "record_gap_forward.py").read_text(
        encoding="utf-8")
    assert "2026-09-09" in text, "the calibration session has to be on record"


def test_completeness_is_judged_on_the_auction_not_the_clock():
    """A session whose minutes stop at 14:14 has a last trade, not a close."""

    import inspect

    source = inspect.getsource(recorder.session_is_complete)
    assert "close_confirmed" in source
    assert not hasattr(recorder, "LATEST_BAR_REQUIRED"), (
        "the clock gate belonged to the retired feed")
    assert "AUCTION_MINUTE" in source


def test_an_incomplete_session_refuses_rather_than_records():
    """A prediction is written once and never rewritten."""

    shapes = [{"close_confirmed": False, "last_minute": "14:14"}] * 200
    complete, why = recorder.session_is_complete("2026-09-10", shapes)
    assert complete is False
    assert "auction" in why

    enough = [{"close_confirmed": True, "last_minute": "14:29"}] * 200
    assert recorder.session_is_complete("2026-09-10", enough) == (True, "")


# --- the one-off that closes v1 out -----------------------------------------

def test_the_closeout_grades_v1_from_the_archive_that_made_it():
    text = (ROOT / "scripts" / "research" / "close_out_gap_forward_v1.py").read_text(
        encoding="utf-8")

    assert "rubix_live_market" in text, "v1 is settled against v1's own source"
    assert 'rule_version = ?' in text and "V1_RULE" in text
    assert "graded_at IS NULL" in text, "it can never regrade a standing outcome"
    for forbidden in ("INSERT INTO", "DELETE FROM", "DROP "):
        assert forbidden not in text, (
            f"the closeout must only fill outcomes, never {forbidden.strip()}")
