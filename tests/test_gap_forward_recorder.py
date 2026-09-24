"""The forward recorder's integrity properties, pinned.

This exists to settle whether the gap edge is real, so the one thing it must
never do is let a prediction be written or altered after its outcome is known.
Everything else about it is convenience; this is the whole point.

Three properties carry that:

* recording twice writes nothing the second time, so a standing prediction can
  never be quietly replaced by a better one;
* grading only ever fills rows that already exist, and never creates one;
* grading twice does not touch an already-graded row.

The project has already been burned once by a results file nobody could
attribute — `backtest_results.csv` claiming +75.67% where the same engine gave
-1.78% — so the rule version and cost basis are stored per row and analyses
refuse to pool versions.
"""

import sqlite3

import pytest

import scripts.research.record_gap_forward as recorder


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "gap_forward.db"
    monkeypatch.setattr(recorder, "STORE", path)
    return path


def _row(conn, session="S1", ticker="AAA"):
    return conn.execute(
        "select selected, gap_percent, net_percent, graded_at, rule_version "
        "from gap_predictions where session=? and ticker=?",
        (session, ticker)).fetchone()


def _seed(store, **overrides):
    conn = recorder.connect()
    values = {
        "session": "S1", "ticker": "AAA", "recorded_at": "t0",
        "rule_version": recorder.RULE_VERSION, "selected": 1,
        "rank_in_session": 0, "session_count": 10, "intraday_return": 5.0,
        "session_range": 6.0, "close_position": 0.9, "turnover": 5e7,
        "spread_percent": 0.2, "close_price": 100.0,
        "broker_round_trip": recorder.BROKER_ROUND_TRIP,
    }
    values.update(overrides)
    conn.execute(
        "INSERT OR IGNORE INTO gap_predictions ({}) VALUES ({})".format(
            ",".join(values), ",".join("?" * len(values))),
        tuple(values.values()))
    conn.commit()
    return conn


def test_a_standing_prediction_is_never_rewritten(store):
    conn = _seed(store)
    # A second attempt with a different, more flattering prediction.
    conn.execute(
        "INSERT OR IGNORE INTO gap_predictions "
        "(session,ticker,recorded_at,rule_version,selected,rank_in_session,"
        " session_count,intraday_return,session_range,close_position,turnover,"
        " spread_percent,close_price,broker_round_trip) "
        "VALUES ('S1','AAA','t1','other',0,99,10,-9.0,1.0,0.1,1.0,9.9,50.0,0.5)")
    conn.commit()
    selected, _gap, _net, _graded, version = _row(conn)
    assert selected == 1
    assert version == recorder.RULE_VERSION
    conn.close()


def test_grading_fills_an_existing_row_and_creates_none(store):
    conn = _seed(store)
    before = conn.execute("select count(*) from gap_predictions").fetchone()[0]
    conn.execute(
        "UPDATE gap_predictions SET graded_at='t2', next_session='S2', "
        "next_open=101.0, gap_percent=1.0, net_percent=0.4 "
        "WHERE session='S1' AND ticker='AAA' AND graded_at IS NULL")
    conn.commit()
    after = conn.execute("select count(*) from gap_predictions").fetchone()[0]
    assert after == before
    assert _row(conn)[1] == pytest.approx(1.0)
    conn.close()


def test_an_already_graded_row_is_not_regraded(store):
    conn = _seed(store)
    for gap in (1.0, 99.0):
        conn.execute(
            "UPDATE gap_predictions SET graded_at='t', next_session='S2', "
            "next_open=101.0, gap_percent=?, net_percent=? "
            "WHERE session='S1' AND ticker='AAA' AND graded_at IS NULL",
            (gap, gap - 0.5))
    conn.commit()
    assert _row(conn)[1] == pytest.approx(1.0)   # the first grading stands
    conn.close()


def test_a_prediction_is_recorded_ungraded(store):
    conn = _seed(store)
    _selected, gap, net, graded, _v = _row(conn)
    assert graded is None and gap is None and net is None
    conn.close()


def test_every_row_carries_its_rule_and_cost_basis(store):
    conn = _seed(store)
    columns = {r[1] for r in conn.execute("pragma table_info(gap_predictions)")}
    # A prediction whose rule nobody recorded is the artifact that started this.
    assert "rule_version" in columns
    assert "broker_round_trip" in columns
    assert "spread_percent" in columns
    conn.close()


def test_the_primary_key_is_session_and_ticker(store):
    conn = recorder.connect()
    keys = [r[1] for r in conn.execute("pragma table_info(gap_predictions)")
            if r[5]]          # r[5] is the pk ordinal
    assert set(keys) == {"session", "ticker"}
    conn.close()


def test_two_rule_versions_are_not_pooled(store, capsys):
    conn = _seed(store)
    conn.execute(
        "INSERT OR IGNORE INTO gap_predictions "
        "(session,ticker,recorded_at,rule_version,selected,rank_in_session,"
        " session_count,intraday_return,session_range,close_position,turnover,"
        " spread_percent,close_price,broker_round_trip,graded_at,gap_percent,"
        " net_percent) "
        "VALUES ('S2','BBB','t','v2',1,0,10,5.0,6.0,0.9,5e7,0.2,100.0,0.36,"
        "'t',1.0,0.4)")
    conn.execute(
        "UPDATE gap_predictions SET graded_at='t', gap_percent=1.0, "
        "net_percent=0.4 WHERE ticker='AAA'")
    conn.commit()
    conn.close()
    recorder.report()
    assert "Not pooled" in capsys.readouterr().out

# --- the completeness guard -------------------------------------------------
#
# A prediction is written once and never rewritten, so an early or degraded run
# would be wrong permanently -- the same immutability that protects a standing
# prediction from being improved also prevents a bad one being corrected. The
# guard therefore has to refuse rather than record. 2026-08-20 is the real case:
# collection failed and the session arrived eight and a half hours late.
#
# What the guard asks changed with the source. It used to ask whether the feed
# had reached 11:25 UTC; it now asks whether the closing auction printed, which
# is the question that was always underneath it. On 2026-09-10 a session cut
# off at 14:14 would have carried a different close for 163 of 191 symbols.


def _shapes(count, *, confirmed, last_minute="14:29"):
    return [{"ticker": f"T{i}", "close_confirmed": confirmed,
             "last_minute": last_minute} for i in range(count)]


def test_too_few_symbols_is_refused():
    complete, why = recorder.session_is_complete(
        "2026-08-26", _shapes(recorder.MINIMUM_SYMBOLS - 1, confirmed=True))
    assert not complete
    assert "symbols" in why


def test_a_session_whose_auction_never_printed_is_refused():
    """The 2026-08-20 shape: rows exist, but not through to the cross."""

    complete, why = recorder.session_is_complete(
        "2026-08-26",
        _shapes(recorder.MINIMUM_SYMBOLS + 10, confirmed=False, last_minute="14:14"))
    assert not complete
    assert "auction" in why
    assert "14:14" in why, "the report has to say how far the session got"


def test_a_complete_session_passes():
    assert recorder.session_is_complete(
        "2026-08-26", _shapes(recorder.MINIMUM_SYMBOLS + 10, confirmed=True))[0]


def _recorder_shapes(count, *, confirmed, last_minute="14:29"):
    return [{"ticker": f"T{i}", "close": 10.0, "intraday": 1.0, "range": 2.0,
             "close_position": 0.5, "turnover": 5e7, "spread": None,
             "roll": 0.21, "close_confirmed": confirmed,
             "last_minute": last_minute} for i in range(count)]


def test_refusing_writes_nothing(monkeypatch, store):
    monkeypatch.setattr(
        recorder, "session_shapes",
        lambda *_a, **_k: _recorder_shapes(recorder.MINIMUM_SYMBOLS + 10,
                                           confirmed=False, last_minute="14:14"))
    with pytest.raises(SystemExit) as excinfo:
        recorder.record("2026-08-26", recorder.DEFAULT_MIN_BARS)
    assert "Nothing was recorded" in str(excinfo.value)
    conn = recorder.connect()
    assert conn.execute("select count(*) from gap_predictions").fetchone()[0] == 0
    conn.close()


def test_force_overrides_the_guard(monkeypatch, store):
    # For someone who has checked and disagrees. It exists so the guard can be
    # strict without becoming a dead end.
    monkeypatch.setattr(
        recorder, "session_shapes",
        lambda *_a, **_k: _recorder_shapes(1, confirmed=False, last_minute="14:14"))
    recorder.record("2026-08-26", recorder.DEFAULT_MIN_BARS, force=True)
    conn = recorder.connect()
    assert conn.execute("select count(*) from gap_predictions").fetchone()[0] == 1
    conn.close()


def test_the_roll_estimate_is_stored_but_never_selects(monkeypatch, store):
    """It rides along for a later analysis; the rule must not read it."""

    monkeypatch.setattr(
        recorder, "session_shapes",
        lambda *_a, **_k: _recorder_shapes(200, confirmed=True))
    recorder.record("2026-08-26", recorder.DEFAULT_MIN_BARS)

    conn = recorder.connect()
    spread, roll = conn.execute(
        "select spread_percent, roll_spread_estimate from gap_predictions "
        "where ticker='T0'").fetchone()
    conn.close()
    assert spread is None, "this source has no quotes and must not pretend to"
    assert roll == pytest.approx(0.21)


# --- a day the daily run was not clicked -------------------------------------
#
# `daily` recorded only the newest session and graded the one before it, so
# 2026-09-23 -- a session nobody ran the daily update on -- has no predictions,
# although the minute store held its minutes for weeks afterwards.

def test_a_skipped_session_is_recorded_on_the_next_run(store):
    for session in ("2026-09-21", "2026-09-22"):
        _seed(store, session=session).close()

    held = ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"]
    assert recorder.sessions_to_record(held) == ["2026-09-23", "2026-09-24"]


def test_history_before_the_record_began_is_not_backfilled(store):
    """A gap is a missed day. Anything before the first recorded session is
    history the rule never saw live -- writing it now is a backtest."""
    _seed(store, session="2026-09-22").close()

    held = ["2026-09-15", "2026-09-16", "2026-09-22", "2026-09-23"]
    assert recorder.sessions_to_record(held) == ["2026-09-23"]


def test_an_earlier_rule_versions_sessions_do_not_start_the_record(store):
    """The v1 rows date from the Rubix feed. The current rule's record starts
    where the current rule started, or catch-up would write it into v1's era."""
    _seed(store, session="2026-08-26", rule_version="v1-rubix").close()
    _seed(store, session="2026-09-22").close()

    held = ["2026-08-26", "2026-08-27", "2026-09-22", "2026-09-23"]
    assert recorder.sessions_to_record(held) == ["2026-09-23"]


def test_an_empty_record_starts_with_the_newest_session_only(store):
    assert recorder.sessions_to_record(["2026-09-22", "2026-09-23"]) == ["2026-09-23"]


def test_daily_catches_up_and_grades_every_open_session(monkeypatch, store):
    _seed(store, session="2026-09-21").close()
    recorded, graded = [], []
    monkeypatch.setattr("sector_flow.mubasher_local.available_sessions",
                        lambda *a, **k: ["2026-09-21", "2026-09-22", "2026-09-23"])
    monkeypatch.setattr(recorder, "record",
                        lambda session, *a, **k: recorded.append(session))
    monkeypatch.setattr(recorder, "grade", lambda session: graded.append(session))

    recorder.daily(recorder.DEFAULT_MIN_BARS)

    assert recorded == ["2026-09-22", "2026-09-23"]
    # Only 2026-09-21 had a row to grade in this fixture; record() was stubbed.
    assert graded == ["2026-09-21"]


def test_one_incomplete_session_does_not_stop_the_catch_up(monkeypatch, store, capsys):
    _seed(store, session="2026-09-21").close()
    recorded = []

    def record(session, *a, **k):
        if session == "2026-09-22":
            raise SystemExit("session 2026-09-22 looks incomplete")
        recorded.append(session)

    monkeypatch.setattr("sector_flow.mubasher_local.available_sessions",
                        lambda *a, **k: ["2026-09-21", "2026-09-22", "2026-09-23"])
    monkeypatch.setattr(recorder, "record", record)
    monkeypatch.setattr(recorder, "grade", lambda session: None)

    recorder.daily(recorder.DEFAULT_MIN_BARS)

    assert recorded == ["2026-09-23"]
    assert "2026-09-22: not recorded" in capsys.readouterr().out


def test_a_session_already_graded_is_not_retried_for_its_stragglers(store):
    """A symbol that did not trade the next session has no open to settle
    against, and never will: grading uses that one session. Retrying it every
    run only reprints the same zero."""
    conn = _seed(store, session="2026-09-21", ticker="AAA")
    conn.execute("UPDATE gap_predictions SET graded_at='t1' WHERE ticker='AAA'")
    conn.commit()
    conn.close()
    _seed(store, session="2026-09-21", ticker="BBB").close()     # the straggler

    assert recorder.needs_grading("2026-09-21") is False


def test_a_session_never_graded_still_is(store):
    _seed(store, session="2026-09-24").close()
    assert recorder.needs_grading("2026-09-24") is True
    assert recorder.needs_grading("2026-09-25") is False, "nothing recorded"
