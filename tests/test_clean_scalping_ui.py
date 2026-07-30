"""Regressions for the consolidated presentation-only scalping workspace."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from dashboard.scalping import (
    LIVE_MONITOR_PRIMARY_COLUMNS,
    RANGE_BOUND_PRIMARY_COLUMNS,
    SCALPING_TABS,
    _live_monitor_primary_frame,
    _range_bound_primary_frame,
    show_scalping_dashboard,
)
from dashboard.uptrend_pullback import (
    UPTREND_PRIMARY_COLUMNS,
    UptrendPullbackView,
    load_uptrend_pullback_view,
    primary_uptrend_frame,
)


def _member(symbol="EGCH"):
    return {
        "symbol": symbol,
        "support_zone_low": 11.9,
        "support_zone_high": 12.8,
        "resistance_zone_low": 13.8,
        "resistance_zone_high": 14.8,
    }


def _record():
    return SimpleNamespace(
        displayed=(_member("EGCH"), _member("ORWE")),
        header={"watchlist_id": "frozen-1"},
    )


def _live_item(symbol, state="ENTRY_TRIGGER_FORMING"):
    return SimpleNamespace(
        symbol=symbol,
        current_price=12.4,
        live_state=state,
        data_quality_status="AVAILABLE_AND_VALIDATED",
        no_chase_reason=None,
        invalidation_condition=None,
        explanations=(),
    )


def test_scalping_dashboard_has_exactly_the_three_required_tabs():
    assert SCALPING_TABS == (
        "تداول داخل رينج ثابت (Stable Range-Bound)",
        "اتجاه صاعد قرب الدعم (Uptrend Pullback)",
        "المتابعة اللحظية (Live Monitor)",
    )
    source = inspect.getsource(show_scalping_dashboard)
    assert source.count("st.tabs(") == 1
    assert "range_tab, uptrend_tab, live_tab = st.tabs(SCALPING_TABS)" in source


def test_range_bound_primary_table_has_only_seven_trader_columns():
    batch = SimpleNamespace(
        results=(
            _live_item("EGCH"),
            _live_item("ORWE"),
            _live_item("LIVE-MOVER-NOT-FROZEN"),
        )
    )
    frame = _range_bound_primary_frame(_record(), batch)
    assert tuple(frame.columns) == RANGE_BOUND_PRIMARY_COLUMNS
    # Seven trader columns plus the separate company-name column.
    assert len(frame.columns) == 8
    assert frame.columns[1] == "اسم السهم"
    assert list(frame["السهم"]) == ["EGCH", "ORWE"]
    assert "score" not in " ".join(frame.columns).lower()
    assert "slope" not in " ".join(frame.columns).lower()


def test_live_monitor_uses_only_frozen_historical_membership():
    batch = SimpleNamespace(
        results=(
            _live_item("ORWE"),
            _live_item("LIVE-MOVER-NOT-FROZEN"),
            _live_item("EGCH", "MOVE_EXTENDED_DO_NOT_CHASE"),
        )
    )
    frame = _live_monitor_primary_frame(_record(), batch)
    assert tuple(frame.columns) == LIVE_MONITOR_PRIMARY_COLUMNS
    assert list(frame["السهم"]) == ["EGCH", "ORWE"]
    assert set(frame["الاستراتيجية"]) == {
        "STABLE_RANGE_BOUND · تداول داخل رينج ثابت"
    }
    assert (
        frame.loc[frame["السهم"] == "EGCH", "جاهزية الدخول البحثية"].iloc[0]
        == "ممتد — لا تطارده"
    )


def test_uptrend_adapter_reads_service_without_preparing():
    service = SimpleNamespace(
        get_for_session=lambda _target: SimpleNamespace(
            status="WATCHLIST_NOT_GENERATED",
            record=None,
            snapshot=None,
            detail="not prepared",
        )
    )
    view = load_uptrend_pullback_view(
        service,
        target_session_date="2026-07-30",
    )
    frame = primary_uptrend_frame(view)
    assert view.available is True
    assert view.status == "WATCHLIST_NOT_GENERATED"
    assert view.candidates == ()
    assert frame.empty
    assert tuple(frame.columns) == UPTREND_PRIMARY_COLUMNS


def test_primary_scalping_workflow_has_only_two_clear_actions():
    source = inspect.getsource(show_scalping_dashboard)
    assert "تجهيز قائمة السكالبنج" in source
    assert "تحديث المتابعة اللحظية" in source
    assert "Load Scan" not in source
    assert "Refresh Scan" not in source
    assert "Run Pre-session Scan" not in source
    assert "Research Rebuild" not in source
    assert "_run_scan" not in source
    assert "rebuild_for_research" not in source
    assert "_prepare_scalping_watchlists(" in source


def test_status_bar_is_rendered_once_and_keeps_production_disabled():
    source = inspect.getsource(show_scalping_dashboard)
    assert source.count("status_bar(") == 1
    status_source = inspect.getsource(
        __import__(
            "dashboard.scalping", fromlist=["_scalping_status_items"]
        )._scalping_status_items
    )
    assert '("Production", "DISABLED", "red")' in status_source


def test_arabic_first_labels_and_required_live_states_are_present():
    source = inspect.getsource(
        __import__("dashboard.scalping", fromlist=["_LIVE_STATE_AR"])
    )
    for label in (
        "انتظار الاقتراب",
        "داخل منطقة المتابعة",
        "تأكيد يتكوّن",
        "جاهزية بحثية",
        "ممتد — لا تطارده",
        "بيانات قديمة",
        "سبريد غير مناسب",
        "مزاد الإغلاق — لا دخول جديد",
        "الجلسة مغلقة",
    ):
        assert label in source


def test_uptrend_empty_state_does_not_fabricate_rows():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string(
        """
from dashboard.uptrend_pullback import render_uptrend_pullback_tab
from dashboard.uptrend_pullback import UptrendPullbackView
render_uptrend_pullback_tab(
    UptrendPullbackView(
        available=True,
        status="WATCHLIST_NOT_GENERATED",
        detail="not prepared",
    )
)
"""
    )
    app.run(timeout=20)
    assert not app.exception
    rendered = " ".join(
        str(element.value)
        for collection in (app.markdown, app.caption)
        for element in collection
    )
    assert "لا توجد قائمة اتجاه صاعد ثابتة جاهزة" in rendered
    assert "Research Only" in rendered
    assert len(app.dataframe) == 0
