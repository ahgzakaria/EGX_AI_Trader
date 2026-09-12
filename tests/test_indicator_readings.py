"""The technical section reads the indicators instead of dumping them.

What the page did: sixteen raw values in five panels, and nothing said about
any of them. SMA 20 beside EMA 20 a tenth of a pound apart (two panels, one
fact), MACD beside its own signal beside the histogram that is the difference
between them (three rows, one fact), and an on-balance volume of 1,375,847,323
-- a running total from an arbitrary start, whose absolute value means nothing.

These pin the rule that makes the new section safe: every line is a
**restatement of supplied fields**. Nothing here computes an indicator, nothing
invents a threshold this project has not validated, and a reading that cannot
be made from the fields present is omitted rather than guessed.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from core.ai_stock_analysis_contract import IndicatorSummary
from dashboard import ai_stock_analysis as page
from dashboard.ai_stock_analysis_components import (indicator_groups,
                                                    indicator_readings)


def indicators(**fields) -> IndicatorSummary:
    base = dict(symbol="COMI.CA", computed_from_sessions=250, volume_safe=True)
    base.update(fields)
    return IndicatorSummary(**base)


def by_label(readings):
    return {label: (reading, tone, basis) for label, reading, tone, basis in readings}


def rendered(readings):
    """`_readings_list` output, captured."""
    markup = []
    original = page.st.markdown
    page.st.markdown = lambda body, **kwargs: markup.append(body)
    try:
        page._readings_list(readings)
    finally:
        page.st.markdown = original
    return markup[0]


# --- the close against its own averages ---------------------------------------

@pytest.mark.parametrize("close, expected, tone", [
    (100.0, "فوق متوسطاته الـ3", "green"),
    (10.0, "تحت متوسطاته الـ3", "red"),
    (55.0, None, "amber"),                      # above 20 and 50, under 200
])
def test_the_close_is_placed_against_its_averages(close, expected, tone):
    readings = by_label(indicator_readings(
        indicators(sma_20=50.0, sma_50=52.0, sma_200=60.0), close))
    text, actual_tone, _ = readings["الإغلاق مقابل متوسطاته"]
    assert actual_tone == tone
    if expected:
        assert text == expected


def test_the_count_names_how_many_averages_actually_exist():
    """Two of three supplied must not be reported as three."""
    readings = by_label(indicator_readings(
        indicators(sma_20=50.0, sma_50=52.0), 100.0))
    text, _, basis = readings["الإغلاق مقابل متوسطاته"]
    assert "الـ2" in text
    assert "200" not in basis


def test_no_averages_means_no_line_rather_than_a_guess():
    assert not indicator_readings(indicators(), 100.0)


def test_a_missing_close_cannot_be_compared_to_anything():
    readings = by_label(indicator_readings(indicators(sma_20=50.0), None))
    assert "الإغلاق مقابل متوسطاته" not in readings


# --- the EMAs stay out of the headline ----------------------------------------

def test_the_emas_are_not_a_second_reading():
    """Same three windows, different weighting. On the evidence that prompted
    this, SMA 20 and EMA 20 sat 0.12% apart."""
    readings = indicator_readings(
        indicators(sma_20=50.0, sma_50=52.0, sma_200=60.0,
                   ema_20=50.06, ema_50=52.1, ema_200=60.2), 100.0)
    assert len(readings) == 1
    assert "EMA" not in "".join(basis for *_, basis in readings)


def test_the_emas_are_still_available_in_the_raw_values():
    """Withheld from the headline is not the same as deleted."""
    titles = [title_en for _, title_en, _ in indicator_groups(
        indicators(ema_20=50.0, ema_50=52.0, ema_200=60.0))]
    assert "Exponential Moving Averages" in titles


# --- MACD as one fact ----------------------------------------------------------

@pytest.mark.parametrize("macd, signal, expected, tone", [
    (1.2, 0.9, "فوق الإشارة", "green"),
    (0.4, 0.9, "تحت الإشارة", "red"),
])
def test_the_three_macd_fields_say_one_thing(macd, signal, expected, tone):
    readings = by_label(indicator_readings(
        indicators(macd=macd, macd_signal=signal, macd_histogram=macd - signal), None))
    text, actual_tone, basis = readings["MACD مقابل إشارته"]
    assert (text, actual_tone) == (expected, tone)
    assert "الهيستوجرام" in basis          # it says where the third number went


def test_a_macd_line_without_its_signal_is_not_read():
    assert "MACD مقابل إشارته" not in by_label(
        indicator_readings(indicators(macd=1.2), None))


# --- RSI, with the convention named -------------------------------------------

@pytest.mark.parametrize("value, band", [
    (78.0, "نطاق التشبع الشرائي"), (22.0, "نطاق التشبع البيعي"),
    (51.0, "منتصف النطاق"),
])
def test_rsi_names_its_band(value, band):
    text, _, basis = by_label(
        indicator_readings(indicators(rsi_14=value), None))["مؤشر القوة النسبية"]
    assert band in text
    assert "عُرف" in basis, "the 30/70 band is presented as validated"


def test_the_rsi_band_is_never_presented_as_this_projects_measurement():
    _, _, basis = by_label(
        indicator_readings(indicators(rsi_14=78.0), None))["مؤشر القوة النسبية"]
    assert "ليست عتبة مقيسة" in basis


# --- volatility and volume -----------------------------------------------------

def test_atr_is_expressed_against_the_price_so_names_can_be_compared():
    text, _, _ = by_label(indicator_readings(
        indicators(atr_14=2.5), 100.0))["مدى الحركة اليومي"]
    assert text.startswith("2.5%")


def test_atr_without_a_close_cannot_be_made_a_percentage():
    assert "مدى الحركة اليومي" not in by_label(
        indicator_readings(indicators(atr_14=2.5), None))


def test_a_zero_close_does_not_divide():
    """`close` arrives as 0.0 from a name that never printed."""
    assert "مدى الحركة اليومي" not in by_label(
        indicator_readings(indicators(atr_14=2.5), 0.0))


def test_volume_is_read_against_the_names_own_normal():
    text, tone, _ = by_label(indicator_readings(
        indicators(volume_ratio=2.4), None))["حجم الجلسة"]
    assert text == "2.40× متوسطه"
    assert tone == "green"


def test_withheld_volume_cannot_produce_a_volume_reading():
    """volume_safe False means the fields are None, and None is not a reading."""
    assert "حجم الجلسة" not in by_label(
        indicator_readings(indicators(volume_safe=False), 100.0))


# --- OBV -----------------------------------------------------------------------

def test_obv_is_never_promoted_to_a_reading():
    """A running total from an arbitrary start. Its level says nothing."""
    readings = indicator_readings(indicators(obv=1_375_847_323.0), 100.0)
    assert not any("OBV" in text or "الرصيد" in label
                   for label, text, _, _ in readings)


def test_obv_is_labelled_as_cumulative_where_it_does_appear():
    labels = [label_en for _, _, rows in indicator_groups(
        indicators(obv=1_375_847_323.0, volume_safe=True))
        for _, label_en, _ in rows]
    assert any("cumulative" in label.lower() for label in labels)


# --- the source of every line --------------------------------------------------

def test_every_reading_names_what_it_compared():
    readings = indicator_readings(
        indicators(sma_20=50.0, sma_50=52.0, sma_200=60.0, rsi_14=64.0,
                   macd=1.2, macd_signal=0.9, atr_14=2.5, volume_ratio=1.1), 100.0)
    assert len(readings) == 5
    for label, text, tone, basis in readings:
        assert basis.strip(), f"{label} states a reading with no stated basis"
        assert tone in ("green", "amber", "red", "gray")


def test_nothing_in_the_readings_computes_an_indicator():
    """Restatement only: no rolling window, no series, no history.

    Comments and the docstring are stripped first -- an earlier test in this
    repo failed three times on prose that was explaining the very thing being
    banned.
    """
    tree = ast.parse(inspect.getsource(indicator_readings))
    tree.body[0].body = [node for node in tree.body[0].body
                         if not (isinstance(node, ast.Expr)
                                 and isinstance(node.value, ast.Constant))]
    body = ast.unparse(tree)
    for forbidden in ("rolling", "history", "ewm", "session_series", "fetch",
                      "pd.", "np."):
        assert forbidden not in body, f"the readings reach for {forbidden}"


# --- the page ------------------------------------------------------------------

def test_the_section_leads_with_readings_and_hides_the_raw_values():
    source = inspect.getsource(page._technical_section)
    assert "_readings_list(" in source
    assert source.index("_readings_list(") < source.index("st.expander(")


def test_the_raw_panels_are_rendered_under_the_expander():
    """Not merely present somewhere on the page."""
    source = inspect.getsource(page._technical_section)
    split = source.index("st.expander(")
    assert "_kv_table(rows)" in source[split:]
    assert "_kv_table(rows)" not in source[:split]


def test_the_readings_take_their_colour_from_the_one_palette():
    from dashboard.ui import TONE_VARS

    body = rendered([("الإغلاق مقابل متوسطاته", "فوق متوسطاته الـ3",
                      "green", "مقارنة الإغلاق بـ SMA 20، 50، 200")])
    assert TONE_VARS["green"] in body
    assert "#" not in body, "a literal hex bypasses the palette"


def test_a_reading_cannot_inject_markup():
    assert "<script>" not in rendered(
        [("<script>a</script>", "<script>b</script>", "green", "<script>c</script>")])


def test_an_unrecognised_tone_falls_back_rather_than_rendering_nothing():
    """`var(--wat)` is undeclared, and an undeclared var renders as nothing."""
    body = rendered([("x", "y", "chartreuse", "z")])
    assert "var(--chartreuse)" not in body
    assert "var(--muted)" in body
