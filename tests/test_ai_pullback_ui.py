"""Presentation-only checks for the AI Analysis Pullback card."""

from pathlib import Path

from core.ai_stock_analysis_contract import (
    PullbackScenarioResult,
    PullbackState,
)
from dashboard.ai_stock_analysis_components import EM_DASH, pullback_scenario_view


def test_pullback_card_maps_only_typed_values():
    scenario = PullbackScenarioResult(
        state=PullbackState.WAIT_REVERSAL_CONFIRMATION,
        research_score=72.0,
        prior_trend_status="VALID_UPTREND",
        historical_data_cutoff="2026-07-30",
        pullback_percent=7.4,
        pullback_atr=1.3,
        impulse_retracement_percent=50.0,
        correction_bars=6,
        support_zone_low=7.30,
        support_zone_high=7.37,
        support_reached=True,
        support_confluence=("HORIZONTAL_SUPPORT", "EMA50"),
        volume_behaviour="SELLING_VOLUME_CONTRACTING",
        confirmation_status="WAITING",
        entry_trigger=7.54,
        stop_loss=7.28,
        target_1=7.66,
        target_2=8.10,
        major_resistance=8.49,
        reward_risk=2.1,
        invalidation_reason="REVERSAL_CONFIRMATION_REQUIRED",
    )
    view = pullback_scenario_view(scenario)
    assert view["state_ar"] == "انتظار تأكيد الارتداد"
    assert view["correction"] == "7.40% / 1.30 ATR"
    assert view["support_zone"] == "7.30 – 7.37"
    assert view["support_reached"] == "نعم · Yes"
    assert view["confluence"] == "HORIZONTAL_SUPPORT + EMA50"
    assert view["trigger"] == "7.54"
    assert view["risk_reward"] == "2.10×"


def test_missing_pullback_measurements_remain_dashes():
    view = pullback_scenario_view(PullbackScenarioResult(
        state=PullbackState.NOT_APPLICABLE,
        missing_measurements=("confirmed_swing_high",)))
    assert view["correction"] == EM_DASH
    assert view["support_zone"] == EM_DASH
    assert view["trigger"] == EM_DASH
    assert view["missing"] == ("confirmed_swing_high",)


def test_internal_confirmation_is_presented_as_amber_research_only():
    view = pullback_scenario_view(PullbackScenarioResult(
        state=PullbackState.CONFIRMED_PULLBACK_ENTRY))
    assert view["state_en"] == "RESEARCH_CONFIRMATION_ONLY"
    assert view["state_ar"] == "تأكيد بحثي فقط — غير صالح للتنفيذ"
    assert view["tone"] == "amber"


def test_page_keeps_breakout_breakdown_and_adds_separate_pullback_section():
    import dashboard.ai_stock_analysis as page

    source = Path(page.__file__).read_text(encoding="utf-8")
    assert "_scenario_section(result)" in source
    assert "_pullback_section(result)" in source
    assert source.index("_scenario_section(result)") < source.index("_pullback_section(result)")
    assert "تحليل جودة التصحيح" in source
    assert "Pullback Health Analysis" in source
    assert "Research Only — غير معتمد كإشارة دخول" in source
    assert "does not represent a BUY signal" in source
    assert "هذا التحليل يصف جودة التصحيح ولا يمثل إشارة شراء" in source
    assert "Research simulation levels" in source
    assert "expanded=False" in source
    assert "pullback_live_confirmation" not in source
