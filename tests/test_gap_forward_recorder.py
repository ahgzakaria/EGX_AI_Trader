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


def _market_db(tmp_path, monkeypatch, last_bar):
    path = tmp_path / "market.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE candles_1m (ticker TEXT, minute TEXT, open REAL, "
        "high REAL, low REAL, close REAL, volume REAL)")
    conn.execute(
        "INSERT INTO candles_1m VALUES ('AAA', ?, 1, 1, 1, 1, 1)",
        (f"2026-08-26T{last_bar}:00+00:00",))
    conn.commit()
    conn.close()
    monkeypatch.setattr(recorder, "MARKET_DB", path)
    return path


def test_too_few_symbols_is_refused(tmp_path, monkeypatch):
    _market_db(tmp_path, monkeypatch, "11:30")
    shapes = [{"ticker": f"T{i}"} for i in range(recorder.MINIMUM_SYMBOLS - 1)]
    complete, why = recorder.session_is_complete("2026-08-26", shapes)
    assert not complete
    assert "symbols" in why


def test_a_session_ending_early_is_refused(tmp_path, monkeypatch):
    # The 2026-08-20 shape: rows exist, but not through to the close.
    _market_db(tmp_path, monkeypatch, "08:00")
    shapes = [{"ticker": f"T{i}"} for i in range(recorder.MINIMUM_SYMBOLS + 10)]
    complete, why = recorder.session_is_complete("2026-08-26", shapes)
    assert not complete
    assert recorder.LATEST_BAR_REQUIRED in why


def test_a_complete_session_passes(tmp_path, monkeypatch):
    _market_db(tmp_path, monkeypatch, "11:30")
    shapes = [{"ticker": f"T{i}"} for i in range(recorder.MINIMUM_SYMBOLS + 10)]
    assert recorder.session_is_complete("2026-08-26", shapes)[0]


def test_refusing_writes_nothing(tmp_path, monkeypatch, store):
    _market_db(tmp_path, monkeypatch, "08:00")
    monkeypatch.setattr(
        recorder, "session_shapes",
        lambda *_a, **_k: [{"ticker": f"T{i}", "close": 10.0, "intraday": 1.0,
                            "range": 2.0, "close_position": 0.5,
                            "turnover": 5e7, "spread": 0.2}
                           for i in range(recorder.MINIMUM_SYMBOLS + 10)])
    with pytest.raises(SystemExit) as excinfo:
        recorder.record("2026-08-26", 160)
    assert "Nothing was recorded" in str(excinfo.value)
    conn = recorder.connect()
    assert conn.execute("select count(*) from gap_predictions").fetchone()[0] == 0
    conn.close()


def test_force_overrides_the_guard(tmp_path, monkeypatch, store):
    # For someone who has checked and disagrees. It exists so the guard can be
    # strict without becoming a dead end.
    _market_db(tmp_path, monkeypatch, "08:00")
    monkeypatch.setattr(
        recorder, "session_shapes",
        lambda *_a, **_k: [{"ticker": "AAA", "close": 10.0, "intraday": 1.0,
                            "range": 2.0, "close_position": 0.5,
                            "turnover": 5e7, "spread": 0.2}])
    recorder.record("2026-08-26", 160, force=True)
    conn = recorder.connect()
    assert conn.execute("select count(*) from gap_predictions").fetchone()[0] == 1
    conn.close()
