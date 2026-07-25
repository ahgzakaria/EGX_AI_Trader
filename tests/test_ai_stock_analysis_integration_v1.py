"""Integration contract tests for AI Stock Analysis V1.

These tests guard the integration seams only. They do not exercise or modify the frozen
trading strategy, ranking, portfolio, AI filter, or execution logic.
"""

from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from core.ai_analysis_narrative import FALLBACK_MODEL
from core.ai_stock_analysis_contract import MomentumState, TrendState
from core.ai_stock_analysis_history import AnalysisHistoryStore
from core.ai_stock_analysis_service import analyze_symbol
from core.egx_session import CAIRO
from dashboard.ai_stock_analysis_components import (
    key_level_rows,
    momentum_reading,
    narrative_source,
    trend_reading,
)


def _daily(symbol="COMI"):
    index = pd.bdate_range("2025-07-01", periods=260, name="Date")
    close = np.linspace(50.0, 92.0, len(index))
    frame = pd.DataFrame({
        "Open": close - 0.2,
        "High": close + 0.8,
        "Low": close - 0.8,
        "Close": close,
        "Adj Close": close,
        "Volume": np.linspace(1_000_000, 1_500_000, len(index)),
    }, index=index)
    frame.attrs["market_data"] = {
        "provider": "eodhd",
        "data_domain": "CURRENT_RESEARCH_V2",
        "latest_completed_session": index[-1].date().isoformat(),
        "history_sufficient": True,
        "volume_safe_for_lookback": True,
        "yahoo_network_used": False,
    }
    return frame


def _intraday():
    # UTC -> Cairo: 10:00, 10:01, 14:15, 14:16.
    index = pd.DatetimeIndex([
        "2026-07-22T07:00:00Z", "2026-07-22T07:01:00Z",
        "2026-07-22T11:15:00Z", "2026-07-22T11:16:00Z",
    ], name="Date")
    return pd.DataFrame({
        "Open": [90.0, 90.1, 91.0, 91.1],
        "High": [90.2, 90.3, 91.2, 91.3],
        "Low": [89.9, 90.0, 90.9, 91.0],
        "Close": [90.1, 90.2, 91.1, 91.2],
        "Adj Close": [90.1, 90.2, 91.1, 91.2],
        "Volume": [10_000, 11_000, 8_000, 9_000],
    }, index=index)


def _response():
    return analyze_symbol(
        "COMI",
        now=datetime(2026, 7, 22, 12, 0, tzinfo=CAIRO),
        history_loader=lambda _symbol: _daily(),
        live_quote_provider=lambda _symbol: {
            "last": 92.1,
            "quote_timestamp": "2026-07-22T11:59:30+03:00",
        },
        intraday_provider=lambda _symbol: _intraday(),
    )


def test_core_owns_typed_trend_and_momentum():
    result = _response().result
    assert isinstance(result.indicators.trend, TrendState)
    assert isinstance(result.indicators.momentum, MomentumState)
    assert result.indicators.trend_strength is not None
    assert result.indicators.momentum_strength is not None
    # UI helpers only render the typed states.
    assert trend_reading(result)[1] in {"Strong Uptrend", "Uptrend"}
    assert momentum_reading(result)[1] in {
        "Strong Positive", "Positive", "Neutral", "Negative", "Strong Negative"
    }


def test_core_supplies_complete_key_level_contract():
    result = _response().result
    assert result.key_levels
    for level in result.key_levels:
        assert level.timeframe == "1D"
        assert isinstance(level.touches, int)
        assert level.distance_percent is not None
        assert level.strength is not None
    rendered = key_level_rows(result)
    assert any(row["timeframe"] == "1D" for row in rendered)
    assert any(row["touches"] != "—" for row in rendered)


def test_real_typed_daily_and_rubix_series_keep_auction_separate():
    result = _response().result
    assert result.daily_chart_series.source == "eodhd"
    assert len(result.daily_chart_series.points) == 180
    assert result.intraday_chart_series.source == "rubix"
    assert len(result.intraday_chart_series.continuous_points) == 2
    assert len(result.intraday_chart_series.auction_points) == 2
    assert not (
        set(result.intraday_chart_series.continuous_points)
        & set(result.intraday_chart_series.auction_points)
    )


def test_no_old_quote_is_presented_as_live_after_close():
    result = analyze_symbol(
        "COMI",
        now=datetime(2026, 7, 22, 15, 0, tzinfo=CAIRO),
        history_loader=lambda _symbol: _daily(),
        live_quote_provider=lambda _symbol: {
            "last": 92.1,
            "quote_timestamp": "2026-07-22T14:20:00+03:00",
        },
        intraday_provider=lambda _symbol: _intraday(),
    ).result
    assert result.price.last is None
    assert result.data_quality.live_available is False
    assert result.intraday_chart_series is not None


def test_default_narrative_is_honestly_labelled_deterministic_fallback():
    response = _response()
    assert response.narrative.model == FALLBACK_MODEL
    assert narrative_source(response.narrative)[1] == "Deterministic Fallback"


def test_page_routes_to_service_and_navigation_loads():
    page_source = Path("dashboard/ai_stock_analysis.py").read_text(encoding="utf-8")
    app_source = Path("app.py").read_text(encoding="utf-8")
    assert "from core.ai_stock_analysis_service import analyze_symbol" in page_source
    assert "return analyze_symbol(symbol, history_store=AnalysisHistoryStore())" in page_source
    assert "show_ai_stock_analysis" in app_source
    assert "AI Stock Analysis" in app_source
    assert "fixture_analysis" not in page_source


def test_analysis_contract_remains_serializable_and_yahoo_absent():
    payload = asdict(_response().result)
    assert payload["data_quality"]["provider"] == "eodhd"
    assert payload["data_quality"]["yahoo_network_used"] is False
    assert payload["daily_chart_series"]["source"] == "eodhd"


def test_integration_analysis_never_mutates_production_history(tmp_path):
    """Tests and previews must append only to their explicitly temporary store."""
    production = Path(__file__).resolve().parents[1] / "data" / "ai_analysis" / "history.jsonl"
    before = production.read_bytes() if production.exists() else None
    temporary = tmp_path / "history.jsonl"
    store = AnalysisHistoryStore(temporary)

    for minute in (0, 1):
        analyze_symbol(
            "COMI",
            now=datetime(2026, 7, 22, 12, minute, tzinfo=CAIRO),
            history_loader=lambda _symbol: _daily(),
            live_quote_provider=lambda _symbol: None,
            intraday_provider=lambda _symbol: _intraday(),
            history_store=store,
        )

    after = production.read_bytes() if production.exists() else None
    records = temporary.read_text(encoding="utf-8").splitlines()
    assert after == before
    assert len(records) == 2
    assert records[0] != records[1]


def test_exactly_one_requested_symbol_reaches_each_provider():
    calls = []

    def daily(symbol):
        calls.append(("daily", symbol))
        return _daily(symbol)

    def quote(symbol):
        calls.append(("quote", symbol))
        return None

    def intraday(symbol):
        calls.append(("intraday", symbol))
        return _intraday()

    analyze_symbol(
        "COMI",
        now=datetime(2026, 7, 22, 12, 0, tzinfo=CAIRO),
        history_loader=daily,
        live_quote_provider=quote,
        intraday_provider=intraday,
    )

    assert calls == [
        ("daily", "COMI"),
        ("quote", "COMI"),
        ("intraday", "COMI"),
    ]
