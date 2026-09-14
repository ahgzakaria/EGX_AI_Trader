"""The forward evaluation of the Daily Dashboard's BUY decisions.

These pin the two pure halves of `scripts/evaluate_daily_dashboard_buys`: the
fold that stops a re-issued BUY being scored as a second trade, and the account
simulation that stops unlimited capital being reported as what the account
made. The replay itself runs the frozen entry and exit managers and is checked
against the paper portfolio's own record by the script, not re-implemented here.
"""

from __future__ import annotations

import pytest

from scripts.evaluate_daily_dashboard_buys import fold_repeats, simulate_account

ACCOUNT = dict(capital=100_000.0, risk_percent=2.0, max_open_positions=10,
               max_heat_percent=10.0)


def trade(ticker, entry, exit_, pnl=100.0, *, outcome="CLOSED", value=15_000.0,
          risk=2_000.0, signal=None, ret=1.0):
    return {"ticker": ticker, "signal_date": signal or entry, "entry_date": entry,
            "exit_date": exit_, "outcome": outcome, "net_profit_egp": pnl,
            "net_return_pct": ret, "shares": 100, "position_value": value,
            "risk_amount": risk, "repeat_of_open": False}


# --- repeats --------------------------------------------------------------------

def test_a_buy_reissued_while_the_first_trade_is_open_is_the_same_trade():
    rows = fold_repeats([trade("EBSC", "2026-07-12", "2026-07-14"),
                         trade("EBSC", "2026-07-13", "2026-07-15")])
    assert [r["repeat_of_open"] for r in rows] == [False, True]


def test_a_buy_after_the_first_trade_closed_is_a_new_trade():
    rows = fold_repeats([trade("EBSC", "2026-07-12", "2026-07-14"),
                         trade("EBSC", "2026-07-28", "2026-07-30")])
    assert not any(r["repeat_of_open"] for r in rows)


def test_an_open_trade_keeps_every_later_buy_a_repeat():
    rows = fold_repeats([trade("MHOT", "2026-09-14", "2026-09-14", outcome="OPEN"),
                         trade("MHOT", "2026-09-20", "2026-09-25")])
    assert rows[1]["repeat_of_open"]


def test_a_decision_that_never_filled_is_never_a_repeat():
    rows = fold_repeats([{"ticker": "CAED", "outcome": "NO_FILL"}])
    assert rows[0]["repeat_of_open"] is False


# --- the account ----------------------------------------------------------------

def test_the_position_limit_is_the_smaller_of_the_count_and_the_heat():
    """10 positions allowed, but 10% heat at 2% risk leaves room for 5."""
    _, summary = simulate_account([], **ACCOUNT)
    assert summary["effective_max_positions"] == 5


def test_a_sixth_concurrent_trade_is_refused():
    rows = [trade(f"S{i}", "2026-08-02", "2026-08-20", value=10_000) for i in range(6)]
    ledger, summary = simulate_account(rows, **ACCOUNT)
    assert summary["admitted"] == 5
    assert summary["refused"] == {"MaxPositions": 1}


def test_a_position_closing_on_a_date_frees_its_slot_before_that_dates_entries():
    rows = [trade(f"S{i}", "2026-08-02", "2026-08-10", value=10_000) for i in range(5)]
    rows.append(trade("LATE", "2026-08-10", "2026-08-20", value=10_000))
    _, summary = simulate_account(rows, **ACCOUNT)
    assert summary["admitted"] == 6 and not summary["refused"]


def test_heat_refuses_a_trade_the_count_would_allow():
    rows = [trade("A", "2026-08-02", "2026-08-20", risk=6_000, value=10_000),
            trade("B", "2026-08-02", "2026-08-20", risk=6_000, value=10_000)]
    _, summary = simulate_account(rows, **ACCOUNT)
    assert summary["refused"] == {"Heat": 1}


def test_cash_refuses_a_trade_the_heat_would_allow():
    rows = [trade("A", "2026-08-02", "2026-08-20", value=60_000),
            trade("B", "2026-08-02", "2026-08-20", value=60_000)]
    _, summary = simulate_account(rows, **ACCOUNT)
    assert summary["refused"] == {"Capital": 1}


def test_realised_profit_is_cash_again():
    rows = [trade("A", "2026-08-02", "2026-08-05", pnl=30_000, value=90_000),
            trade("B", "2026-08-06", "2026-08-20", value=120_000)]
    _, summary = simulate_account(rows, **ACCOUNT)
    assert summary["admitted"] == 2


def test_same_day_candidates_are_taken_in_ranking_order():
    rows = [trade(f"S{i}", "2026-08-02", "2026-08-20", value=10_000) for i in range(6)]
    ranking = {("S5", "2026-08-02"): 1}
    ledger, _ = simulate_account(rows, **ACCOUNT, ranking=ranking)
    assert ledger[0]["ticker"] == "S5" and ledger[0]["admitted"]
    assert not ledger[-1]["admitted"]


def test_an_open_trade_is_marked_and_never_realised():
    rows = [trade("A", "2026-08-02", "2026-08-05", pnl=500, ret=5.0),
            trade("B", "2026-08-06", "2026-09-14", pnl=-300, outcome="OPEN", ret=-3.0)]
    _, summary = simulate_account(rows, **ACCOUNT)
    assert summary["closed"] == 1 and summary["wins"] == 1
    assert summary["closed_pnl_egp"] == 500
    assert summary["open_mark_egp"] == -300
    assert summary["equity_egp"] == pytest.approx(100_200)


def test_a_repeat_never_reaches_the_account():
    repeat = trade("A", "2026-08-03", "2026-08-05")
    repeat["repeat_of_open"] = True
    _, summary = simulate_account([trade("A", "2026-08-02", "2026-08-05"), repeat],
                                  **ACCOUNT)
    assert summary["admitted"] == 1


def test_the_result_is_reported_without_its_single_best_trade():
    """One trade carrying a whole period is a fact the reader needs."""
    rows = [trade("KWIN", "2026-08-19", "2026-08-30", pnl=9_500),
            trade("A", "2026-08-02", "2026-08-05", pnl=-300),
            trade("B", "2026-08-06", "2026-08-09", pnl=200)]
    _, summary = simulate_account(rows, **ACCOUNT)
    assert summary["best_trade"][0] == "KWIN"
    assert summary["closed_pnl_without_best_egp"] == pytest.approx(-100)
