"""Scoring the portfolio's own recommendations: the choices made before the result.

`scripts/research/score_portfolio_recommendations.py` reads back the log
`holdings/store.py` writes and asks whether each exit rule was right. The
answers move money, so the parts that could be adjusted after a disappointing
number are pinned here: the horizon, the benchmark, the refusal to score a
window that has not finished, and the cost a rule has to beat.
"""

from __future__ import annotations

import pandas as pd
import pytest

from scripts.research import score_portfolio_recommendations as study


SESSIONS = pd.to_datetime(
    ["2026-08-31", "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-06"])


def series(*closes):
    return pd.Series(list(closes), index=SESSIONS[:len(closes)], dtype="float64")


# --- what was fixed before the result ---------------------------------------------

def test_the_primary_horizon_is_ten_sessions():
    """Half the exit plan's own 20-session time stop, chosen before running."""
    assert study.PRIMARY == 10
    assert study.HORIZONS == (1, 5, 10, 20)


def test_the_benchmark_is_the_cross_section_not_zero():
    source = study.__doc__ + study.peer_return.__doc__
    assert "median" in source
    # EGX30 is reported, never the thing a rule is judged on: its record ended
    # ten sessions behind the stock record.
    assert "EGX30" in study.__doc__


# --- the forward window -------------------------------------------------------------

def test_a_window_that_has_not_finished_is_not_scored():
    prices = series(10.0, 10.5, 11.0)
    assert study.forward(prices, "2026-08-31", 2) == (10.0, 11.0)
    assert study.forward(prices, "2026-08-31", 3) is None


def test_a_session_the_symbol_did_not_trade_is_refused_not_approximated():
    prices = series(10.0, 10.5, 11.0)
    assert study.forward(prices, "2026-09-04", 1) is None


def test_no_series_at_all_is_none():
    assert study.forward(None, "2026-08-31", 1) is None
    assert study.forward(pd.Series(dtype="float64"), "2026-08-31", 1) is None


# --- the cross-section --------------------------------------------------------------

def panel(rows, columns=40):
    """A panel of ``columns`` symbols, each row one session's closes."""
    return pd.DataFrame(
        {f"S{i}.CA": [row[min(i, len(row) - 1)] for row in rows] for i in range(columns)},
        index=SESSIONS[:len(rows)])


def test_the_peer_return_is_the_median_symbols_move():
    wide = panel([[100.0], [110.0]])          # every symbol +10%
    assert study.peer_return(wide, "2026-08-31", 1) == pytest.approx(10.0)


def test_a_cross_section_too_thin_to_be_one_is_refused():
    thin = panel([[100.0], [110.0]], columns=29)
    assert study.peer_return(thin, "2026-08-31", 1) is None


def test_the_peer_return_needs_the_window_to_have_finished():
    wide = panel([[100.0], [110.0]])
    assert study.peer_return(wide, "2026-08-31", 5) is None


# --- what a rule has to beat ---------------------------------------------------------

def scored_row(lift, cost=0.6, action="EXIT", rule="TIME_STOP"):
    return {"action": action, "rule": rule, f"lift_{study.PRIMARY}": lift,
            f"return_{study.PRIMARY}": lift, "cost_percent": cost}


def test_a_rule_beats_its_cost_only_when_what_it_avoided_is_bigger():
    # Avoided 5% against a 0.6% round trip.
    [row] = study.summarize([scored_row(-5.0)])
    assert row["beats_its_cost"] == "yes"
    assert row["underperformed_after"] == "1/1"

    # Avoided 0.3%, which the round trip eats.
    [row] = study.summarize([scored_row(-0.3)])
    assert row["beats_its_cost"] == "no"


def test_an_exit_before_a_rise_is_not_credited():
    [row] = study.summarize([scored_row(6.11)])
    assert row["beats_its_cost"] == "no"
    assert row["underperformed_after"] == "0/1"


def test_rules_are_never_pooled_with_each_other():
    rows = study.summarize([
        scored_row(-5.0, rule="LIQUIDITY_LEAVING"),
        scored_row(+6.0, rule="TIME_STOP"),
    ])
    assert {row["rule"] for row in rows} == {"LIQUIDITY_LEAVING", "TIME_STOP"}
    assert all(row["scored"] == 1 for row in rows)


def test_a_row_still_inside_its_window_never_reaches_the_summary():
    assert study.summarize([scored_row(None)]) == []
