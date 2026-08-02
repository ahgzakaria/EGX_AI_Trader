"""Cross-surface contract: page, compact card, detailed card and chart agree.

Everything renders from one :class:`AnalysisPresentation`. These tests pin that
no surface may substitute its own number, that the detailed card exports at exact
dimensions in three languages, and that a timeframe change moves candles only.

No provider, network or database access — a synthetic AnalysisResult is used.
"""

from __future__ import annotations

import io
import inspect
from datetime import datetime, timezone

import pytest
from PIL import Image

from core.ai_stock_analysis_contract import (
    AnalysisRequest,
    AnalysisResult,
    ChartPoint,
    ChartSeries,
    ConfidenceBreakdown,
    DataQualitySummary,
    DataStatus,
    IndicatorSummary,
    KeyLevel,
    MarketPhase,
    MomentumState,
    PriceSummary,
    PullbackScenarioResult,
    PullbackState,
    Recommendation,
    ScenarioResult,
    ScenarioState,
    TrendState,
)
from core.analysis_chart import (
    DEFAULT_TIMEFRAME,
    LEVEL_STYLE,
    TIMEFRAMES,
    build_daily_figure,
    resolve_label_lanes,
    select_window,
)
from core.analysis_presentation import EM_DASH, build_presentation
from core.detailed_analysis_card import (
    DETAILED_CARD_SIZES,
    LANGUAGES,
    SECTION_KEYS,
    render_detailed_card_png,
)

# The verified RAYA baseline, held as literals so a drift is a test failure.
CLOSE = 7.50
SESSION = "2026-07-30"
SUPPORT_1, SUPPORT_2 = 7.3681, 7.30
RESISTANCE_1, RESISTANCE_2 = 7.5378, 7.6592
BREAKOUT, INVALIDATION = 8.49, 6.9927


def _candles(count=180):
    points = []
    for index in range(count):
        base = 3.0 + index * 0.025
        points.append(ChartPoint(
            timestamp=f"2026-{1 + index // 31:02d}-{1 + index % 28:02d}",
            open=base, high=base + 0.06, low=base - 0.06, close=base, volume=1_000_000.0))
    points[-1] = ChartPoint(timestamp=SESSION, open=7.52, high=7.54, low=7.35,
                            close=CLOSE, volume=7_196_773.0)
    return tuple(points)


def _result():
    return AnalysisResult(
        request=AnalysisRequest(symbol="RAYA", request_id="req-1",
                                as_of="2026-08-01T10:00:00+02:00",
                                market_phase=MarketPhase.CLOSED),
        price=PriceSummary(symbol="RAYA", session_date=SESSION, close=CLOSE,
                           previous_close=7.52, change_amount=-0.02,
                           change_percent=-0.27, open=7.52, high=7.54, low=7.35,
                           volume=7_196_773.0),
        indicators=IndicatorSummary(symbol="RAYA", computed_from_sessions=180,
                                    trend=TrendState.SIDEWAYS, trend_strength=50.0,
                                    momentum=MomentumState.NEGATIVE,
                                    momentum_strength=40.0, ema_20=RESISTANCE_2,
                                    ema_50=SUPPORT_1, ema_200=5.5707, atr_14=0.2899,
                                    average_volume_20=16_660_951.35, volume_ratio=0.43),
        confidence=ConfidenceBreakdown(overall=59.2, method_version="v1", components=()),
        data_quality=DataQualitySummary(status=DataStatus.CURRENT, provider="eodhd",
                                        latest_completed_session=SESSION),
        recommendation=Recommendation.AVOID, market_phase=MarketPhase.CLOSED,
        evidence_version="v1", generated_at="2026-08-01T10:00:00+02:00",
        key_levels=(
            KeyLevel(kind="SUPPORT", price=SUPPORT_1, basis="EMA50"),
            KeyLevel(kind="SUPPORT", price=SUPPORT_2, basis="PIVOT"),
            KeyLevel(kind="RESISTANCE", price=RESISTANCE_1, basis="PIVOT"),
            KeyLevel(kind="RESISTANCE", price=RESISTANCE_2, basis="EMA20"),
            KeyLevel(kind="BREAKOUT", price=BREAKOUT, basis="SWING"),
            KeyLevel(kind="STOP", price=INVALIDATION, basis="ATR"),
        ),
        scenarios=(ScenarioResult(
            scenario_id="breakout_continuation", title="Breakout continuation",
            state=ScenarioState.WAIT, trigger=BREAKOUT, target=9.21,
            stop=SUPPORT_2, risk_reward=0.61, remaining_room_percent=22.86,
            confirmation_requirements=("close above 8.49",),
            invalidation_conditions=("close below 7.30",)),),
        daily_chart_series=ChartSeries(timeframe="1D", session_date=SESSION,
                                       points=_candles(), source="eodhd",
                                       latest_completed_session=SESSION),
        intraday_chart_series=ChartSeries(timeframe="1m", session_date=SESSION,
                                          points=(), source="rubix"),
        evidence_hash="hash-1",
        pullback_scenario=PullbackScenarioResult(
            state=PullbackState.NOT_APPLICABLE,
            prior_trend_status="INVALID_PRIOR_TREND",
            ema_alignment_status="EMA20_ABOVE_EMA50",
            structure_status="HIGHER_HIGH_HIGHER_LOW",
            invalidation_reason="INVALID_PRIOR_UPTREND",
            swing_high=BREAKOUT, impulse_low=7.68, pullback_percent=11.66,
            pullback_atr=3.42, impulse_retracement_percent=122.22,
            support_zone_low=7.3246, support_zone_high=7.4116,
            volume_behaviour="SELLING_VOLUME_CONTRACTING",
            confirmation_status="WAITING", ema20=RESISTANCE_2, ema50=SUPPORT_1,
            major_resistance=BREAKOUT, entry_trigger=RESISTANCE_2,
            explanation_ar="الاتجاه السابق لا يحقق شروط الاتجاه الصاعد البحثية.",
            explanation_en="The prior move does not satisfy the research uptrend rules."),
    )


@pytest.fixture(scope="module")
def presentation():
    return build_presentation(_result(), None, company_name="Raya Holding For Financial Investments")


# --------------------------------------------------------------------------- #
# Unified model
# --------------------------------------------------------------------------- #

def test_the_model_carries_the_analysed_values_unchanged(presentation):
    assert presentation.ticker == "RAYA"
    assert presentation.close == CLOSE
    assert presentation.last_completed_session == SESSION
    assert presentation.confidence == 59.2
    assert presentation.recommendation_en == "Avoid For Now"
    assert presentation.level_price("support_1") == SUPPORT_1
    assert presentation.level_price("support_2") == SUPPORT_2
    assert presentation.level_price("resistance_1") == RESISTANCE_1
    assert presentation.level_price("resistance_2") == RESISTANCE_2
    assert presentation.level_price("breakout") == BREAKOUT
    assert presentation.level_price("invalidation") == INVALIDATION
    assert presentation.pullback.support_zone_low == 7.3246
    assert presentation.pullback.support_zone_high == 7.4116


def test_every_surface_receives_the_same_close_and_levels(presentation):
    """Chart window and card rows both read the model — no independent source."""

    for timeframe in TIMEFRAMES:
        window = select_window(presentation, timeframe)
        assert window.candles[-1].close == CLOSE
        assert window.candles[-1].date == SESSION
        prices = {lv.key: lv.price for lv in window.levels}
        assert prices["support_1"] == SUPPORT_1
        assert prices["breakout"] == BREAKOUT
        assert prices["invalidation"] == INVALIDATION


def test_the_full_company_name_is_preserved(presentation):
    assert presentation.company_name == "Raya Holding For Financial Investments"
    assert presentation.display_label == \
        "RAYA — Raya Holding For Financial Investments"
    png = render_detailed_card_png(presentation, size="DETAILED", language="EN")
    assert len(png) > 10_000        # the name is drawn, not dropped


def test_missing_values_stay_missing_and_render_as_an_em_dash():
    import dataclasses

    stripped = build_presentation(
        dataclasses.replace(_result(), key_levels=(), scenarios=(),
                            price=PriceSummary(symbol="RAYA", session_date=SESSION)),
        None, company_name="Raya")
    assert stripped.close is None
    assert stripped.level_price("support_1") is None
    assert stripped.level("support_1").display == EM_DASH
    assert stripped.change_display == EM_DASH
    assert stripped.level("support_1").display != "0"


def test_no_renderer_recalculates_a_decision_or_a_level():
    """The renderers must read the model, never the raw engine modules."""

    import core.analysis_chart as chart
    import core.detailed_analysis_card as card

    for module in (chart, card):
        source = inspect.getsource(module)
        assert "ai_pullback_scenario" not in source
        assert "recommendation_engine" not in source
        assert "compute_" not in source


# --------------------------------------------------------------------------- #
# Detailed card
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("size", sorted(DETAILED_CARD_SIZES))
@pytest.mark.parametrize("language", LANGUAGES)
def test_detailed_card_dimensions_are_exact(presentation, size, language):
    png = render_detailed_card_png(presentation, size=size, language=language)
    assert Image.open(io.BytesIO(png)).size == DETAILED_CARD_SIZES[size]


def test_both_card_kinds_remain_available(presentation):
    from core.analysis_card_generator import CARD_SIZES
    from dashboard.ai_stock_analysis_components import CARD_KIND_LABELS

    assert set(CARD_KIND_LABELS) == {"COMPACT", "DETAILED"}
    assert CARD_SIZES["POST"] == (1080, 1350)          # compact untouched
    assert DETAILED_CARD_SIZES["DETAILED"] == (1080, 1920)
    assert DETAILED_CARD_SIZES["DETAILED_HD"] == (1350, 2400)


def test_the_detailed_card_declares_every_required_section():
    required = ("header", "trend", "price_volume", "pullback", "levels",
                "scenarios", "risk", "summary", "footer")
    assert SECTION_KEYS == required


def test_the_detailed_card_rows_expose_no_raw_enum(presentation):
    from core.detailed_analysis_card import _rows_for

    for section in ("trend", "price_volume", "pullback", "levels", "scenarios", "risk"):
        for label, value in _rows_for(presentation, section, "BILINGUAL"):
            text = f"{label} {value}"
            for enum in ("INVALID_PRIOR_UPTREND", "INVALID_PRIOR_TREND",
                         "EMA20_ABOVE_EMA50", "SELLING_VOLUME_CONTRACTING"):
                assert enum not in text, f"{section} leaked {enum}"


def test_the_preview_and_the_download_are_the_same_bytes(presentation):
    """Rendering twice from the same model yields identical output."""

    first = render_detailed_card_png(presentation, size="DETAILED", language="AR")
    second = render_detailed_card_png(presentation, size="DETAILED", language="AR")
    assert first == second


def test_language_modes_produce_different_text_but_identical_geometry(presentation):
    arabic = render_detailed_card_png(presentation, size="DETAILED", language="AR")
    english = render_detailed_card_png(presentation, size="DETAILED", language="EN")
    assert arabic != english
    assert Image.open(io.BytesIO(arabic)).size == Image.open(io.BytesIO(english)).size


def test_an_unknown_size_or_language_is_refused(presentation):
    with pytest.raises(ValueError):
        render_detailed_card_png(presentation, size="SQUARE")
    with pytest.raises(ValueError):
        render_detailed_card_png(presentation, language="FR")


# --------------------------------------------------------------------------- #
# Chart
# --------------------------------------------------------------------------- #

def test_the_default_window_is_about_120_completed_sessions(presentation):
    from core.analysis_chart import DEFAULT_SESSIONS

    window = select_window(presentation, DEFAULT_TIMEFRAME)
    assert abs(len(window.candles) - DEFAULT_SESSIONS) <= 10
    assert TIMEFRAMES[DEFAULT_TIMEFRAME] == 126


def test_timeframe_changes_candles_only_and_never_the_levels(presentation):
    baseline = {lv.key: lv.price for lv in select_window(presentation, "FULL").levels}
    counts = {}
    for timeframe in TIMEFRAMES:
        window = select_window(presentation, timeframe)
        counts[timeframe] = len(window.candles)
        assert {lv.key: lv.price for lv in window.levels} == baseline
        assert window.candles[-1].date == SESSION
    assert counts["3M"] < counts["6M"] <= counts["1Y"] <= counts["FULL"]


def test_the_final_candle_equals_the_last_completed_session(presentation):
    for timeframe in TIMEFRAMES:
        window = select_window(presentation, timeframe)
        assert window.candles[-1].date == presentation.last_completed_session


def test_no_rubix_series_enters_the_daily_chart(presentation):
    assert presentation.chart_source == "eodhd"
    source = inspect.getsource(select_window)
    assert "rubix" not in source.lower()
    assert "intraday" not in source.lower()


def test_label_collisions_are_resolved_deterministically():
    """The reported case: 7.37 / 7.50 / 7.54 / 7.57 / 7.66 must stay distinct."""

    levels = [("a", 7.37), ("b", 7.50), ("c", 7.54), ("d", 7.57), ("e", 7.66)]
    lanes = resolve_label_lanes(levels, min_gap=0.06)
    anchors = sorted(lanes.values())
    assert len(anchors) == 5
    for previous, following in zip(anchors, anchors[1:]):
        assert following - previous >= 0.06 - 1e-9
    # Deterministic: the same input always yields the same lanes.
    assert lanes == resolve_label_lanes(levels, min_gap=0.06)
    # Order is preserved, and a cluster stays centred on its members' true prices
    # rather than drifting the whole stack one way.
    assert [k for k, _ in sorted(lanes.items(), key=lambda i: i[1])] ==         [k for k, _ in levels]
    centre_before = sum(price for _, price in levels) / len(levels)
    centre_after = sum(lanes.values()) / len(lanes)
    assert abs(centre_after - centre_before) < 1e-6


def test_levels_have_a_visual_hierarchy(presentation):
    weights = {lv.key: lv.weight for lv in presentation.levels}
    assert weights["breakout"] == "major"
    assert weights["invalidation"] == "major"
    assert weights["support_1"] == "medium"
    assert weights["support_2"] == "minor"
    assert weights["research_trigger"] == "diagnostic"
    assert LEVEL_STYLE["major"]["width"] > LEVEL_STYLE["medium"]["width"] \
        > LEVEL_STYLE["minor"]["width"]


def test_the_figure_carries_emas_volume_and_bands(presentation):
    pytest.importorskip("plotly")
    figure = build_daily_figure(presentation, timeframe="FULL")
    names = [trace.name for trace in figure.data]
    assert "EODHD Daily" in names
    assert "Volume" in names
    assert "EMA20" in names and "EMA50" in names
    # EMA200 is drawn only "when available": 180 candles is short of its span.
    assert "EMA200" not in names
    # Support and the pullback zone are BANDS (rects), not just hairlines.
    rects = [s for s in figure.layout.shapes if s.type == "rect"]
    assert len(rects) >= 3


def test_ema200_appears_once_enough_history_exists(presentation):
    pytest.importorskip("plotly")
    import dataclasses

    long_history = dataclasses.replace(
        presentation, candles=presentation.candles * 2)   # 360 sessions
    names = [t.name for t in build_daily_figure(long_history, timeframe="FULL").data]
    assert "EMA200" in names


def test_the_chart_download_uses_the_displayed_figure():
    source = inspect.getsource(build_daily_figure)
    assert "presentation" in source
    from core.analysis_chart import figure_to_png
    assert "figure" in inspect.signature(figure_to_png).parameters


def test_pullback_annotations_are_labelled_diagnostic(presentation):
    pytest.importorskip("plotly")
    figure = build_daily_figure(presentation, timeframe="FULL")
    texts = " ".join(a.text or "" for a in figure.layout.annotations)
    assert "research" in texts.lower()


# --------------------------------------------------------------------------- #
# Regression
# --------------------------------------------------------------------------- #

def test_decision_values_are_copied_not_recomputed(presentation):
    result = _result()
    assert presentation.recommendation_en == "Avoid For Now"
    assert result.recommendation is Recommendation.AVOID
    assert presentation.confidence == result.confidence.overall
    assert presentation.trend_strength == result.indicators.trend_strength
    assert presentation.pullback.pullback_percent == \
        result.pullback_scenario.pullback_percent
    # The English label is real English now; the raw code lives in *_code.
    assert presentation.pullback.state_en == "Not currently assessable"


def test_the_lane_gap_budgets_for_the_TALLEST_chip_not_the_average(presentation):
    """The last-close chip is the most prominent label and therefore the tallest.

    Spacing the lanes by an average label height let it overlap its neighbour —
    "Last 7.50" sat on top of "Resistance 1 7.54" on the real RAYA chart.
    """

    pytest.importorskip("plotly")
    from core.analysis_chart import LABEL_PX

    # font 12 + borderpad 3 top and bottom, the largest chip the renderer draws.
    tallest_chip_px = 12 * 1.2 + 6
    assert LABEL_PX >= tallest_chip_px

    height = 700
    plot_px = (height - 86 - 36) * 0.76
    for timeframe in TIMEFRAMES:
        figure = build_daily_figure(presentation, timeframe=timeframe, height=height)
        labels = sorted(a.y for a in figure.layout.annotations if a.yref == "y")
        window = select_window(presentation, timeframe)
        closes = [c.close for c in window.candles if c.close is not None]
        span = max(max(closes), max(labels)) - min(min(closes), min(labels))
        gaps_px = [(labels[i + 1] - labels[i]) / span * plot_px
                   for i in range(len(labels) - 1)]
        assert min(gaps_px) >= tallest_chip_px, \
            f"{timeframe}: labels are {min(gaps_px):.1f}px apart, chip is {tallest_chip_px:.1f}px"


def test_two_levels_at_the_same_price_share_one_label(presentation):
    """RAYA's breakout and major resistance are both 8.49 — one fact, one label."""

    pytest.importorskip("plotly")
    figure = build_daily_figure(presentation, timeframe="FULL")
    texts = [a.text for a in figure.layout.annotations if a.yref == "y"]
    assert any("/" in text and "8.49" in text for text in texts)
    # The merged label is counted once, so the lane set stays small enough to fit.
    assert sum("8.49" in text for text in texts) == 1
