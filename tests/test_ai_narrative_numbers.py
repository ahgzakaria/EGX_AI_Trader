"""Exactness tests for the field-aware numeric allow-list (no epsilon anywhere).

The evidence below is hand-built so the boundary cases are unambiguous, including a
low-priced security (1.84) where any absolute epsilon would be catastrophic: with the old
0.05 tolerance, "1.89" — a 2.7% error — would have been accepted as a rendering of 1.84.
"""

from __future__ import annotations

import pytest

from core.ai_narrative_numbers import (
    KIND_INDICATOR,
    KIND_PRICE,
    KIND_VOLUME,
    build_number_allowlist,
    canonical_key,
    normalize_text,
    renderings,
    validate_numbers,
)
from core.ai_stock_analysis_contract import (
    AnalysisRequest,
    AnalysisResult,
    ConfidenceBreakdown,
    ConfidenceComponent,
    DataQualitySummary,
    DataStatus,
    IndicatorSummary,
    KeyLevel,
    MarketPhase,
    MomentumState,
    PriceSummary,
    Recommendation,
    ScenarioResult,
    ScenarioState,
    TrendState,
)

GEN_AT = "2026-07-22T13:40:02+03:00"

ARABIC_DIGITS = {ord(str(i)): chr(0x0660 + i) for i in range(10)}


def to_arabic(text: str) -> str:
    """Render a Western-digit string with Arabic-Indic digits and separators."""
    return text.translate(ARABIC_DIGITS).replace(".", "٫").replace(",", "٬")


@pytest.fixture()
def result():
    """One low-priced security with the exact values the audit calls out."""
    request = AnalysisRequest(symbol="LOWP", request_id="req-num-0001", as_of=GEN_AT,
                              market_phase=MarketPhase.CONTINUOUS)
    price = PriceSummary(
        symbol="LOWP", session_date="2026-07-22", close=1.84, previous_close=1.60,
        change_amount=0.24, change_percent=15.37, open=1.62, high=1.86, low=1.60,
        volume=3_200_000.0, turnover=5_888_000.0)
    indicators = IndicatorSummary(
        symbol="LOWP", computed_from_sessions=250, trend=TrendState.UPTREND,
        trend_strength=80.0, momentum=MomentumState.POSITIVE, momentum_strength=62.0,
        sma_20=1.75, ema_20=1.78, rsi_14=61.5, macd=-1.42, macd_signal=-1.05,
        macd_histogram=-0.37, atr_14=0.09, average_volume_20=3_150_000.0,
        volume_ratio=1.02, expected_range_position=71.2, obv=48_250_000.0)
    scenario = ScenarioResult(
        scenario_id="s1", title="Breakout continuation", state=ScenarioState.NEAR_READY,
        trigger=93.50, entry_low=93.60, entry_high=94.20, target=97.80, stop=91.00,
        remaining_room_percent=5.84, risk_reward=1.90, confidence=0.55,
        confirmation_requirements=("close above 93.50",),
        invalidation_conditions=("daily close below 91.00",))
    return AnalysisResult(
        request=request, price=price, indicators=indicators,
        confidence=ConfidenceBreakdown(
            overall=63.0, method_version="conf@1",
            components=(ConfidenceComponent(name="trend", weight=0.4, score=80.0),)),
        data_quality=DataQualitySummary(status=DataStatus.CURRENT,
                                        latest_completed_session="2026-07-22"),
        recommendation=Recommendation.NEAR_READY, market_phase=MarketPhase.CONTINUOUS,
        evidence_version="evidence@LOWP@2026-07-22@abc", generated_at=GEN_AT,
        key_levels=(KeyLevel(kind="SUPPORT", price=1.75, basis="20-session low",
                             distance_percent=4.89, strength=0.6),),
        scenarios=(scenario,),
        recommendation_reasons=("price above SMA20",),
        evidence_hash="sha256:abc")


def _ok(result, text):
    return validate_numbers(text, build_number_allowlist(result))


# --------------------------------------------------------------------------- #
# The audit's explicit rejections — a low-priced security has no epsilon to hide in
# --------------------------------------------------------------------------- #

# "1.9" is deliberately absent from this list: it is the supplied risk/reward value, so it
# is traceable evidence in its own right — the allow-list is per-value, not per-field.
@pytest.mark.parametrize("written", ["1.89", "1.85", "1.80", "1.8", "1.83", "1.86000001"])
def test_near_miss_of_a_low_price_is_rejected(result, written):
    ok, offending = _ok(result, f"الإغلاق {written} جنيه.")
    assert not ok and written in offending


def test_altered_target_9785_is_rejected(result):
    ok, offending = _ok(result, "الهدف 97.85.")
    assert not ok and offending == ["97.85"]


def test_altered_stop_9105_is_rejected(result):
    ok, offending = _ok(result, "الوقف 91.05.")
    assert not ok and offending == ["91.05"]


def test_altered_percentage_is_rejected(result):
    ok, offending = _ok(result, "التغير 15.42%.")
    assert not ok and offending == ["15.42%"]


def test_no_absolute_epsilon_survives_anywhere(result):
    # Every one of these lies inside the old 0.05 absolute window of a real value.
    for written in ("1.88", "97.83", "91.04", "15.40", "1.79"):
        ok, _ = _ok(result, f"القيمة {written}.")
        assert not ok, f"{written} must not be accepted by proximity"


def test_unrelated_value_near_a_large_volume_is_rejected(result):
    ok, offending = _ok(result, "حجم التداول 3,200,001 سهم.")
    assert not ok and offending == ["3,200,001"]


# --------------------------------------------------------------------------- #
# The audit's explicit acceptances
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("written", ["1.84", "1.840", "1.8400", "1.84000"])
def test_trailing_zero_variants_of_the_same_value_are_accepted(result, written):
    ok, offending = _ok(result, f"الإغلاق {written} جنيه.")
    assert ok, offending


def test_arabic_indic_equivalent_is_accepted(result):
    ok, offending = _ok(result, f"الإغلاق {to_arabic('1.84')} جنيه.")
    assert ok, offending


def test_arabic_indic_near_miss_is_still_rejected(result):
    ok, offending = _ok(result, f"الإغلاق {to_arabic('1.89')} جنيه.")
    assert not ok and offending


def test_thousands_separator_equivalents_are_accepted(result):
    for written in ("3200000", "3,200,000", to_arabic("3,200,000")):
        ok, offending = _ok(result, f"حجم التداول {written} سهم.")
        assert ok, (written, offending)


def test_percent_variants_of_a_percentage_field_are_accepted(result):
    for written in ("15.37%", "15.37٪", "15.37 %", "15.37"):
        ok, offending = _ok(result, f"التغير {written}.")
        assert ok, (written, offending)


def test_negative_indicator_formatting_is_accepted_only_for_the_same_value(result):
    for written in ("-1.42", "-1.420", "−1.42"):
        ok, offending = _ok(result, f"مؤشر الماكد {written}.")
        assert ok, (written, offending)
    for written in ("1.42", "-1.43", "-1.40"):
        ok, _ = _ok(result, f"مؤشر الماكد {written}.")
        assert not ok, f"{written} must not be approved by the MACD value -1.42"


def test_unit_ratio_confidence_accepts_both_supplied_and_displayed_forms(result):
    for written in ("0.55", "55"):
        ok, offending = _ok(result, f"ثقة السيناريو {written}.")
        assert ok, (written, offending)
    ok, _ = _ok(result, "ثقة السيناريو 56.")
    assert not ok


def test_evidence_string_numbers_stay_traceable(result):
    # Session date and machine-reason numbers are auditable evidence text.
    ok, offending = _ok(result, "آخر جلسة مكتملة 2026-07-22 والسعر فوق SMA20.")
    assert ok, offending


def test_percent_sign_is_gated_by_field_kind(result):
    # 1.84 is a price; writing it as a percentage misrepresents the evidence.
    ok, offending = _ok(result, "الإغلاق 1.84%.")
    assert not ok and offending == ["1.84%"]


# --------------------------------------------------------------------------- #
# Unit-level behaviour
# --------------------------------------------------------------------------- #

def test_renderings_never_include_a_different_value():
    approved = renderings(1.84, KIND_PRICE)
    assert {"1.84", "1.840", "1.8400"} <= approved
    assert approved.isdisjoint({"1.8", "1.80", "1.85", "1.89", "1.9", "2"})


def test_renderings_of_an_unrounded_value_include_the_display_precision():
    approved = renderings(1.8433, KIND_PRICE)
    assert "1.84" in approved and "1.8433" in approved
    assert "1.85" not in approved and "1.8" not in approved


def test_volume_renderings_are_integer_and_exact():
    approved = renderings(3_200_000.0, KIND_VOLUME)
    assert "3200000" in approved
    assert "3200001" not in approved and "3199999" not in approved


def test_indicator_sign_is_preserved():
    assert "1.42" not in renderings(-1.42, KIND_INDICATOR)
    assert "-1.42" in renderings(-1.42, KIND_INDICATOR)


def test_canonical_key_strips_only_presentation():
    assert canonical_key("+1,234.50") == "1234.50"
    assert canonical_key("1,234.50") == "1234.50"
    assert canonical_key("-1.42") == "-1.42"
    assert canonical_key("1.840") != canonical_key("1.84")


def test_normalize_text_maps_arabic_numerals_and_separators():
    assert normalize_text(to_arabic("1,234.56")) == "1,234.56"
    assert normalize_text("15.37٪") == "15.37%"
