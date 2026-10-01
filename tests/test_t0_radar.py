"""The T+0 radar and its forward record.

These pin what decides the list: each gate on its own, that the ranking is the
five-session mean range and never reads direction, that the forecast is that mean
times the measured ratios, that an empty same-session list filters nothing while
a populated one filters everything absent from it, and that the record of what
the radar forecast can be graded once, never rewritten, and never pooled across
rule versions.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd
import pytest

from core.effective_cost import SymbolCost
from t0_radar import forward, radar

DAYS = pd.bdate_range("2026-05-03", periods=90)


def history(range_pct=4.0, close=100.0, days=DAYS):
    """A flat stock whose every session spans ``range_pct`` of the prior close."""

    half = close * range_pct / 200
    return pd.DataFrame({"Open": close, "High": close + half, "Low": close - half,
                         "Close": close, "Volume": 1_000_000.0}, index=days)


def turnover(value=50e6, days=DAYS):
    return pd.Series(value, index=days)


def assess(frame=None, flow=None, sessions=DAYS, cost=0.6, t0="UNKNOWN", enforced=False):
    return radar.assess("TEST", history() if frame is None else frame,
                        turnover() if flow is None else flow, sessions, cost, t0, enforced)


def build(histories, costs, flows, eligibility="UNKNOWN", t0=None):
    return radar.build(histories, turnover_of=flows.get, costs=costs,
                       eligibility_of=lambda s: eligibility,
                       t0_provenance=t0 or {"available": False})


# --- the gates ---------------------------------------------------------------


def test_a_name_that_clears_every_gate_is_measured():
    row = assess()
    assert row["gate"] is None
    assert row["recent_range_pct"] == pytest.approx(4.0)
    assert row["score"] == row["recent_range_pct"]
    assert row["room_multiple"] == pytest.approx(4.0 / 0.6)


def test_a_listed_name_gets_its_forecast_and_reasons():
    finished = radar.finish(pd.DataFrame([assess()])).iloc[0]
    assert finished["rank"] == 1
    assert finished["forecast_pct"] == pytest.approx(4.0 * radar.FORECAST_TOP[1])
    assert any("المدى المتوقع" in r for r in finished["reasons"])
    # A count reads as a count, not as 20.0 sessions.
    assert any("في 20 من آخر 20 جلسة" in r for r in finished["reasons"])
    # Unknown is in its column; the reader checks it, so it is not a caution.
    assert finished["t0_status"] == "UNKNOWN"
    assert not any("T+0" in c for c in finished["cautions"])


def test_liquidity_counts_a_session_without_a_trade_as_zero():
    # 50M on five of the last twenty sessions is 12.5M a session, not 50M.
    sparse = turnover(0.0)
    sparse.iloc[-5:] = 50e6
    assert assess(flow=sparse)["gate"] == "Liquidity"


def test_an_unmeasured_cost_is_refused_rather_than_assumed():
    assert assess(cost=None)["gate"] == "CostUnmeasured"


def test_a_range_under_three_round_trips_is_refused():
    assert assess(frame=history(range_pct=1.5), cost=0.6)["gate"] == "NoRoom"


def test_a_move_past_the_daily_limit_is_an_unadjusted_action():
    frame = history()
    frame.iloc[-3:, frame.columns.get_indexer(["Open", "High", "Low", "Close"])] *= 0.66
    row = assess(frame=frame)
    assert row["gate"] == "CorporateAction"
    assert "-34.0%" in row["corporate_action"]


def test_a_name_that_did_not_trade_last_session_is_refused():
    sessions = DAYS.append(pd.DatetimeIndex([DAYS[-1] + pd.offsets.BDay()]))
    assert assess(sessions=sessions)["gate"] == "NotTradedLastSession"


def test_a_one_price_session_is_refused():
    frame = history()
    frame.iloc[-1, frame.columns.get_indexer(["High", "Low"])] = 100.0
    assert assess(frame=frame)["gate"] == "OnePriceSession"


def test_a_short_history_is_refused():
    assert assess(frame=history().iloc[-30:])["gate"] == "InsufficientHistory"


def test_an_empty_t0_list_filters_nothing_and_a_populated_one_filters_the_rest():
    assert assess(t0="UNKNOWN", enforced=False)["gate"] is None
    assert assess(t0="UNKNOWN", enforced=True)["gate"] == "NotT0"
    assert assess(t0="NOT_ELIGIBLE", enforced=True)["gate"] == "NotT0"
    eligible = radar.finish(pd.DataFrame([assess(t0="ELIGIBLE", enforced=True)])).iloc[0]
    assert any("T+0" in r for r in eligible["reasons"])


# --- the ranking and the forecast --------------------------------------------


def test_a_wider_last_five_sessions_ranks_first():
    calm, busy = history(), history()
    rows = busy.index[-5:]
    busy.loc[rows, "High"], busy.loc[rows, "Low"] = 103.5, 96.5     # 7% for a week
    finished = radar.finish(pd.DataFrame([
        dict(assess(frame=calm), symbol="CALM"), dict(assess(frame=busy), symbol="BUSY")]))
    assert list(finished["symbol"]) == ["BUSY", "CALM"]
    assert finished.loc[0, "recent_range_pct"] == pytest.approx(7.0)


def test_the_ranking_never_reads_direction():
    up, down = history(), history()
    up.iloc[-1, up.columns.get_indexer(["Close"])] = 101.9     # near the high
    down.iloc[-1, down.columns.get_indexer(["Close"])] = 98.1  # near the low
    a, b = assess(frame=up), assess(frame=down)
    assert a["close_position"] > 0.9 and b["close_position"] < 0.1
    assert a["score"] == b["score"]


def test_the_forecast_is_the_recent_range_times_the_measured_ratios():
    assert radar.forecast(5.0, rank=1) == pytest.approx(tuple(5.0 * r for r in radar.FORECAST_TOP))
    assert radar.forecast(5.0, rank=radar.DEFAULT_TOP + 1) == pytest.approx(
        tuple(5.0 * r for r in radar.FORECAST_REST))
    # A name that ranks first had a wide week and narrows: its ratios are lower.
    assert all(t < r for t, r in zip(radar.FORECAST_TOP, radar.FORECAST_REST))


# --- the universe ------------------------------------------------------------


def _universe(count=55):
    histories = {f"S{i:02d}": history(range_pct=3.0 + i * 0.1) for i in range(count)}
    histories["THIN"] = history()
    costs = {name: SymbolCost(name, 0.15, 30, "2026-09-08") for name in histories}
    flows = {name: turnover(1e6 if name == "THIN" else 50e6) for name in histories}
    return histories, costs, flows


def test_the_funnel_partitions_the_universe_and_ranks_run_from_one():
    histories, costs, flows = _universe()
    result = build(histories, costs, flows)
    assert result.session_date == DAYS[-1].date().isoformat()
    assert sum(result.funnel.values()) + len(result.candidates) == len(histories)
    assert result.funnel["Liquidity"] == 1
    assert list(result.candidates["rank"]) == list(range(1, len(result.candidates) + 1))
    assert result.candidates["score"].is_monotonic_decreasing
    assert result.provenance["cost_measured_through"] == "2026-09-08"
    assert result.provenance["rule_version"] == radar.RULE_VERSION


def test_the_saved_list_reads_back_with_its_reasons(tmp_path):
    histories, costs, flows = _universe()
    result = build(histories, costs, flows)
    path = radar.write_csv(result, tmp_path)
    run, session = radar.dates_from_name(path)
    assert session == result.session_date and run
    saved = pd.read_csv(path, encoding="utf-8-sig")
    assert radar.split_sentences(saved.loc[0, "reasons"]) == result.candidates.loc[0, "reasons"]
    assert saved.loc[0, "rule_version"] == radar.RULE_VERSION
    assert radar.dates_from_name(tmp_path / "renamed.csv") == (None, None)


# --- the forward record ------------------------------------------------------


def _recorded(tmp_path, count=52):
    histories, costs, flows = _universe(count)
    result = build(histories, costs, flows)
    store = forward.RadarStore(tmp_path / "t0.db")
    store.record(result)
    return store, result


NEXT = (DAYS[-1] + pd.offsets.BDay()).date().isoformat()
BAR = {"High": 130.0, "Low": 90.0, "Close": 103.0}


def test_a_session_is_recorded_once_per_rule(tmp_path):
    store, result = _recorded(tmp_path)
    again = store.record(result)
    assert again["already_recorded"] and again["written"] == 0
    with sqlite3.connect(store.path) as c:
        assert c.execute("SELECT COUNT(*) FROM picks").fetchone()[0] == len(result.candidates)


def test_a_pick_is_graded_against_the_next_session_from_its_true_open(tmp_path):
    store, result = _recorded(tmp_path)
    graded = store.grade([result.session_date, NEXT], lambda s, d: BAR,
                         lambda d: {s: 100.0 for s in result.candidates["symbol"]})
    assert graded == len(result.candidates)
    with sqlite3.connect(store.path) as c:
        row = c.execute("SELECT next_session, next_range_pct, next_best_from_open_pct, "
                        "next_open_to_close_pct, open_source FROM picks LIMIT 1").fetchone()
    assert row[0] == NEXT
    assert row[1] == pytest.approx(40.0)         # (130 - 90) / the radar close of 100
    assert row[2] == pytest.approx(30.0)
    assert row[3] == pytest.approx(3.0)
    assert row[4] == "mubasher_minute"
    report = store.report(top=10)
    assert report["sessions_graded"] == 1 and report["top"]["picks"] == 10
    # A 40% session against forecasts of a few percent lands above every band.
    assert report["top"]["inside_band_share"] == 0.0
    assert report["top"]["actual_over_forecast_median"] > 1


def test_rule_versions_are_recorded_and_reported_apart(tmp_path):
    store, result = _recorded(tmp_path)
    older = radar.RadarResult(result.session_date, result.candidates, result.funnel,
                              dict(result.provenance, rule_version="t0-radar-v0"))
    assert store.record(older)["written"] == len(result.candidates)
    store.grade([NEXT], lambda s, d: BAR, lambda d: {})
    assert store.report()["top"]["picks"] == 10
    assert store.report(rule_version="t0-radar-v0")["top"]["picks"] == 10
    assert store.report()["sessions_recorded"] == 1


def test_without_a_true_open_the_open_legs_stay_empty():
    outcome = forward.grade_one(100.0, {"High": 104.0, "Low": 99.0, "Close": 101.0}, None)
    assert outcome["next_range_pct"] == pytest.approx(5.0)
    assert outcome["next_open"] is None and outcome["next_best_from_open_pct"] is None
    assert forward.grade_one(100.0, None, 100.0)["next_traded"] == 0


def test_nothing_recorded_can_be_rewritten_or_removed(tmp_path):
    store, _ = _recorded(tmp_path)
    with sqlite3.connect(store.path) as c:
        with pytest.raises(sqlite3.DatabaseError):
            c.execute("UPDATE picks SET forecast_pct = 0 WHERE rank = 1")
        with pytest.raises(sqlite3.DatabaseError):
            c.execute("DELETE FROM picks")
        with pytest.raises(sqlite3.DatabaseError):
            c.execute("UPDATE runs SET candidates = 0")


def test_a_graded_pick_is_not_graded_again(tmp_path):
    store, _ = _recorded(tmp_path)
    store.grade([NEXT], lambda s, d: BAR, lambda d: {})
    assert store.grade([NEXT], lambda s, d: {"High": 1.0, "Low": 1.0, "Close": 1.0},
                       lambda d: {}) == 0
    assert store.pending_sessions() == []
    assert np.isfinite(store.report()["top"]["range_median"])
