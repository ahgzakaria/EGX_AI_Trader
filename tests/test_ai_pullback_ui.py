"""Presentation-only checks for the AI Analysis Pullback card."""

from pathlib import Path
from types import SimpleNamespace

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
    assert view["state_ar"] == "وصل إلى منطقة مهمة وينتظر تأكيد الارتداد"
    assert view["correction"] == "7.40% / 1.30 ATR"
    assert view["support_zone"] == "7.30 – 7.37"
    assert view["support_reached"] == "نعم · Yes"
    assert view["confluence"] == "Horizontal Support + EMA50"
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
    assert view["state_en"] == "Research confirmation only"
    assert view["state_ar"] == "ظهر تأكيد ارتداد بحثي فقط"
    assert view["tone"] == "amber"


def test_page_keeps_breakout_breakdown_and_adds_separate_pullback_section():
    import dashboard.ai_stock_analysis as page

    source = Path(page.__file__).read_text(encoding="utf-8")
    assert "_scenario_section(result)" in source
    assert "_pullback_section(result)" in source
    render_flow = source[source.index("def show_ai_stock_analysis"):]
    assert render_flow.index("_pullback_section(result)") < render_flow.index(
        "_scenario_section(result)")
    assert "تحليل جودة التصحيح" in source
    assert "Pullback Health Analysis" in source
    assert "Research Only — غير معتمد كإشارة دخول" in source
    assert "does not represent a BUY signal" in source
    assert "هذا التحليل يصف جودة التصحيح ولا يمثل إشارة شراء" in source
    assert "Research simulation levels" in source
    assert "expanded=False" in source
    assert "pullback_live_confirmation" not in source


def test_main_pullback_state_is_visible_and_only_simulation_levels_are_collapsed():
    import dashboard.ai_stock_analysis as page

    source = Path(page.__file__).read_text(encoding="utf-8")
    section = source[source.index("def _pullback_section"):source.index(
        "def _regenerate_narrative_only")]
    assert section.index("state_badge") < section.index("with st.expander")
    assert "مستويات المحاكاة البحثية · Research simulation levels" in section
    assert "expanded=False" in section
    assert "Research Trigger" in section
    assert "Research Structural Stop" in section
    assert "Research Target 1" in section
    assert "Research Target 2" in section
    assert "Research Major Resistance" in section
    assert "الحالة الحالية · Current State" in section
    assert "st.button" not in section


def test_insufficient_history_keeps_exact_reason_and_missing_values():
    scenario = PullbackScenarioResult(
        state=PullbackState.NOT_APPLICABLE,
        invalidation_reason="INSUFFICIENT_COMPLETED_DAILY_HISTORY",
        missing_measurements=("confirmed_swing_high", "impulse_low"),
    )
    view = pullback_scenario_view(scenario)
    # The user-facing sentence is the LOCALIZED reason only; the internal enum
    # moved to invalidation_code for the collapsed diagnostics section.
    assert view["invalidation"] == "التاريخ اليومي المكتمل غير كافٍ"
    assert "INSUFFICIENT_COMPLETED_DAILY_HISTORY" not in view["invalidation"]
    assert view["invalidation_code"] == "INSUFFICIENT_COMPLETED_DAILY_HISTORY"
    assert view["swing_high"] == EM_DASH
    assert view["impulse_low"] == EM_DASH
    assert view["pullback_percent"] == EM_DASH


def test_local_pullback_error_is_a_visible_local_diagnostic():
    view = pullback_scenario_view(PullbackScenarioResult(
        state=PullbackState.NOT_APPLICABLE,
        invalidation_reason="PULLBACK_DIAGNOSTIC_ERROR:RuntimeError",
        missing_measurements=("pullback_calculation_failed",),
    ))
    assert view["calculation_error"] is True
    # Still surfaced as a local diagnostic, but the raw exception token is kept
    # out of the reading flow and preserved in the diagnostic code field.
    assert "تعذر حساب تحليل جودة التصحيح" in view["invalidation"]
    assert "RuntimeError" not in view["invalidation"]
    assert view["invalidation_code"] == "PULLBACK_DIAGNOSTIC_ERROR:RuntimeError"


def test_selecting_another_stock_discards_the_previous_bundle(monkeypatch):
    import dashboard.ai_stock_analysis as page
    from dashboard.ai_stock_analysis_components import fixture_analysis

    fake = SimpleNamespace(session_state={
        page.STATE_BUNDLE: fixture_analysis("COMI"),
        page.STATE_SYMBOL: "COMI",
        page.STATE_CARD: b"old-card",
        page.STATE_CARD_KEY: "old-card-key",
    })
    monkeypatch.setattr(page, "st", fake)
    page._discard_stale_analysis("HRHO")

    assert fake.session_state[page.STATE_BUNDLE] is None
    assert fake.session_state[page.STATE_SYMBOL] is None
    assert fake.session_state[page.STATE_CARD] is None
    assert fake.session_state[page.STATE_CARD_KEY] is None
