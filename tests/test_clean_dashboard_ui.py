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
    # The count is derived, not hard-coded: it duplicated the assertion above
    # and had to be edited whenever the table deliberately changed.
    assert len(display.columns) == len(SWING_PRIMARY_COLUMNS)
    # Ticker + full company name, kept as SEPARATE columns for filter/export.
    assert display.columns[1] == "اسم السهم"
    assert display.loc[0, "السهم"] == "COMI.CA"
    assert display.loc[0, "اسم السهم"] == "Commercial International Bank-Egypt (CIB)"
    assert display.loc[0, "القرار"] == "BUY"


def test_swing_default_view_keeps_trader_summary_primary():
    """The default view still leads with what a trader came for.

    Asserted on the readings and the sections, not on how they are laid out:
    this test has been rewritten twice by layout changes that did not alter
    what the page tells anyone -- first when the counts moved from `st.metric`
    to tiles, then when the tiles became a single context strip.
    """
    source = inspect.getsource(show_dashboard)
    # The three decisions, the coverage they were counted over, and the market
    # they were measured in.
    for reading in ("شراء", "متابعة", "تجنب", "BUY", "WATCH", "AVOID",
                    "التغطية", "حالة السوق"):
        assert reading in source, f"the default view lost {reading}"
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


def test_the_table_shows_the_levels_the_ratio_came_from():
    """A reward/risk figure alone is not actionable.

    The scan already computes the entry band, stop and both targets; the table
    showed only the ratio, so a reader could see 2.5 and still not know where
    to enter, where the idea is wrong, or where to take profit.
    """

    frame = _scan_frame()
    frame["BuyLow"], frame["BuyHigh"] = 98.0, 101.0
    frame["StopLoss"], frame["Target1"], frame["Target2"] = 94.0, 108.0, 115.0

    display = _swing_primary_frame(frame)

    # Formatted rather than numeric since the levels pass through `_level`,
    # which is what keeps a refused row from quoting a stop loss of 0.000.
    assert display.loc[0, "نطاق الشراء"] == "98.00 – 101.00"
    assert display.loc[0, "وقف الخسارة"] == "94.000"
    assert display.loc[0, "الهدف 1"] == "108.000"
    assert display.loc[0, "الهدف 2"] == "115.000"
    assert display.loc[0, "العائد إلى المخاطرة"] == "2.50"


def test_a_missing_entry_band_is_a_dash_not_a_single_price():
    """An entry the strategy expressed as a range must not become a point."""

    frame = _scan_frame()
    frame["BuyLow"], frame["BuyHigh"] = None, None
    assert _swing_primary_frame(frame).loc[0, "نطاق الشراء"] == "—"

    frame["BuyLow"], frame["BuyHigh"] = 12.4, 12.4
    assert _swing_primary_frame(frame).loc[0, "نطاق الشراء"] == "12.40"
