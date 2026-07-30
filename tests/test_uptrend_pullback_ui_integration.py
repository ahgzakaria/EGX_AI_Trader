"""Focused regressions for the typed Uptrend Pullback dashboard connection."""

from __future__ import annotations

from datetime import datetime, timezone
import inspect
from types import SimpleNamespace

from dashboard.scalping import (
    _live_monitor_primary_frame,
    _prepare_scalping_watchlists,
)
from dashboard.uptrend_pullback import (
    UPTREND_PRIMARY_COLUMNS,
    UptrendPullbackView,
    primary_uptrend_frame,
    uptrend_live_frame,
)
from scalping_expected_range.live_readiness import (
    CLOSING_AUCTION_NO_NEW_ENTRY,
    RubixBatchSnapshot,
    RubixQuote,
    RubixSymbolSnapshot,
)
from scalping_uptrend_pullback.live_readiness import (
    UptrendLiveReadinessEngine,
)
from scalping_uptrend_pullback.states import (
    INSUFFICIENT_UPSIDE,
    STRATEGY_IDENTITY,
    UPTREND_NEAR_SUPPORT,
)


def _uptrend_member(symbol="DSCW", rank=1, state=UPTREND_NEAR_SUPPORT):
    return {
        "symbol": symbol,
        "strategy_identity": STRATEGY_IDENTITY,
        "eligible_rank": rank,
        "total_score": 81.25,
        "candidate_state": state,
        "support_zone_lower": 9.8,
        "support_zone_upper": 10.0,
        "distance_from_support_percent": 0.3,
        "first_research_target": 10.8,
        "invalidation_level": 9.5,
    }


def _uptrend_record(*members):
    return SimpleNamespace(
        header={
            "status": "READY",
            "strategy_identity": STRATEGY_IDENTITY,
            "watchlist_id": "uptrend-frozen-1",
            "target_session_date": "2026-07-30",
            "displayed_count": len(members),
            "candidate_limit": 20,
            "historical_data_cutoff": "2026-07-29",
        },
        displayed=tuple(members),
    )


def _view(*members):
    record = _uptrend_record(*members)
    return UptrendPullbackView(
        available=True,
        status="WATCHLIST_READY",
        record=record,
        candidates=record.displayed,
        data_cutoff="2026-07-29",
        source="EODHD_DAILY",
    )


def _snapshot(price, evaluated_at):
    quote = RubixQuote(
        symbol="DSCW",
        last_price=price,
        bid=price - 0.01,
        ask=price + 0.01,
        cumulative_volume=1_000_000,
        change_percent=0.0,
        market_timestamp=evaluated_at,
        received_at=evaluated_at,
    )
    symbol = RubixSymbolSnapshot("DSCW", quote, ())
    return RubixBatchSnapshot(
        "2026-07-30",
        ("DSCW",),
        {"DSCW": symbol},
        evaluated_at.isoformat(),
        1,
        2,
        0.1,
    )


def test_uptrend_primary_table_is_seven_columns_and_excludes_insufficient_upside():
    view = _view(
        _uptrend_member(),
        _uptrend_member("REJECTED", 2, INSUFFICIENT_UPSIDE),
    )
    frame = primary_uptrend_frame(view)
    assert tuple(frame.columns) == UPTREND_PRIMARY_COLUMNS
    # Seven trader columns plus the separate company-name column.
    assert len(frame.columns) == 8
    assert frame.columns[1] == "اسم السهم"
    assert list(frame["السهم"]) == ["DSCW"]


def test_same_symbol_can_exist_in_two_distinct_strategy_monitors():
    range_record = SimpleNamespace(
        displayed=(
            {
                "symbol": "DSCW",
                "support_zone_low": 9.0,
                "support_zone_high": 9.2,
            },
        )
    )
    range_frame = _live_monitor_primary_frame(range_record)
    uptrend_frame = uptrend_live_frame(_view(_uptrend_member()))
    assert list(range_frame["السهم"]) == ["DSCW"]
    assert list(uptrend_frame["السهم"]) == ["DSCW"]
    assert (
        range_frame["الاستراتيجية"].iloc[0]
        == "STABLE_RANGE_BOUND · تداول داخل رينج ثابت"
    )
    assert (
        uptrend_frame["الاستراتيجية"].iloc[0]
        == "UPTREND_PULLBACK_SCALPING_V1 · اتجاه صاعد قرب الدعم"
    )


def test_live_prices_cannot_change_uptrend_membership_rank_or_score():
    member = _uptrend_member()
    record = _uptrend_record(member)
    engine = UptrendLiveReadinessEngine(SimpleNamespace())
    first_at = datetime(2026, 7, 30, 9, 0, tzinfo=timezone.utc)
    second_at = datetime(2026, 7, 30, 9, 1, tzinfo=timezone.utc)
    first = engine.evaluate(
        record,
        evaluated_at=first_at,
        live_snapshot=_snapshot(9.95, first_at),
    )
    second = engine.evaluate(
        record,
        evaluated_at=second_at,
        live_snapshot=_snapshot(10.5, second_at),
    )
    assert first.symbols_requested == second.symbols_requested == ("DSCW",)
    assert first.results[0].historical_rank == second.results[0].historical_rank == 1
    assert first.results[0].historical_score == second.results[0].historical_score
    assert member["eligible_rank"] == 1
    assert member["total_score"] == 81.25


def test_closing_auction_blocks_uptrend_new_entries_without_reading_rubix():
    member = _uptrend_member()
    record = _uptrend_record(member)
    reader = SimpleNamespace(
        load=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("auction must not query Rubix")
        )
    )
    batch = UptrendLiveReadinessEngine(reader).evaluate(
        record,
        evaluated_at=datetime(
            2026,
            7,
            30,
            11,
            20,
            tzinfo=timezone.utc,
        ),
    )
    assert batch.results[0].live_state == CLOSING_AUCTION_NO_NEW_ENTRY


def test_single_prepare_action_invokes_both_services_independently(monkeypatch):
    missing = SimpleNamespace(status="WATCHLIST_NOT_GENERATED", record=None)
    ready_range = SimpleNamespace(
        status="WATCHLIST_READY",
        record=SimpleNamespace(header={"displayed_count": 3}),
    )
    range_service = SimpleNamespace(
        get_for_session=lambda _target: missing,
        prepare_for_session=lambda _target: ready_range,
    )
    uptrend_record = SimpleNamespace(header={"displayed_count": 3})
    prepared_view = UptrendPullbackView(
        True,
        "WATCHLIST_READY",
        uptrend_record,
    )
    calls = []

    def prepare_uptrend(service, *, target_session_date):
        calls.append((service, target_session_date))
        return prepared_view

    monkeypatch.setattr(
        "dashboard.scalping.prepare_uptrend_pullback_view",
        prepare_uptrend,
    )
    uptrend_service = object()
    range_result, uptrend_view, messages = _prepare_scalping_watchlists(
        range_service,
        "2026-07-30",
        missing,
        uptrend_service,
        "2026-07-30",
        UptrendPullbackView(True, "WATCHLIST_NOT_GENERATED"),
    )
    assert range_result is ready_range
    assert uptrend_view is prepared_view
    assert calls == [(uptrend_service, "2026-07-30")]
    assert len(messages) == 2


def test_dashboard_adapter_contains_no_strategy_calculation_or_execution_route():
    from dashboard import uptrend_pullback
    from scalping_uptrend_pullback import live_readiness

    adapter_source = inspect.getsource(uptrend_pullback)
    live_source = inspect.getsource(live_readiness)
    for forbidden in (
        "analyze_uptrend_pullback",
        "build_frozen_uptrend_watchlist",
        "yahoo",
        "place_order",
        "paper_trade",
        "portfolio",
    ):
        assert forbidden not in adapter_source.lower()
        assert forbidden not in live_source.lower()
    assert STRATEGY_IDENTITY == "UPTREND_PULLBACK_SCALPING_V1"
    assert "ENTRY_READY_RESEARCH_ONLY" in live_source
