"""Presentation regressions for readable Swing/Daily and shared UI styling."""

from __future__ import annotations

import inspect
from pathlib import Path

import pandas as pd

from dashboard.home import (
    SWING_PRIMARY_COLUMNS,
    _render_swing_advanced_research,
    _swing_primary_frame,
    show_dashboard,
)


def _scan_frame():
    return pd.DataFrame(
        [
            {
                "Ticker": "COMI.CA",
                "Signal": "BUY",
                "Regime": "TRENDING",
                "Price": 100.0,
                "RR": 2.5,
                "Confidence": 80,
                "OperationalStatus": "ACTIONABLE",
                "Score": 90,
                "Reasons": "unchanged strategy evidence",
            }
        ]
    )


def test_swing_primary_table_is_compact_and_does_not_change_results():
    original = _scan_frame()
    before = original.copy(deep=True)
    display = _swing_primary_frame(original)

    pd.testing.assert_frame_equal(original, before)
    assert tuple(display.columns) == SWING_PRIMARY_COLUMNS
    # Ticker + full company name, kept as SEPARATE columns for filter/export.
    assert len(display.columns) == 8
    assert display.columns[1] == "اسم السهم"
    assert display.loc[0, "السهم"] == "COMI.CA"
    assert display.loc[0, "اسم السهم"] == "Commercial International Bank-Egypt (CIB)"
    assert display.loc[0, "القرار"] == "BUY"


def test_swing_default_view_keeps_trader_summary_primary():
    source = inspect.getsource(show_dashboard)
    assert "تغطية البيانات" in source
    assert "حالة السوق" in source
    assert "شراء (BUY)" in source
    assert "متابعة (WATCH)" in source
    assert "تجنب (AVOID)" in source
    assert "أهم الفرص القابلة للمتابعة" in source
    assert "جدول السوق المختصر" in source
    assert source.count("_render_swing_advanced_research(") == 1


def test_comparisons_charts_and_developer_fields_are_advanced_only():
    default_source = inspect.getsource(show_dashboard)
    advanced_source = inspect.getsource(_render_swing_advanced_research)
    assert "Classic vs BREAKOUT_SWING" not in default_source
    assert "Adaptive per-symbol decisions" not in default_source
    assert "Full signal/regime charts" not in default_source
    assert 'st.expander("البحث المتقدم (Advanced Research)"' in advanced_source
    assert "Classic versus Breakout comparison" in advanced_source
    assert "Adaptive per-symbol decisions" in advanced_source
    assert "Developer metrics and attribution" in advanced_source


def test_global_style_meets_readability_targets():
    source = Path("dashboard/ui.py").read_text(encoding="utf-8")
    assert "font-size:16px" in source
    assert "[data-testid=\"stDataFrame\"]" in source
    assert "font-size:15px" in source
    assert "min-height: 46px" in source
    assert "@media (max-width: 900px)" in source


def test_system_health_owns_developer_diagnostics_language():
    source = Path("dashboard/system_health.py").read_text(encoding="utf-8")
    for label in (
        "تفاصيل التغطية",
        "معرّفات الفحص",
        "المهام المجدولة",
        "اللقطات غير الصالحة",
        "إخفاقات المصدر",
        "بصمات قواعد البيانات",
        "العمليات",
    ):
        assert label in source
