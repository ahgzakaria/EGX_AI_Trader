"""The wiring: prices chosen, plans persisted, recommendations logged once.

Every provider is injected, so these tests exercise the decisions this layer
makes rather than the availability of a market feed.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from core.level_status import BASIS_COMPLETED_CLOSE, BASIS_LIVE
from holdings.assistant import (
    NO_PRICE,
    build_portfolio_view,
    log_recommendations,
    select_price,
)
from holdings.book import FeeModel
from holdings.plan import ExitPolicy
from holdings.store import HoldingsStore


FREE = FeeModel(rates_loaded=True)
POLICY = ExitPolicy(trailing_enabled=False)


def frame(last_close=11.0, *, low=10.0, high=12.0, atr=0.5, volume=100_000):
    """Bars whose levels reproduce the user's own example.

    A 20-bar low of 10.00 and a 20-bar high of 12.00 with ATR 0.50 give a stop
    at 9.85, a partial target at 12.00 and a final target at 13.00 -- "bought at
    10, take some at 12, out at 13".
    """

    closes = [11.0] * 24 + [last_close]
    index = pd.date_range("2026-07-28", periods=len(closes), freq="D")
    return pd.DataFrame(
        {
            "Close": closes,
            "High": [high] * 24 + [last_close + 0.1],
            "Low": [low] * 24 + [last_close - 0.1],
            "Volume": [100_000] * 24 + [volume],
            "ATR": [atr] * len(closes),
            "EMA20": [9.8] * len(closes),
        },
        index=index,
    )


def fresh_quote(last=11.0):
    return {"last": last, "freshness": "FRESH", "session_phase": "OPEN",
            "spread_percent": 0.3, "quote_timestamp": "2026-08-31T09:00:00+00:00"}


@pytest.fixture
def store(tmp_path):
    store = HoldingsStore(tmp_path / "portfolio.db")
    store.record_trade("ABUK", "2026-08-03", "BUY", 1000, 10.0, fee_model=FREE)
    return store


def view_for(store, **overrides):
    options = {
        "fee_model": FREE, "policy": POLICY,
        "history_loader": lambda symbol: frame(),
        "quote_loader": lambda symbol: fresh_quote(),
        "sector_map": {"ABUK.CA": "Basic Resources"},
        "sector_strengths": {"Basic Resources": 0.8},
        "sector_intraday": {},
    }
    return build_portfolio_view(store, **{**options, **overrides})


# --------------------------------------------------------------------------- #
# Price selection
# --------------------------------------------------------------------------- #

def test_a_fresh_quote_is_used_and_labelled_live():
    selected = select_price(fresh_quote(11.5), frame())

    assert selected.value == pytest.approx(11.5)
    assert selected.basis == BASIS_LIVE
    assert selected.live


def test_a_stale_quote_falls_back_to_the_completed_close_and_says_why():
    """Never silently: the reason the live price was refused is what tells the
    reader whether the number in front of them is worth acting on."""

    stale = {"last": 11.0, "freshness": "STALE", "session_phase": "OPEN",
             "freshness_warning": "Rubix exchange timestamp is delayed"}

    selected = select_price(stale, frame(10.05))

    assert selected.value == pytest.approx(10.05)
    assert selected.basis == BASIS_COMPLETED_CLOSE
    assert "delayed" in selected.reason


def test_no_quote_and_no_history_leaves_no_price_at_all():
    selected = select_price(None, None)

    assert selected.value is None
    assert selected.basis == NO_PRICE


# --------------------------------------------------------------------------- #
# The view
# --------------------------------------------------------------------------- #

def test_the_view_prices_the_position_and_builds_a_plan(store):
    view = view_for(store)
    item = view.positions[0]

    assert item.symbol == "ABUK"
    assert item.price.basis == BASIS_LIVE
    assert item.value.market_value_egp == pytest.approx(11_000.0)
    assert item.plan.available
    assert item.recommendation is not None
    assert item.sector == "Basic Resources"


def test_a_symbol_whose_history_cannot_be_loaded_does_not_break_the_page(store):
    store.record_trade("HRHO", "2026-08-04", "BUY", 100, 20.0, fee_model=FREE)

    view = view_for(
        store,
        history_loader=lambda symbol: None if symbol == "HRHO" else frame(),
        quote_loader=lambda symbol: None if symbol == "HRHO" else fresh_quote(),
    )

    symbols = [item.symbol for item in view.positions]
    broken = next(item for item in view.positions if item.symbol == "HRHO")

    assert symbols == ["ABUK", "HRHO"]
    assert broken.price.basis == NO_PRICE
    assert broken.recommendation.action == "WITHHELD"


def test_an_unpriced_holding_is_counted_at_cost_and_declared(store):
    """Counting it at zero would understate the account; at market is impossible."""

    store.record_trade("HRHO", "2026-08-04", "BUY", 100, 20.0, fee_model=FREE)

    view = view_for(
        store,
        history_loader=lambda symbol: None if symbol == "HRHO" else frame(),
        quote_loader=lambda symbol: None if symbol == "HRHO" else fresh_quote(),
    )

    assert view.unpriced_count == 1
    # 11,000 at market + 2,000 at cost + cash of -12,000 from the two buys.
    assert view.total_equity_egp == pytest.approx(1_000.0)


def test_sector_exposure_groups_positions_by_sector(store):
    store.record_trade("HRHO", "2026-08-04", "BUY", 100, 20.0, fee_model=FREE)

    view = view_for(
        store,
        sector_map={"ABUK.CA": "Basic Resources", "HRHO.CA": "Basic Resources"},
    )

    assert list(view.sector_exposure) == ["Basic Resources"]


# --------------------------------------------------------------------------- #
# Plan versioning
# --------------------------------------------------------------------------- #

def test_the_first_view_writes_a_plan_and_an_unchanged_one_does_not(store):
    view_for(store)
    assert len(store.plan_history("ABUK")) == 1

    view_for(store)
    assert len(store.plan_history("ABUK")) == 1


def test_a_changed_level_writes_a_new_version(store):
    """Sessions are given explicitly: a plan is only rebuilt when the completed
    session moves, so a test about rebuilding has to move it."""

    view_for(store, session="2026-08-21")

    # A higher 20-bar low lifts support, which lifts the structural stop.
    view_for(store, session="2026-08-24",
             history_loader=lambda symbol: frame(low=11.0))

    history = store.plan_history("ABUK")
    assert len(history) == 2
    assert history[0]["stop"] > history[1]["stop"]


def test_a_stop_is_never_written_lower_than_the_one_already_in_force(store):
    view_for(store, session="2026-08-21",
             history_loader=lambda symbol: frame(low=11.0))
    raised = store.active_plan("ABUK")["stop"]

    view_for(store, session="2026-08-24")

    assert store.active_plan("ABUK")["stop"] == pytest.approx(raised)


# --------------------------------------------------------------------------- #
# Daily data is loaded once per session, not once per visit
# --------------------------------------------------------------------------- #

SESSION = "2026-08-21"


def counting_loader(calls):
    def load(symbol):
        calls.append(symbol)
        return frame()
    return load


def test_a_second_visit_in_the_same_session_loads_no_daily_data(store):
    """The whole reason this path exists: with twenty-one positions, reloading
    every daily frame on every page visit cost half a minute of spinner."""

    calls = []
    options = {"history_loader": counting_loader(calls), "session": SESSION}

    view_for(store, **options)
    first = len(calls)
    second = view_for(store, **options)

    assert first == 1
    assert len(calls) == 1, "the second visit reloaded daily data"
    assert second.positions[0].plan_reused
    assert second.positions[0].plan.stop is not None


def test_a_reused_plan_carries_the_same_levels_that_were_stored(store):
    calls = []
    options = {"history_loader": counting_loader(calls), "session": SESSION}

    built = view_for(store, **options).positions[0].plan
    reused = view_for(store, **options).positions[0].plan

    assert reused.stop == pytest.approx(built.stop)
    assert reused.target_partial == pytest.approx(built.target_partial)
    assert reused.target_final == pytest.approx(built.target_final)
    assert reused.session_date == built.session_date


def test_a_new_completed_session_reloads_the_daily_data(store):
    calls = []
    loader = counting_loader(calls)

    view_for(store, history_loader=loader, session=SESSION)
    view_for(store, history_loader=loader, session="2026-08-24")

    assert len(calls) == 2


def test_recording_a_trade_reloads_only_that_symbol(store):
    """A changed holding moves its breakeven and its time stop, so its plan is
    rebuilt -- and nothing else is."""

    store.record_trade("HRHO", "2026-08-04", "BUY", 100, 20.0, fee_model=FREE)
    calls = []
    options = {"history_loader": counting_loader(calls), "session": SESSION}
    view_for(store, **options)
    calls.clear()

    store.record_trade("ABUK", "2026-08-20", "BUY", 500, 11.0, fee_model=FREE)
    view_for(store, **options)

    assert calls == ["ABUK"]


def test_an_unknown_completed_session_always_rebuilds(store):
    """An unavailable calendar is never permission to serve stored levels."""

    calls = []
    options = {"history_loader": counting_loader(calls), "session": ""}

    view_for(store, **options)
    view_for(store, **options)

    assert len(calls) == 2


def test_a_forced_rebuild_reloads_even_when_the_plan_is_current(store):
    calls = []
    options = {"history_loader": counting_loader(calls), "session": SESSION}

    view_for(store, **options)
    view_for(store, **options, rebuild=True)

    assert len(calls) == 2


def test_a_reused_plan_still_falls_back_to_the_stored_completed_close(store):
    """Without a frame there is no close to fall back to -- unless it was
    stored with the plan, which is what keeps a stale quote from blanking the
    page rather than merely being labelled."""

    calls = []
    view_for(store, history_loader=counting_loader(calls), session=SESSION)

    stale = {"last": 11.0, "freshness": "STALE", "session_phase": "OPEN"}
    view = view_for(store, history_loader=counting_loader(calls), session=SESSION,
                    quote_loader=lambda symbol: stale)
    item = view.positions[0]

    assert item.plan_reused
    assert item.price.basis == BASIS_COMPLETED_CLOSE
    assert item.price.value == pytest.approx(11.0)


def test_an_unchanged_plan_in_a_new_session_is_restamped_not_versioned(store):
    """A session that produces the same levels is not a revision. Writing a
    version for it would bury the revisions that meant something."""

    options = {"history_loader": lambda symbol: frame()}
    view_for(store, **options, session=SESSION)
    view_for(store, **options, session="2026-08-24")

    history = store.plan_history("ABUK")
    evidence = json.loads(history[0]["evidence_json"])

    assert len(history) == 1
    # The plan's own session stays the one its candles end on; what moved is
    # the calendar expectation the stored plan is now valid for.
    assert history[0]["session_date"] == "2026-08-21"
    assert evidence["built_for_session"] == "2026-08-24"


def test_a_plan_is_reused_even_when_the_provider_is_a_session_behind(store):
    """The case that made the first version of this useless: the calendar says
    Monday is complete, the provider has not published Monday yet, so the
    frame ends on Sunday. That must still be reusable."""

    calls = []
    options = {"history_loader": counting_loader(calls), "session": "2026-08-31"}

    first = view_for(store, **options)
    view_for(store, **options)

    assert first.positions[0].plan.session_date == "2026-08-21"
    assert len(calls) == 1


# --------------------------------------------------------------------------- #
# The recommendation log
# --------------------------------------------------------------------------- #

def test_only_actionable_recommendations_are_logged(store):
    view = view_for(store)

    assert view.positions[0].recommendation.action == "HOLD"
    assert log_recommendations(store, view) == 0
    assert store.recommendations() == []


def test_the_same_recommendation_is_not_logged_twice_in_one_session(store):
    """A page left open on a refresh timer must not bury the log in duplicates,
    or the outcome measurement it exists for becomes meaningless."""

    view = view_for(store, quote_loader=lambda symbol: fresh_quote(12.0))

    assert view.positions[0].recommendation.action == "TRIM"
    assert log_recommendations(store, view) == 1
    assert log_recommendations(store, view) == 0
    assert len(store.recommendations("ABUK")) == 1


def test_a_different_rule_in_the_same_session_is_logged(store):
    log_recommendations(store, view_for(
        store, quote_loader=lambda symbol: fresh_quote(12.0)))
    log_recommendations(store, view_for(
        store, quote_loader=lambda symbol: fresh_quote(9.0)))

    rules = sorted(row["rule"] for row in store.recommendations("ABUK"))
    assert rules == ["STOP_BREACHED", "TARGET_PARTIAL_REACHED"]


def test_a_logged_recommendation_keeps_the_evidence_that_produced_it(store):
    log_recommendations(store, view_for(
        store, quote_loader=lambda symbol: fresh_quote(12.0)))
    row = store.recommendations("ABUK")[0]

    assert row["price"] == pytest.approx(12.0)
    assert row["price_basis"] == BASIS_LIVE
    assert row["plan_version"] == 1
    assert "sector" in row["evidence_json"]
