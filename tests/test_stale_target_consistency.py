"""Regression tests for display-only target state and page provenance."""

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime

import pandas as pd
import pytest

from core.ai_analysis_evidence import EVIDENCE_ENGINE_VERSION
from core.egx_session import CAIRO
from core.level_status import (
    ACTIVE,
    BASIS_COMPLETED_CLOSE,
    BASIS_FROZEN_CLOSE,
    BASIS_LIVE,
    PREVIOUSLY_REACHED,
    REACHED,
    SHORT,
    STALE_LEVEL,
    assess_levels,
    classic_levels_engine_version,
    classic_snapshot_evidence_hash,
    classify_target,
    risk_reward,
    select_comparison_price,
)
from dashboard.ai_stock_analysis import analysis_provenance
from dashboard.ai_stock_analysis_components import fixture_analysis
from dashboard.stock_details import stock_level_context


STAMP = "2026-07-22T14:30:00+03:00"
SIGNAL_STAMP = "2026-07-26T10:00:00+03:00"
LIVE_STAMP = "2026-07-26T10:05:00+03:00"
NOW_CONTINUOUS = datetime(2026, 7, 26, 11, 0, tzinfo=CAIRO)


def test_long_2211_reaches_target_2210():
    assessment = assess_levels(
        22.11, (("Target 1", 22.10),), signal_timestamp=STAMP,
        price_timestamp=STAMP,
    )
    assert assessment.targets[0].state == REACHED


def test_full_precision_comparison_is_not_display_rounding():
    # Both values display as 22.10 at two decimals, but the raw target is ahead.
    assert f"{22.101:.2f}" == f"{22.104:.2f}"
    assert classify_target(22.101, 22.104) == ACTIVE


def test_required_raw_22104_vs_22105_is_active():
    assert classify_target(22.104, 22.105) == ACTIVE


def test_target_is_not_reached_due_only_to_rounding():
    current = 22.10449
    target = 22.10451
    assert round(current, 2) == round(target, 2)
    assert classify_target(current, target) == ACTIVE


def test_machine_precision_equality_is_reached_without_display_rounding():
    assert classify_target(22.10 - 5e-10, 22.10) == REACHED


@pytest.mark.parametrize(
    ("current", "target", "expected"),
    [
        (22.105, 22.105, REACHED),
        (22.106, 22.105, REACHED),
        (None, 22.105, STALE_LEVEL),
        (22.105, None, STALE_LEVEL),
        (-1.0, 22.105, STALE_LEVEL),
        (22.105, -1.0, STALE_LEVEL),
    ],
)
def test_boundary_missing_and_negative_values(current, target, expected):
    assert classify_target(current, target) == expected


def test_target_two_is_promoted_after_target_one_reached():
    assessment = assess_levels(
        22.11,
        (("Target 1", 22.10), ("Target 2", 23.09)),
        entry=22.11,
        stop=19.84,
        signal_timestamp=STAMP,
        price_timestamp=STAMP,
    )
    assert assessment.targets[0].state == REACHED
    assert assessment.next_active.label == "Target 2"
    assert assessment.next_active.value == 23.09
    assert assessment.risk_reward_next == (23.09 - 22.11) / (22.11 - 19.84)


def test_all_long_targets_reached_has_no_active_target():
    assessment = assess_levels(
        24.0,
        (("Target 1", 22.10), ("Target 2", 23.09)),
        signal_timestamp=STAMP,
        price_timestamp=STAMP,
    )
    assert assessment.all_reached_or_stale
    assert assessment.next_active is None
    assert all(target.state == REACHED for target in assessment.targets)


def test_previously_reached_uses_observed_high_not_rounding():
    assert classify_target(
        22.0,
        22.5,
        high_water=22.6,
        high_water_timestamp="2026-07-26T10:10:00+03:00",
        signal_timestamp=SIGNAL_STAMP,
        high_water_verified=True,
    ) == PREVIOUSLY_REACHED


def test_previously_reached_requires_verified_post_signal_timestamp():
    assert classify_target(22.0, 22.5, high_water=22.6) == ACTIVE
    assert classify_target(
        22.0,
        22.5,
        high_water=22.6,
        high_water_timestamp="2026-07-26T10:10:00+03:00",
        signal_timestamp=SIGNAL_STAMP,
        high_water_verified=False,
    ) == ACTIVE
    assert classify_target(
        22.0,
        22.5,
        high_water=22.6,
        high_water_timestamp="2026-07-26T09:59:59+03:00",
        signal_timestamp=SIGNAL_STAMP,
        high_water_verified=True,
    ) == ACTIVE


def test_short_previously_reached_requires_post_signal_low():
    assert classify_target(
        22.5,
        22.0,
        direction=SHORT,
        low_water=21.9,
        low_water_timestamp="2026-07-26T10:10:00+03:00",
        signal_timestamp=SIGNAL_STAMP,
        low_water_verified=True,
    ) == PREVIOUSLY_REACHED
    assert classify_target(
        22.5,
        22.0,
        direction=SHORT,
        low_water=21.9,
        low_water_timestamp="2026-07-26T09:00:00+03:00",
        signal_timestamp=SIGNAL_STAMP,
        low_water_verified=True,
    ) == ACTIVE


def test_stock_context_retains_previously_reached_from_observed_live_high():
    frame = pd.DataFrame(
        {"Close": [21.90]},
        index=pd.DatetimeIndex(["2026-07-22"], name="Date"),
    )
    frame.attrs["market_data"] = {
        "provider": "eodhd",
        "latest_completed_candle": STAMP,
        "live_quote_last": 22.00,
        "live_quote_timestamp": LIVE_STAMP,
        "live_quote_provider": "rubix",
        "live_quote_freshness": "FRESH",
    }
    stock = _stock(frame)
    stock["FrozenSnapshotPrice"] = 21.90
    context = stock_level_context(
        stock,
        observed_high=22.20,
        observed_high_timestamp="2026-07-26T10:10:00+03:00",
        observed_high_verified=True,
        now=NOW_CONTINUOUS,
    )
    assert context["assessment"].targets[0].state == PREVIOUSLY_REACHED
    assert context["assessment"].next_active.label == "Target 2"


def test_incomparable_frozen_and_live_timestamps_are_stale():
    assessment = assess_levels(
        22.11, (("Target 1", 22.10),),
        signal_timestamp=None,
        price_timestamp="2026-07-26T09:00:00Z",
    )
    assert assessment.targets[0].state == STALE_LEVEL


def test_stock_context_exposes_frozen_and_live_overlay_timestamps():
    frame = pd.DataFrame(
        {"Close": [22.11]},
        index=pd.DatetimeIndex(["2026-07-22"], name="Date"),
    )
    frame.attrs["market_data"] = {
        "provider": "eodhd",
        "latest_completed_candle": STAMP,
        "live_quote_last": 22.44,
        "live_quote_timestamp": LIVE_STAMP,
        "live_quote_provider": "rubix",
        "live_quote_freshness": "FRESH",
    }
    stock = _stock(frame)
    stock.update({
        "LivePrice": 22.44,
        "LivePriceTimestamp": LIVE_STAMP,
        "LivePriceStatus": "FRESH",
    })
    context = stock_level_context(stock, now=NOW_CONTINUOUS)
    assert context["frozen_price"] == 22.11
    assert context["live_price"] == 22.44
    assert context["level_timestamp"] == STAMP
    assert context["price_timestamp"] == pd.Timestamp(LIVE_STAMP)
    assert context["comparison"].status == BASIS_LIVE
    assert context["provenance"].provider == "eodhd"
    assert context["provenance"].live_provider == "rubix"
    assert context["provenance"].status == "FROZEN + LIVE OVERLAY"


def test_stale_live_overlay_is_marked_stale_without_erasing_frozen_reach():
    frame = pd.DataFrame(
        {"Close": [22.11]},
        index=pd.DatetimeIndex(["2026-07-22"], name="Date"),
    )
    frame.attrs["market_data"] = {
        "provider": "eodhd",
        "latest_completed_candle": STAMP,
        "live_quote_last": 22.49,
        "live_quote_timestamp": LIVE_STAMP,
        "live_quote_provider": "rubix",
        "live_quote_freshness": "STALE",
    }
    stock = _stock(frame)
    stock.update({
        "LivePrice": 22.49,
        "LivePriceTimestamp": LIVE_STAMP,
        "LivePriceStatus": "STALE",
    })
    context = stock_level_context(stock, now=NOW_CONTINUOUS)
    assert context["live_assessment"].targets[0].state == STALE_LEVEL
    assert context["assessment"].targets[0].state == REACHED
    assert context["assessment"].next_active.label == "Target 2"
    assert context["price_basis"] == BASIS_COMPLETED_CLOSE


def test_no_active_long_target_may_be_below_current_price():
    assessment = assess_levels(
        22.11,
        (("Target 1", 22.10), ("Target 2", 21.50)),
        signal_timestamp=STAMP,
        price_timestamp=STAMP,
    )
    assert not assessment.has_active_target
    assert not any(
        target.active and target.value <= assessment.current_price
        for target in assessment.targets
    )


def test_short_scenario_can_have_active_target_below_current_price():
    assessment = assess_levels(
        22.11,
        (("Target 1", 21.50),),
        direction=SHORT,
        signal_timestamp=STAMP,
        price_timestamp=STAMP,
    )
    assert assessment.targets[0].state == ACTIVE
    assert assessment.next_active.value < assessment.current_price


def _selection(*, now=NOW_CONTINUOUS, **overrides):
    values = {
        "live_price": 22.44,
        "live_provider": "rubix",
        "live_timestamp": LIVE_STAMP,
        "live_status": "FRESH",
        "completed_close": 22.11,
        "completed_provider": "eodhd",
        "completed_timestamp": STAMP,
        "frozen_price": 22.11,
        "frozen_timestamp": STAMP,
        "signal_timestamp": SIGNAL_STAMP,
        "now": now,
    }
    values.update(overrides)
    return select_comparison_price(**values)


def test_price_precedence_same_continuous_session_uses_rubix():
    selected = _selection()
    assert selected.status == BASIS_LIVE
    assert selected.value == 22.44
    assert selected.provider == "rubix"
    assert selected.timestamp == pd.Timestamp(LIVE_STAMP)


def test_price_precedence_closing_auction_uses_rubix():
    selected = _selection(
        signal_timestamp="2026-07-26T14:00:00+03:00",
        live_timestamp="2026-07-26T14:19:00+03:00",
        now=datetime(2026, 7, 26, 14, 20, tzinfo=CAIRO),
    )
    assert selected.status == BASIS_LIVE
    assert "CLOSING_AUCTION" in selected.reason


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 7, 26, 14, 25, tzinfo=CAIRO),
        datetime(2026, 7, 26, 14, 26, tzinfo=CAIRO),
        datetime(2026, 7, 25, 11, 0, tzinfo=CAIRO),  # Saturday
    ],
)
def test_closed_and_weekend_reject_live_and_use_completed_close(now):
    selected = _selection(now=now)
    assert selected.status == BASIS_COMPLETED_CLOSE
    assert selected.value == 22.11


def test_holiday_rejects_live_via_central_calendar():
    holiday = date(2026, 7, 27)
    selected = _selection(
        signal_timestamp="2026-07-27T10:00:00+03:00",
        live_timestamp="2026-07-27T10:05:00+03:00",
        now=datetime(2026, 7, 27, 11, 0, tzinfo=CAIRO),
        holidays={holiday},
    )
    assert selected.status == BASIS_COMPLETED_CLOSE
    assert "HOLIDAY" in selected.reason


def test_next_trading_session_live_after_signal_is_comparable():
    selected = _selection(
        signal_timestamp="2026-07-23T14:30:00+03:00",
        live_timestamp="2026-07-26T10:05:00+03:00",
        now=NOW_CONTINUOUS,
    )
    assert selected.status == BASIS_LIVE


def test_stale_or_older_rubix_quote_falls_back_to_completed_close():
    stale = _selection(live_status="RUBIX_STALE")
    older = _selection(live_timestamp="2026-07-26T09:59:59+03:00")
    assert stale.status == BASIS_COMPLETED_CLOSE
    assert older.status == BASIS_COMPLETED_CLOSE
    assert "predates frozen calculation" in older.reason


def test_naive_aware_timestamp_mix_is_not_silently_compared():
    selected = _selection(live_timestamp="2026-07-26T10:05:00")
    assert selected.status == BASIS_COMPLETED_CLOSE
    assert "naive and timezone-aware" in selected.reason


def test_completed_close_then_frozen_snapshot_precedence():
    completed = _selection(live_price=None)
    frozen = _selection(
        live_price=None, completed_close=None, completed_timestamp=None
    )
    assert completed.status == BASIS_COMPLETED_CLOSE
    assert completed.provider == "eodhd"
    assert frozen.status == BASIS_FROZEN_CLOSE


def test_remaining_rr_uses_current_price_and_suppresses_below_stop():
    assessment = assess_levels(
        22.50,
        (("Target 1", 22.10), ("Target 2", 23.09)),
        stop=19.84,
        signal_timestamp=STAMP,
        price_timestamp=STAMP,
    )
    assert assessment.next_active.label == "Target 2"
    assert assessment.risk_reward_next == pytest.approx(
        (23.09 - 22.50) / (22.50 - 19.84)
    )
    assert risk_reward(19.80, 19.84, 23.09) is None


def test_context_does_not_modify_frozen_strategy_values():
    frame = pd.DataFrame(
        {"Close": [22.11]},
        index=pd.DatetimeIndex(["2026-07-22"], name="Date"),
    )
    stock = _stock(frame)
    original = {
        name: deepcopy(stock[name])
        for name in ("BuyLow", "BuyHigh", "StopLoss", "Target1", "Target2", "RR")
    }
    context = stock_level_context(stock, now=NOW_CONTINUOUS)
    assert {name: stock[name] for name in original} == original
    assert context["assessment"].next_active.value == stock["Target2"]


def test_legacy_serialized_signal_is_safe_and_never_fabricates_provenance():
    frame = pd.DataFrame(
        {"Close": [22.11]},
        index=pd.DatetimeIndex(["2026-07-22"], name="Date"),
    )
    legacy = {
        "Ticker": "SAUD.CA",
        "Price": 22.11,
        "Signal": "WATCH",
        "BuyLow": 21.96,
        "BuyHigh": 22.11,
        "StopLoss": 19.84,
        "Target1": 22.10,
        "Target2": 23.09,
        "RR": 0.43,
        "Data": frame,
    }
    context = stock_level_context(legacy, now=NOW_CONTINUOUS)
    assert context["legacy"] is True
    assert context["signal_timestamp"] is None
    assert context["provenance"].evidence_hash == "Legacy Snapshot"
    assert context["provenance"].engine == "Legacy Snapshot"
    assert context["provenance"].provider == "Legacy Snapshot"
    assert all(
        target.state == STALE_LEVEL for target in context["assessment"].targets
    )


def test_stock_details_legacy_snapshot_streamlit_smoke():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string(
        """
import pandas as pd
from dashboard.stock_details import show_stock_details

frame = pd.DataFrame(
    {"Close": [22.11], "Volume": [1000.0]},
    index=pd.DatetimeIndex(["2026-07-22"], name="Date"),
)
stock = {
    "Ticker": "SAUD.CA", "Rating": "A", "Signal": "WATCH",
    "Price": 22.11, "Confidence": 80, "Score": 70,
    "AIProbability": None, "AILevel": "N/A", "RR": 0.43,
    "BuyLow": 21.96, "BuyHigh": 22.11, "StopLoss": 19.84,
    "Target1": 22.10, "Target2": 23.09,
    "Support": 19.99, "Resistance": 22.16,
    "Reasons": "legacy reason", "Regime": "BULL", "IndexRegime": "BULL",
    "DecisionTrace": {}, "ConfidenceBreakdown": {},
    "Trend": 20, "Volume": 10, "Momentum": 10, "Candles": 5,
    "Breakout": 5, "Data": frame,
}
show_stock_details(stock)
"""
    )
    app.run(timeout=30)
    assert not app.exception


def test_classic_provenance_hash_is_stable_and_does_not_mutate_evidence():
    frame = pd.DataFrame(
        {
            "Open": [22.0], "High": [22.2], "Low": [21.9],
            "Close": [22.11], "Volume": [1_000.0],
        },
        index=pd.DatetimeIndex(["2026-07-22"], name="Date"),
    )
    before = frame.copy(deep=True)
    result = _stock(frame)
    first = classic_snapshot_evidence_hash("SAUD.CA", frame, result)
    second = classic_snapshot_evidence_hash("SAUD.CA", frame, result)
    assert first == second and first.startswith("sha256:")
    assert classic_levels_engine_version().startswith("classic_levels@sha256:")
    pd.testing.assert_frame_equal(frame, before)


def test_ai_page_provenance_uses_typed_evidence_identity():
    result = fixture_analysis("COMI").result
    result = replace(
        result,
        evidence_hash="sha256:typed-evidence",
        generated_at="2026-07-26T12:12:31+03:00",
    )
    provenance = analysis_provenance(result)
    assert provenance.engine == EVIDENCE_ENGINE_VERSION
    assert provenance.evidence_hash == "sha256:typed-evidence"
    assert provenance.signal_timestamp == "2026-07-26T12:12:31+03:00"
    assert provenance.provider == result.data_quality.provider


FROZEN_SCANNER_FIELDS = (
    "Signal", "Score", "Confidence", "BuyLow", "BuyHigh", "StopLoss",
    "Target1", "Target2", "RR", "Reasons",
)


def test_scanner_provenance_is_additive_to_frozen_golden_output(monkeypatch):
    """Golden scanner rows and provider-call count stay at the pre-fix baseline."""

    import core.scanner as scanner

    basket = [
        "SAUD.CA", "COMI.CA", "EAST.CA", "SWDY.CA", "TMGH.CA",
        "WATCH.CA", "BLOCKED.CA",
    ]
    decisions = {
        symbol: _fake_decision(symbol, index)
        for index, symbol in enumerate(basket[:-1])
    }
    # This immutable projection is exactly what the pre-fix scanner returned.
    golden = {
        symbol: {
            "Signal": result["Signal"],
            "Score": result["Score"],
            "Confidence": result["Confidence"],
            "BuyLow": result["BuyLow"],
            "BuyHigh": result["BuyHigh"],
            "StopLoss": result["StopLoss"],
            "Target1": result["Target1"],
            "Target2": result["Target2"],
            "RR": result["RR"],
            "Reasons": " | ".join(result["Reasons"]),
        }
        for symbol, result in decisions.items()
    }
    baseline_rank = [
        symbol for symbol, _ in sorted(
            decisions.items(),
            key=lambda item: (
                item[1]["AIProbability"], item[1]["Confidence"],
                item[1]["Score"], item[1]["RR"],
            ),
            reverse=True,
        )
    ]
    provider_calls = []

    def load_history(symbol, purpose=None, **kwargs):
        # ``**kwargs`` absorbs the scan-scoped context the scanner now threads through;
        # this double still asserts on the symbol and purpose it was called with.
        provider_calls.append((symbol, purpose))
        if symbol == "BLOCKED.CA":
            raise RuntimeError("DATA_INSUFFICIENT")
        frame = _scanner_frame(symbol)
        return frame

    class DecisionService:
        LIVE_ADVISORY = "LIVE_ADVISORY"

        def __init__(self, mode=None):
            self.mode = mode

        def evaluate(self, frame, index):
            return deepcopy(decisions[frame.attrs["symbol"]])

    class Experiment:
        run_id = "RUN_20260726_100000"
        run_dir = "unused"

        def __init__(self, *args, **kwargs):
            pass

        def save_records(self, *args, **kwargs):
            pass

        def save_dataframe(self, *args, **kwargs):
            pass

        def complete(self, *args, **kwargs):
            pass

        def fail(self, error):
            raise AssertionError(f"unexpected experiment failure: {error}")

    class Paper:
        def update_open_trades(self):
            pass

        def record_signals(self, rows):
            pass

    class Forward:
        def process_scan(self, *args, **kwargs):
            return {"status": "TEST"}

    monkeypatch.setattr(scanner, "load_symbols", lambda source: basket)
    monkeypatch.setattr(scanner, "load_history", load_history)
    monkeypatch.setattr(scanner, "calculate_indicators", lambda frame: frame)
    monkeypatch.setattr(scanner, "TradingDecisionService", DecisionService)
    monkeypatch.setattr(scanner, "BreakoutSwingStrategy", lambda: object())
    monkeypatch.setattr(
        scanner, "_safe_breakout_evaluation",
        lambda *args: _fake_breakout(),
    )
    monkeypatch.setattr(scanner, "ExperimentRun", Experiment)
    monkeypatch.setattr(scanner, "PaperTradingTracker", Paper)
    monkeypatch.setattr(scanner, "ForwardTestingService", Forward)
    monkeypatch.setattr(scanner, "actionability_fields", lambda *args: {})
    monkeypatch.setattr(
        scanner, "successful_coverage_row",
        lambda symbol, *args: {"symbol": symbol, "status": "OK"},
    )
    monkeypatch.setattr(
        scanner, "failed_coverage_row",
        lambda symbol, *args: {"symbol": symbol, "status": "FAILED"},
    )
    monkeypatch.setattr(scanner, "symbol_data_coverage", lambda symbol: {})
    monkeypatch.setattr(
        scanner, "write_coverage_report", lambda rows: pd.DataFrame(rows)
    )
    monkeypatch.setattr(
        scanner, "_apply_adaptive_selector", lambda rows: {"status": "TEST"}
    )
    monkeypatch.setattr(scanner.settings, "reload", lambda: None)

    # The fixture frames end on their own synthetic session, so the freshness
    # gate is told which session counts as current HERE. Deriving today's
    # exchange session would exclude every fixture symbol and the test would
    # stop saying anything about provenance additivity.
    fixture_session = pd.date_range("2025-07-01", periods=260, freq="D")[-1].date()
    rows = scanner.scan_symbols("unused.csv", data_purpose="scanner",
                                expected_session=fixture_session.isoformat())
    by_symbol = {row["Ticker"]: row for row in rows}

    assert len(provider_calls) == len(basket)  # pre-fix baseline: one per symbol
    assert [symbol for symbol, _purpose in provider_calls] == basket
    assert [row["Ticker"] for row in rows] == baseline_rank
    for symbol, expected in golden.items():
        assert {
            field: by_symbol[symbol][field] for field in FROZEN_SCANNER_FIELDS
        } == expected
        assert by_symbol[symbol]["LevelsCalculationVersion"].startswith(
            "classic_levels@sha256:"
        )
        assert by_symbol[symbol]["EvidenceHash"].startswith("sha256:")
    assert "BLOCKED.CA" not in by_symbol


def _scanner_frame(symbol):
    index = pd.date_range("2025-07-01", periods=260, freq="D")
    base = pd.Series(range(260), index=index, dtype=float) / 100 + 20
    frame = pd.DataFrame({
        "Open": base,
        "High": base + 0.20,
        "Low": base - 0.20,
        "Close": base + 0.05,
        "Volume": 1_000.0,
        "EMA20": base,
        "EMA50": base,
        "EMA200": base,
        "ATR": 0.40,
        "RSI": 55.0,
        "MACD": 0.10,
    })
    frame.index.name = "Date"
    frame.attrs["symbol"] = symbol
    frame.attrs["market_data"] = {
        "provider": "eodhd",
        "data_domain": "CURRENT_RESEARCH_V2",
        "live_quote_available": False,
    }
    return frame


def _fake_decision(symbol, index):
    signal = ("BUY", "WATCH", "AVOID")[index % 3]
    score = 80 - index
    confidence = 90 - index
    return {
        "Signal": signal,
        "Stars": 4,
        "Confidence": confidence,
        "Score": score,
        "AIProbability": 90.0 - index * 10,
        "AILevel": "Good",
        "AIApproved": None,
        "Trend": 20,
        "Volume": 10,
        "Momentum": 10,
        "Candles": 5,
        "Breakout": 5,
        "Support": 19.0,
        "Resistance": 23.0,
        "BuyLow": 20.0 + index,
        "BuyHigh": 20.5 + index,
        "StopLoss": 19.5 + index,
        "Target1": 22.0 + index,
        "Target2": 23.0 + index,
        "RR": 2.5 - index * 0.1,
        "Reasons": [f"golden-{symbol}", signal],
        "Regime": "BULL",
        "IndexRegime": "BULL",
        "DecisionTrace": {"Risk": "PASS"},
        "ConfidenceBreakdown": {"Risk": 10},
    }


def _fake_breakout():
    return {
        "Signal": "UNAVAILABLE",
        "RR": 0.0,
        "Score": 0,
        "Confidence": 0,
        "EdgeScore": 0,
        "Entry": None,
        "StopLoss": None,
        "Target1": None,
        "Target2": None,
        "SetupTypes": (),
        "Reasons": ("test unavailable",),
        "DecisionTrace": {},
        "DecisionSupportOnly": True,
    }


def _stock(frame):
    return {
        "Ticker": "SAUD.CA",
        "Price": 22.11,
        "FrozenSnapshotPrice": 22.11,
        "FrozenDataTimestamp": STAMP,
        "SignalTimestamp": SIGNAL_STAMP,
        "CompletedSessionClose": 22.11,
        "CompletedSessionTimestamp": STAMP,
        "CompletedSessionProvider": "eodhd",
        "HistoricalProvider": "eodhd",
        "LevelsCalculationVersion": "classic_levels@test",
        "EvidenceHash": "sha256:test-saud",
        "Signal": "WATCH",
        "BuyLow": 21.96,
        "BuyHigh": 22.11,
        "StopLoss": 19.84,
        "Target1": 22.10,
        "Target2": 23.09,
        "RR": 0.43,
        "Support": 19.99,
        "Resistance": 22.16,
        "Data": frame,
    }
