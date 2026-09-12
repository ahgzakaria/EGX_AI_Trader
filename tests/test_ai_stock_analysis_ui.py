"""UI contract tests for the AI Stock Analysis page.

These tests protect the page's hard guarantees: nothing is analysed before the button is
pressed, exactly one symbol is ever requested, no provider or Yahoo path is touched, every
rendered number comes from a typed evidence field, and missing values never render as zero.
"""

from __future__ import annotations

import datetime as dt
import types
from dataclasses import replace

import pytest

from core.ai_stock_analysis_contract import (
    AnalysisResult,
    ConfidenceBreakdown,
    ConfidenceComponent,
    DataStatus,
    MarketPhase,
    PriceSummary,
    Recommendation,
    ScenarioState,
)
from dashboard.ai_stock_analysis_components import (
    ALLOWED_RECOMMENDATION_LABELS_AR,
    EM_DASH,
    RECOMMENDATION_LABELS,
    build_card_chart,
    build_card_payload,
    confidence_rows,
    data_mode,
    data_quality_warnings,
    fixture_analysis,
    include_live_in_session_range,
    indicator_groups,
    key_level_rows,
    load_fixture,
    market_phase_labels,
    narrative_source,
    normalize_symbol,
    price_summary_rows,
    provenance_rows,
    scenario_view,
    selected_levels,
    volume_analysis_available,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def bundle():
    return fixture_analysis("COMI")


@pytest.fixture
def result(bundle):
    return bundle.result


def _flat_indicator_rows(result):
    return [row for _, _, rows in indicator_groups(result.indicators) for row in rows]


# --------------------------------------------------------------------------- #
# Fixture / contract loading
# --------------------------------------------------------------------------- #

def test_corrected_fixture_loads_into_typed_contract_objects(bundle):
    result = bundle.result
    assert isinstance(result, AnalysisResult)
    assert result.request.symbol == "COMI"
    assert result.recommendation is Recommendation.NEAR_READY
    assert result.market_phase is MarketPhase.CONTINUOUS
    assert result.indicators.sma_20 == 89.7 and result.indicators.ema_20 == 90.2
    assert result.scenarios[0].state is ScenarioState.NEAR_READY
    assert result.data_quality.status is DataStatus.CURRENT


def test_fixture_matches_the_published_contract_version():
    raw = load_fixture()
    from core.ai_stock_analysis_contract import CONTRACT_VERSION
    assert raw["contract_version"] == CONTRACT_VERSION


def test_fixture_analysis_touches_only_the_requested_symbol():
    bundle = fixture_analysis("hrho")
    assert bundle.result.request.symbol == "HRHO"
    assert bundle.result.price.symbol == "HRHO"
    assert bundle.result.indicators.symbol == "HRHO"
    assert bundle.narrative.symbol == "HRHO"


# --------------------------------------------------------------------------- #
# No analysis before the button / one symbol only / no universe scan
# --------------------------------------------------------------------------- #

def test_no_analysis_runs_before_the_button_is_pressed(monkeypatch):
    """Rendering the page with the button unpressed must not invoke the runner."""
    import dashboard.ai_stock_analysis as page

    calls = []

    def _runner(symbol):
        calls.append(symbol)
        return fixture_analysis(symbol)

    fake = _FakeStreamlit(button_returns=False)
    monkeypatch.setattr(page, "st", fake)
    monkeypatch.setattr(page, "apply_global_style", lambda: None)
    monkeypatch.setattr(page, "page_header", lambda *a, **k: None)
    monkeypatch.setattr(page, "section_header", lambda *a, **k: None)
    monkeypatch.setattr(page, "empty_state", lambda *a, **k: None)
    monkeypatch.setattr(page, "status_bar", lambda *a, **k: None)
    monkeypatch.setattr(page, "metric_card", lambda *a, **k: None)

    page.show_ai_stock_analysis(runner=_runner)
    assert calls == [], "the runner must not be called until Analyze Stock is pressed"


def test_analysis_requests_exactly_one_symbol():
    import dashboard.ai_stock_analysis as page
    seen = []
    page.run_analysis(" comi ", runner=lambda symbol: seen.append(symbol) or fixture_analysis(symbol))
    assert seen == ["COMI"]


@pytest.mark.parametrize("bad", [["COMI", "HRHO"], ("COMI",), {"COMI"}, {"a": 1}])
def test_a_collection_of_symbols_is_rejected(bad):
    with pytest.raises(ValueError):
        normalize_symbol(bad)


@pytest.mark.parametrize("bad", ["", "   ", None, 5])
def test_an_empty_or_non_string_symbol_is_rejected(bad):
    with pytest.raises(ValueError):
        normalize_symbol(bad)


def test_no_full_universe_scan_is_reachable_from_the_page():
    """No universe/scanner entry point is imported or called anywhere in the UI."""
    names = _referenced_names(_page_source()) | _referenced_names(_components_source())
    for forbidden in ("ExpectedRangeScanner", "scan", "scan_universe", "get_universe",
                      "universe", "full_universe", "load_universe", "all_symbols"):
        assert forbidden not in names, f"UI references a universe path: {forbidden}"


def test_no_provider_call_from_the_ui():
    names = _referenced_names(_page_source()) | _referenced_names(_components_source())
    for forbidden in ("RubixSqliteProvider", "RubixSQLiteProvider", "requests", "urllib",
                      "httpx", "socket",
                      "data_provider", "research_router", "get_current_research_history",
                      "quote_overlay", "yfinance"):
        assert forbidden not in names, f"UI references a provider path: {forbidden}"


def test_no_yahoo_call_or_comparison_anywhere_in_the_ui():
    names = (_referenced_names(_page_source()) | _referenced_names(_components_source())
             | _referenced_names(_card_source()))
    for forbidden in ("yfinance", "yahoo", "Ticker", "download"):
        assert forbidden not in names
    # The only permitted Yahoo mentions are the honest provenance flag and the frozen-seed
    # label — both read-only renderings of what the evidence engine reported.
    assert "yahoo_network_used" in _components_source()
    assert "Frozen historical bootstrap seed" in _components_source()


def test_fixture_analysis_opens_no_socket(monkeypatch):
    import socket

    def _boom(*args, **kwargs):
        raise AssertionError("the UI opened a network socket")

    monkeypatch.setattr(socket.socket, "connect", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    bundle = fixture_analysis("COMI")
    assert bundle.result.request.symbol == "COMI"


# --------------------------------------------------------------------------- #
# Price summary
# --------------------------------------------------------------------------- #

def test_price_summary_renders_every_required_field(result):
    labels = {label_en for _, label_en, _, _, _ in price_summary_rows(result)}
    for required in ("Change", "Change %", "Open", "High", "Low", "Previous Close",
                     "Volume", "Turnover", "Bid", "Ask", "Spread"):
        assert required in labels
    assert any(label.startswith("Last") or label.startswith("Close") for label in labels)


def test_missing_price_values_render_as_an_em_dash_never_zero(result):
    blank = PriceSummary(symbol="COMI")
    stripped = replace(result, price=blank)
    values = [value for _, _, value, _, _ in price_summary_rows(stripped)]
    assert all(value == EM_DASH for value in values), values
    assert "0.00" not in values


def test_a_genuine_zero_is_not_confused_with_a_missing_value(result):
    zeroed = replace(result.price, volume=0.0, change_amount=0.0)
    rows = dict((label_en, value) for _, label_en, value, _, _ in
                price_summary_rows(replace(result, price=zeroed)))
    assert rows["Volume"] != EM_DASH
    assert rows["Change"] == "+0.00"


# --------------------------------------------------------------------------- #
# Data mode / market phase
# --------------------------------------------------------------------------- #

def test_the_four_data_modes_are_distinguishable(result):
    live = result
    assert data_mode(live)[1] == "Live Quote"

    closed = replace(result, market_phase=MarketPhase.CLOSED,
                     request=replace(result.request, market_phase=MarketPhase.CLOSED))
    assert data_mode(closed)[1] == "Last Completed Session"

    cached = replace(result, data_quality=replace(result.data_quality,
                                                  status=DataStatus.CACHE_MODE))
    assert data_mode(cached)[1] == "Cache Mode"

    dark = replace(result, data_quality=replace(result.data_quality, live_available=False))
    assert data_mode(dark)[1] == "Live Data Unavailable"


@pytest.mark.parametrize("phase,english", [
    (MarketPhase.PRE_SESSION, "Pre-session"),
    (MarketPhase.CONTINUOUS, "Continuous Session"),
    (MarketPhase.CLOSING_AUCTION, "Closing Auction"),
    (MarketPhase.CLOSED, "Closed"),
    (MarketPhase.HOLIDAY, "Holiday"),
    (MarketPhase.WEEKEND, "Weekend"),
])
def test_every_market_phase_has_a_distinct_label(phase, english):
    assert market_phase_labels(phase)[1] == english


def test_market_phase_at_1425_is_closed():
    """14:25 belongs to CLOSED, never to CONTINUOUS and never to the auction."""
    from core.ai_stock_analysis_service import resolve_market_phase
    trading_day = _a_trading_day()
    assert resolve_market_phase(_at(trading_day, 14, 24, 59)) is MarketPhase.CLOSING_AUCTION
    assert resolve_market_phase(_at(trading_day, 14, 25, 0)) is MarketPhase.CLOSED
    assert resolve_market_phase(_at(trading_day, 14, 29, 59)) is MarketPhase.CLOSED


def test_continuous_and_auction_boundaries():
    from core.ai_stock_analysis_service import resolve_market_phase
    day = _a_trading_day()
    assert resolve_market_phase(_at(day, 9, 59, 59)) is MarketPhase.PRE_SESSION
    assert resolve_market_phase(_at(day, 10, 0, 0)) is MarketPhase.CONTINUOUS
    assert resolve_market_phase(_at(day, 14, 14, 59)) is MarketPhase.CONTINUOUS
    assert resolve_market_phase(_at(day, 14, 15, 0)) is MarketPhase.CLOSING_AUCTION


def test_auction_data_is_kept_out_of_the_continuous_session_range(result):
    assert include_live_in_session_range(MarketPhase.CONTINUOUS) is True
    assert include_live_in_session_range(MarketPhase.CLOSING_AUCTION) is False

    auction = replace(result, market_phase=MarketPhase.CLOSING_AUCTION,
                      request=replace(result.request, market_phase=MarketPhase.CLOSING_AUCTION))
    chart = build_card_chart(auction)
    assert chart.last is None, "an auction print must not be plotted inside the session range"
    assert chart.low == result.price.low and chart.high == result.price.high

    warnings = [english for _, _, english in data_quality_warnings(auction)]
    assert any("auction" in text.lower() for text in warnings)


# --------------------------------------------------------------------------- #
# Technical overview
# --------------------------------------------------------------------------- #

def test_sma_and_ema_render_in_separate_rows(result):
    groups = {title_en: rows for _, title_en, rows in indicator_groups(result.indicators)}
    sma_labels = [label for label, _, _ in groups["Simple Moving Averages"]]
    ema_labels = [label for label, _, _ in groups["Exponential Moving Averages"]]
    assert sma_labels == ["SMA 20", "SMA 50", "SMA 200"]
    assert ema_labels == ["EMA 20", "EMA 50", "EMA 200"]

    sma_values = [value for _, _, value in groups["Simple Moving Averages"]]
    ema_values = [value for _, _, value in groups["Exponential Moving Averages"]]
    assert sma_values == ["89.70", "86.20", "78.40"]
    assert ema_values == ["90.20", "87.10", "80.30"]
    assert sma_values != ema_values, "an EMA value must never occupy an SMA row"


def test_macd_fields_render_explicitly(result):
    momentum = dict((label_en, value) for _, title_en, rows in
                    indicator_groups(result.indicators) if title_en == "Momentum"
                    for _, label_en, value in rows)
    assert momentum["MACD"] == "1.420"
    assert momentum["MACD Signal"] == "1.050"
    assert momentum["MACD Histogram"] == "0.370"
    assert momentum["RSI 14"] == "61.5"


def test_volume_ratio_and_obv_are_suppressed_when_volume_is_unsafe(result):
    unsafe = replace(result, indicators=replace(result.indicators, volume_safe=False))
    assert volume_analysis_available(unsafe.indicators) is False
    labels = [label_en for _, label_en, _ in _flat_indicator_rows(unsafe)]
    assert "Volume Ratio" not in labels
    assert "OBV" not in labels
    assert "Average Volume 20" not in labels

    warnings = [english for _, _, english in data_quality_warnings(
        replace(unsafe, data_quality=replace(unsafe.data_quality,
                                             volume_safe_for_lookback=False)))]
    assert any("Volume Analysis Unavailable" in text for text in warnings)


def test_volume_group_is_present_when_volume_is_safe(result):
    """OBV's label now says "cumulative" -- the fact asserted here is that the
    row is present, not what it is called."""
    labels = [label_en for _, label_en, _ in _flat_indicator_rows(result)]
    assert "Volume Ratio" in labels
    assert any(label.startswith("OBV") for label in labels)


def test_missing_indicator_values_render_as_em_dash(result):
    from core.ai_stock_analysis_contract import IndicatorSummary
    blank = IndicatorSummary(symbol="COMI", computed_from_sessions=0)
    stripped = replace(result, indicators=blank)
    values = [value for _, _, value in _flat_indicator_rows(stripped)]
    assert all(value == EM_DASH for value in values), values


def test_trend_and_momentum_are_labels_not_numbers(result):
    from dashboard.ai_stock_analysis_components import momentum_reading, trend_reading
    trend_ar, trend_en, _, basis = trend_reading(result)
    assert trend_en == "Uptrend" and basis
    assert not any(char.isdigit() for char in trend_ar)

    momentum_ar, momentum_en, _, basis = momentum_reading(result)
    assert momentum_en == "Strong Positive" and basis
    assert not any(char.isdigit() for char in momentum_ar)

    from core.ai_stock_analysis_contract import IndicatorSummary
    blank = replace(result, indicators=IndicatorSummary(symbol="COMI",
                                                        computed_from_sessions=0))
    assert trend_reading(blank)[0] == EM_DASH
    assert momentum_reading(blank)[0] == EM_DASH


# --------------------------------------------------------------------------- #
# Key levels
# --------------------------------------------------------------------------- #

def test_key_levels_render_supplied_values_with_their_basis(result):
    rows = {row["key"]: row for row in key_level_rows(result)}
    assert set(rows) == {"support_1", "support_2", "resistance_1", "resistance_2",
                         "breakout", "invalidation"}
    assert rows["support_1"]["value"] == "89.70"
    assert rows["support_1"]["basis"] == "SMA20"
    assert rows["support_1"]["distance"] == "-2.90%"
    assert rows["resistance_1"]["value"] == "93.50"
    assert rows["invalidation"]["value"] == "88.60"
    assert rows["support_1"]["timeframe"] == "1D"
    assert rows["support_1"]["touches"] == "3"
    assert rows["support_1"]["last_touch_date"] == "2026-07-07"
    # A slot with no supplied level stays empty rather than borrowing another level.
    assert rows["support_2"]["present"] is False
    assert rows["support_2"]["value"] == EM_DASH


def test_the_card_and_the_page_agree_on_which_levels_were_selected(result):
    chosen = selected_levels(result)
    chart = build_card_chart(result)
    assert chart.support == chosen["support_1"]["price"]
    assert chart.resistance == chosen["resistance_1"]["price"]
    assert chart.stop == chosen["invalidation"]["price"]
    assert chart.trigger == chosen["breakout"]["price"]


# --------------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------------- #

def test_typed_scenario_fields_render(result):
    view = scenario_view(result.scenarios[0])
    assert view["trigger"] == "93.50"
    assert view["entry"] == "93.60 – 94.20"
    assert view["target"] == "97.80"
    assert view["stop"] == "91.00"
    assert view["remaining_room"] == "5.84%"
    assert view["risk_reward"] == "1.90×"
    assert view["confidence"] == "55 / 100"
    assert view["state_ar"] == "قريب من التفعيل"
    assert view["confirmations"] == ("close above 93.50", "volume >= average_volume_20")
    assert view["invalidations"] == ("daily close below 91.00",)


def test_missing_scenario_fields_render_as_em_dash():
    from core.ai_stock_analysis_contract import ScenarioResult
    bare = ScenarioResult(scenario_id="x", title="t", state=ScenarioState.WAIT)
    view = scenario_view(bare)
    for key in ("trigger", "entry", "target", "stop", "remaining_room", "risk_reward",
                "confidence"):
        assert view[key] == EM_DASH, key


def test_only_the_allowed_arabic_recommendation_labels_exist():
    assert ALLOWED_RECOMMENDATION_LABELS_AR == {
        "انتظار", "مراقبة", "قريب من التفعيل", "جاهز بشروط", "تجنب حاليًا",
        "البيانات غير كافية"}


def test_no_unconditional_buy_instruction_is_ever_rendered():
    labels = " ".join(f"{ar} {en}" for ar, en, _ in RECOMMENDATION_LABELS.values())
    for forbidden in ("BUY", "Buy Now", "اشتر", "شراء الآن"):
        assert forbidden not in labels
    source = _page_source() + _components_source()
    assert "اشترِ" not in source and "اشتر الآن" not in source


# --------------------------------------------------------------------------- #
# Narrative + confidence
# --------------------------------------------------------------------------- #

def test_ai_narrative_source_is_labelled(bundle):
    assert narrative_source(bundle.narrative)[1] == "AI Narrative"


def test_deterministic_fallback_is_labelled_as_such(bundle):
    from core.ai_analysis_narrative import FALLBACK_MODEL
    fallback = replace(bundle.narrative, model=FALLBACK_MODEL)
    arabic, english, _ = narrative_source(fallback)
    assert english == "Deterministic Fallback"
    assert arabic and arabic != english


def test_missing_narrative_is_labelled_unavailable(bundle):
    assert narrative_source(None)[1] == "AI Unavailable"
    assert narrative_source(replace(bundle.narrative, model=""))[1] == "AI Unavailable"
    assert narrative_source(replace(bundle.narrative, summary="  "))[1] == "AI Unavailable"


def test_confidence_renders_supplied_components_and_dashes_the_rest(result):
    rows = {row["label_en"]: row for row in confidence_rows(result.confidence)}
    assert rows["Trend"]["score"] == "72" and rows["Trend"]["supplied"] is True
    assert rows["Momentum"]["score"] == "49"
    assert rows["Liquidity"]["score"] == "65"
    for absent in ("Freshness", "Spread", "History", "Scenario Quality", "Target Room",
                   "Volume"):
        assert rows[absent]["score"] == EM_DASH
        assert rows[absent]["supplied"] is False


def test_confidence_overall_is_never_recomputed(result):
    """The page shows the engine's ``overall`` even when it disagrees with the weights."""
    skewed = ConfidenceBreakdown(
        overall=12.0, method_version="confidence@1.0.0",
        components=(ConfidenceComponent(name="trend", weight=1.0, score=99.0),))
    shown = replace(result, confidence=skewed)
    payload = build_card_payload(shown, None)
    assert payload.confidence_label == "12 / 100"


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #

def test_provenance_is_rendered_honestly(result):
    rows = dict((label_en, value) for _, label_en, value in provenance_rows(result))
    assert rows["Data Domain"] == "CURRENT_RESEARCH_V2"
    assert rows["Provider"] == "eodhd"
    assert rows["Live Provider"] == "rubix"
    assert rows["Yahoo Network Used"] == "لا / No"
    assert rows["Latest Completed Session"] == "2026-07-22"
    assert rows["Evidence Version"] == result.evidence_version


def test_yahoo_is_never_presented_as_a_provider(result):
    seeded = replace(result, data_quality=replace(result.data_quality,
                                                  yahoo_seed_present=True))
    rows = dict((label_en, value) for _, label_en, value in provenance_rows(seeded))
    assert rows["Provider"] == "eodhd"
    assert rows["Live Provider"] == "rubix"
    assert "Frozen historical bootstrap seed" in rows
    for label, value in rows.items():
        if label in ("Provider", "Live Provider"):
            assert "yahoo" not in str(value).lower()


def test_a_yahoo_network_read_is_surfaced_as_an_error(result):
    breached = replace(result, data_quality=replace(result.data_quality,
                                                    yahoo_network_used=True))
    warnings = data_quality_warnings(breached)
    assert any(severity == "error" and "Yahoo" in english
               for severity, _, english in warnings)


# --------------------------------------------------------------------------- #
# Streamlit render smoke
# --------------------------------------------------------------------------- #

def test_streamlit_render_smoke_before_and_after_analysis(monkeypatch):
    """The page renders end to end against a recording Streamlit stand-in."""
    import dashboard.ai_stock_analysis as page

    fake = _FakeStreamlit(button_returns=True)
    monkeypatch.setattr(page, "st", fake)
    for name in ("apply_global_style",):
        monkeypatch.setattr(page, name, lambda: None)
    for name in ("page_header", "section_header", "empty_state", "status_bar",
                 "metric_card"):
        monkeypatch.setattr(page, name, lambda *a, **k: None)

    page.show_ai_stock_analysis(runner=lambda symbol: fixture_analysis(symbol))

    written = " ".join(fake.written)
    assert "COMI" in written
    assert "Production Disabled" in written
    assert fake.session_state[page.STATE_BUNDLE] is not None
    assert fake.session_state[page.STATE_SYMBOL] == "COMI"


def test_production_disabled_badge_is_always_present():
    from dashboard.ai_stock_analysis_components import SAFETY_BADGES
    english = [label_en for _, label_en, _ in SAFETY_BADGES]
    assert english == ["Decision Support Only", "Research / Paper Mode",
                       "Production Disabled"]


def test_card_is_not_regenerated_for_unchanged_evidence(result):
    from dashboard.ai_stock_analysis_components import card_cache_key
    first = card_cache_key(result, "POST")
    assert first == card_cache_key(result, "POST")
    assert first != card_cache_key(result, "STORY")
    moved = replace(result, evidence_version="evidence@COMI@2026-07-23@ffffffff")
    assert first != card_cache_key(moved, "POST")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _referenced_names(source: str) -> set[str]:
    """Every identifier the module actually references — imports, names and attributes.

    Comments and docstrings are excluded by construction, so prose about *not* scanning the
    universe cannot trip a check that looks for a universe scan.
    """
    import ast
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.update(alias.name.split("."))
                if alias.asname:
                    names.add(alias.asname)
        elif isinstance(node, ast.ImportFrom):
            names.update((node.module or "").split("."))
            for alias in node.names:
                names.add(alias.name)
                if alias.asname:
                    names.add(alias.asname)
    return names


def _page_source() -> str:
    from pathlib import Path
    import dashboard.ai_stock_analysis as page
    return Path(page.__file__).read_text(encoding="utf-8")


def _components_source() -> str:
    from pathlib import Path
    import dashboard.ai_stock_analysis_components as components
    return Path(components.__file__).read_text(encoding="utf-8")


def _card_source() -> str:
    from pathlib import Path
    import core.analysis_card_generator as card
    return Path(card.__file__).read_text(encoding="utf-8")


def _a_trading_day() -> dt.date:
    """The first ordinary EGX trading day on or after the fixture's session date."""
    from core.egx_session import is_regular_trading_day
    from core.egx_calendar import is_official_holiday
    day = dt.date(2026, 7, 22)
    for _ in range(30):
        if is_regular_trading_day(day) and not is_official_holiday(day):
            return day
        day += dt.timedelta(days=1)
    raise AssertionError("no ordinary trading day found in the probe window")


def _at(day: dt.date, hour: int, minute: int, second: int) -> dt.datetime:
    from core.egx_session import CAIRO
    return dt.datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=CAIRO)


class _FakeColumn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeExpander(_FakeColumn):
    pass


class _FakeSpinner(_FakeColumn):
    pass


class _FakeStreamlit(types.SimpleNamespace):
    """A minimal recording stand-in for the Streamlit API this page uses."""

    def __init__(self, button_returns=False):
        super().__init__()
        self.session_state = {}
        self.written = []
        self._button_returns = button_returns

    # --- recording text sinks ---
    def _record(self, *args, **kwargs):
        for arg in args:
            if isinstance(arg, str):
                self.written.append(arg)
        return None

    markdown = caption = write = text = _record
    error = warning = info = success = _record

    def image(self, *args, **kwargs):
        return None

    def dataframe(self, *args, **kwargs):
        return None

    def plotly_chart(self, *args, **kwargs):
        return None

    # --- inputs ---
    def columns(self, spec, **kwargs):
        count = spec if isinstance(spec, int) else len(spec)
        return [_FakeColumn() for _ in range(count)]

    def selectbox(self, label, options, index=0, **kwargs):
        values = list(options)
        # The production picker starts empty. This recording fake explicitly
        # chooses COMI only for end-to-end render tests that press Analyze.
        if index is None:
            return next(
                (option for option in values
                 if getattr(option, "ticker", None) == "COMI"),
                None,
            )
        return values[index or 0]

    def text_input(self, label, value="", **kwargs):
        return value

    def radio(self, label, options, index=0, format_func=None, **kwargs):
        return list(options)[index or 0]

    def checkbox(self, label, value=False, **kwargs):
        return value

    def button(self, label, **kwargs):
        return self._button_returns

    def download_button(self, *args, **kwargs):
        return False

    def expander(self, *args, **kwargs):
        return _FakeExpander()

    def spinner(self, *args, **kwargs):
        return _FakeSpinner()
