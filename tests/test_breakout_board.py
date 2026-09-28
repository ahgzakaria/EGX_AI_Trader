"""The Daily Dashboard leads with breakouts, and the old rule is a reference.

On the live record the dashboard's own BUYs did worse than the stocks it said to
avoid (DASHBOARD_CALLS_VS_OUTCOMES.md), while a plain first close above the
twenty-session high was the best group. These pin the two lists the page now
leads with, and that they come before the old rule rather than beside it.
"""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import pytest

from services import breakout_board as board


def history(closes, volume=1_000_000.0, spread=0.005):
    closes = np.asarray(closes, dtype=float)
    index = pd.bdate_range("2026-06-01", periods=len(closes))
    return pd.DataFrame({"Open": closes, "High": closes * (1 + spread),
                         "Low": closes * (1 - spread), "Close": closes,
                         "Volume": np.full(len(closes), float(volume))}, index=index)


def breaking(last=11.0, volume=1_000_000.0, last_volume=None):
    frame = history(np.r_[np.full(30, 10.0), [last]], volume=volume)
    if last_volume is not None:
        frame.iloc[-1, frame.columns.get_loc("Volume")] = last_volume
    return frame


# --- the watch list -----------------------------------------------------------

def test_a_first_close_above_the_prior_high_is_listed():
    hit = board.fresh_breakout(breaking(), minimum_turnover=1.0)
    assert hit is not None
    assert hit.prior_high == pytest.approx(10.05)
    assert hit.above_percent == pytest.approx((11.0 / 10.05 - 1) * 100)


def test_the_second_close_above_it_is_not_a_first():
    frame = history(np.r_[np.full(30, 10.0), [11.0, 11.2]])
    assert board.fresh_breakout(frame, minimum_turnover=1.0) is None


def test_a_close_under_the_level_is_not_a_breakout():
    assert board.fresh_breakout(breaking(last=10.0), minimum_turnover=1.0) is None


def test_a_name_too_thin_to_trade_is_left_out():
    """Swing Breakout's measured floor: 5 million EGP of median daily turnover."""
    thin = breaking(volume=1_000.0)                         # ~10,000 EGP a day
    assert board.fresh_breakout(thin, minimum_turnover=board.liquidity_floor()) is None
    assert board.liquidity_floor() == 5_000_000.0


def test_the_list_is_ordered_by_volume_and_skips_the_buy_signals():
    histories = {
        "AAA.CA": breaking(last_volume=2_000_000.0),
        "BBB.CA": breaking(last_volume=6_000_000.0),
        "CCC.CA": breaking(last_volume=4_000_000.0),
        "DDD.CA": breaking(last=10.0),
    }
    listed = board.fresh_breakouts(histories, minimum_turnover=1.0, exclude=["CCC.CA"])
    assert [b.symbol for b in listed] == ["BBB.CA", "AAA.CA"]
    assert listed[0].volume_ratio > listed[1].volume_ratio


def test_no_bar_after_the_last_is_read():
    """The list is built at the close: cutting the frame there changes nothing."""
    frame = breaking()
    longer = pd.concat([frame, history([12.0, 12.5]).set_axis(
        pd.bdate_range(frame.index[-1] + pd.offsets.BDay(), periods=2))])
    cut = longer.loc[:frame.index[-1]]
    assert board.fresh_breakout(cut, 1.0) == board.fresh_breakout(frame, 1.0)


# --- the buy signals come from the rule itself --------------------------------------

def test_the_buy_signals_are_the_confirmed_breakout_rules_own_scan(monkeypatch):
    seen = {}

    def scan(histories=None, cfg=None, **_):
        seen["histories"], seen["cfg"] = histories, cfg
        return "result"

    monkeypatch.setattr("strategy_momentum_breakout.scan.scan", scan)
    assert board.confirmed_breakouts({"A.CA": "frame"}, cfg="cfg") == "result"
    assert seen == {"histories": {"A.CA": "frame"}, "cfg": "cfg"}


def test_the_histories_are_the_ones_the_scan_already_loaded():
    frame = history([10.0] * 5)
    rows = [{"Ticker": "A.CA", "Data": frame}, {"Ticker": "B.CA", "Data": None},
            {"Ticker": "C.CA"}]
    assert list(board.histories_from_results(rows)) == ["A.CA"]


# --- the page ------------------------------------------------------------------------

def test_the_page_leads_with_the_breakouts_and_the_old_rule_follows():
    from dashboard import home

    source = inspect.getsource(home.show_dashboard)
    assert source.index("_render_breakout_board(") < source.index("_render_opportunities(")
    assert "القاعدة القديمة · مرجع للمقارنة" in source
    assert source.index("_breakout_board(results)") < source.index("context_strip(")


def test_the_old_rule_is_shown_with_its_live_record():
    from dashboard import home

    assert "−2.06%" in home.LEGACY_RULE_NOTE and "44%" in home.LEGACY_RULE_NOTE
    assert "+0.96%" in home.WATCH_LIST_NOTE and "ليست إشارة شراء" in home.WATCH_LIST_NOTE


def test_the_board_is_computed_once_per_scan(monkeypatch):
    from dashboard import home

    calls = []
    monkeypatch.setattr(home.st, "session_state", {})
    monkeypatch.setattr("services.breakout_board.confirmed_breakouts",
                        lambda h, cfg=None: calls.append(1) or type(
                            "R", (), {"signals": [], "count": 0, "considered": 0})())
    rows = [{"RunID": "RUN_1", "Ticker": "A.CA", "Data": history([10.0] * 25)}]
    first = home._breakout_board(rows)
    second = home._breakout_board(rows)
    assert first is second and len(calls) == 1
