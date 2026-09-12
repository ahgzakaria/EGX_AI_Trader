"""The compact market table never prints a level the scan did not compute.

The engine leaves every level at zero for a name it refused. In the 2026-09-10
scan, 98 of the 100 AVOID rows carried ``StopLoss = 0``, ``Target1 = 0``,
``Target2 = 0``, ``RR = 0`` and ``Confidence = 0`` -- and the table rendered
each of them as a price. A stop loss of ``0.000`` is not a stop: it is the
absence of one, drawn as the most dangerous number on the row, on roughly half
of every scan.

``نطاق الشراء`` already refused a zero band. These pin that the five columns
beside it now do the same, and that a real level is still shown.

Presentation only.
"""

from __future__ import annotations

import pandas as pd
import pytest

from dashboard.home import _ABSENT_AS_ZERO, _entry_band, _level, _swing_primary_frame


# --- the level formatter ------------------------------------------------------

@pytest.mark.parametrize("absent", [0, 0.0, -0.0, -1.5, None, "", "n/a", float("nan")])
def test_a_level_that_was_never_computed_is_an_em_dash(absent):
    assert _level(absent) == "—"


@pytest.mark.parametrize("value, digits, shown", [
    (89.5, 3, "89.500"),
    (1120.0, 3, "1,120.000"),
    (2.45, 2, "2.45"),
    (95, 0, "95"),
    (0.001, 3, "0.001"),
])
def test_a_real_level_is_shown_at_its_own_precision(value, digits, shown):
    assert _level(value, digits) == shown


def test_zero_is_refused_and_a_hair_above_zero_is_not():
    """The boundary matters: the engine writes exactly 0, never 0.001."""
    assert _level(0.0) == "—"
    assert _level(0.001) == "0.001"


# --- the frame ----------------------------------------------------------------

def scan_rows():
    """One qualified name and one refused one, shaped as the scanner emits them."""
    return pd.DataFrame([
        {"Ticker": "COMI.CA", "Signal": "BUY", "Regime": "BULL", "Price": 92.40,
         "BuyLow": 91.80, "BuyHigh": 92.50, "StopLoss": 89.50, "Target1": 96.50,
         "Target2": 101.00, "RR": 2.45, "Confidence": 88,
         "OperationalStatus": "QUALIFIED_ENTRY"},
        {"Ticker": "ESRS.CA", "Signal": "AVOID", "Regime": "BEAR", "Price": 1120.00,
         "BuyLow": 0.0, "BuyHigh": 0.0, "StopLoss": 0.0, "Target1": 0.0,
         "Target2": 0.0, "RR": 0.0, "Confidence": 0,
         "OperationalStatus": "MARKET_CLOSED"},
    ])


@pytest.fixture
def frame():
    return _swing_primary_frame(scan_rows())


@pytest.mark.parametrize("column", sorted(_ABSENT_AS_ZERO))
def test_a_refused_row_quotes_no_level_at_all(frame, column):
    assert frame[column].iloc[1] == "—"


def test_a_refused_row_shows_no_zero_anywhere(frame):
    """Not in one column. In none of them -- one cell saying "no band" while
    the next four quote 0.000 is the shape the defect actually had."""
    row = frame.iloc[1]
    for column in (*_ABSENT_AS_ZERO, "نطاق الشراء"):
        assert "0.000" not in str(row[column])
        assert str(row[column]).strip() not in ("0", "0.0", "0.00")


def test_a_qualified_row_keeps_every_level(frame):
    row = frame.iloc[0]
    assert row["وقف الخسارة"] == "89.500"
    assert row["الهدف 1"] == "96.500"
    assert row["الهدف 2"] == "101.000"
    assert row["العائد إلى المخاطرة"] == "2.45"
    assert row["نطاق الشراء"] == "91.80 – 92.50"


def test_the_decision_and_the_price_are_still_there_on_a_refused_row(frame):
    """A refusal is still a row: the reader has to be able to see what was
    refused and at what price, or the table cannot be scanned."""
    row = frame.iloc[1]
    assert row["القرار"] == "AVOID"
    assert float(row["السعر"]) == 1120.00
    assert row["الحالة التشغيلية"] == "MARKET_CLOSED"


def test_the_scan_results_are_not_mutated():
    source = scan_rows()
    before = source.copy(deep=True)
    _swing_primary_frame(source)
    pd.testing.assert_frame_equal(source, before)


def test_a_missing_column_does_not_break_the_frame():
    """Not every caller passes every column."""
    thin = pd.DataFrame([{"Ticker": "COMI.CA", "Signal": "BUY", "Price": 92.4}])
    view = _swing_primary_frame(thin)
    assert view["السهم"].iloc[0] == "COMI.CA"


# --- the band, which already worked, stays working ----------------------------

@pytest.mark.parametrize("low, high, shown", [
    (91.80, 92.50, "91.80 – 92.50"),
    (0.0, 0.0, "—"),
    (0.0, 92.50, "—"),
    (None, 92.50, "—"),
    (92.50, 92.50, "92.50"),
])
def test_the_entry_band_is_unchanged(low, high, shown):
    assert _entry_band(low, high) == shown
