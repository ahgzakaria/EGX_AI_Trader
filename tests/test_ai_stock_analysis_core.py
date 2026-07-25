"""Core tests for AI Stock Analysis On Demand (Claude Core ownership).

Covers the corrected typed contract (SMA/EMA distinct; explicit MACD/OBV/volume-ratio/
expected-range-position fields; no numbers hidden in strings), Layer-1 evidence
determinism + indicator correctness, the volume-safety / derived-provenance / Yahoo-never
invariants, the auction-aware market-phase boundaries (incl. the 14:25 cutoff), the safe
Arabic narrative interface (tightened numeric-hallucination validation + deterministic
fallback), append-only history, fixture conformance, JSON serialization, and the
one-symbol-only service path (no universe scan) — all with fully injected dependencies
(no network, no live DB).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from core import ai_analysis_evidence as evidence
from core import ai_analysis_narrative as narrative
from core.ai_stock_analysis_contract import (
    AnalysisRequest,
    ConfidenceComponent,
    DataStatus,
    IndicatorSummary,
    KeyLevel,
    MarketPhase,
    PriceSummary,
    Recommendation,
    ScenarioResult,
    ScenarioState,
)
from core.ai_stock_analysis_history import AnalysisHistoryStore
from core.ai_stock_analysis_service import (
    AnalysisResponse,
    analyze_symbol,
    resolve_market_phase,
)

CAIRO = ZoneInfo("Africa/Cairo")
FIXTURE = Path(__file__).parent / "fixtures" / "ai_stock_analysis_evidence.json"
GEN_AT = "2026-07-22T13:40:02+03:00"


# --------------------------------------------------------------------------- #
# Synthetic frame helpers (deterministic, contract-shaped)
# --------------------------------------------------------------------------- #

def _frame(closes, *, volume=1_000_000.0, volume_safe=True, latest_session=None,
           high_mult=1.01, low_mult=0.99, market_data=None):
    n = len(closes)
    idx = pd.date_range("2024-01-01", periods=n, freq="B", name="Date")
    close = pd.Series(closes, dtype=float, index=idx)
    vol = pd.Series(np.full(n, volume, dtype=float) if np.isscalar(volume) else volume,
                    index=idx)
    df = pd.DataFrame({
        "Open": close.shift(1).fillna(close.iloc[0]),
        "High": close * high_mult,
        "Low": close * low_mult,
        "Close": close,
        "Adj Close": close,
        "Volume": vol,
    }, index=idx)
    md = {
        "data_domain": "CURRENT_RESEARCH_V2", "provider": "eodhd",
        "price_series": "SPLIT_ADJUSTED",
        "price_adjustment_policy": "SPLIT_ADJUSTED_ALL_EVENTS",
        "volume_series": "RAW_EODHD", "volume_adjustment_policy": "NONE",
        "volume_safe_for_lookback": volume_safe,
        "latest_action_in_lookback": None if volume_safe else "2024-06-01",
        "freshness_status": "HISTORY_CURRENT",
        "latest_completed_session": latest_session or idx[-1].date().isoformat(),
        "expected_completed_session": latest_session or idx[-1].date().isoformat(),
        "history_sufficient": n >= 200,
        "yahoo_network_used": False, "yahoo_seed_present": False,
        "routing_tier": "TIER_A_FORWARD_SAFE", "live_provider": "rubix",
    }
    if market_data:
        md.update(market_data)
    df.attrs["market_data"] = md
    return df


def _uptrend(n=260, start=50.0, step=0.15):
    return _frame([start + i * step for i in range(n)])


def _curved(n=260, start=50.0):
    # Non-linear (accelerating) uptrend so SMA and EMA of the same period differ.
    return _frame([start + (i ** 1.4) * 0.02 for i in range(n)])


def _downtrend(n=260, start=100.0, step=0.15):
    return _frame([start - i * step for i in range(n)])


def _request(symbol="TEST", **kw):
    base = dict(symbol=symbol, request_id="req-test-0001",
                as_of="2026-07-22T13:40:00+03:00",
                market_phase=MarketPhase.CONTINUOUS, lookback_days=250,
                include_live=True, language="ar", requested_by="pytest")
    base.update(kw)
    return AnalysisRequest(**base)


def _evidence(symbol="UP", df=None):
    return evidence.build_evidence(_request(symbol), df if df is not None else _uptrend(),
                                   market_phase=MarketPhase.CONTINUOUS, generated_at=GEN_AT)


# --------------------------------------------------------------------------- #
# Indicator correctness
# --------------------------------------------------------------------------- #

def test_sma_and_ema_match_reference():
    close = pd.Series([float(x) for x in range(1, 41)])
    assert evidence._last(evidence._sma(close, 20)) == pytest.approx(close.iloc[-20:].mean())
    ema = evidence._last(evidence._ema(close, 20))
    assert close.iloc[-20:].mean() < ema < close.iloc[-1]


def test_rsi_all_gains_is_100_all_losses_is_0():
    up = pd.Series([float(x) for x in range(1, 60)])
    down = pd.Series([float(x) for x in range(60, 1, -1)])
    assert evidence._last(evidence._rsi_wilder(up)) == pytest.approx(100.0)
    assert evidence._last(evidence._rsi_wilder(down)) == pytest.approx(0.0)


def test_atr_positive():
    df = _uptrend()
    atr = evidence._last(evidence._atr_wilder(df["High"], df["Low"], df["Close"]))
    assert atr is not None and atr > 0


def test_macd_histogram_sign_follows_trend():
    up = pd.Series([50 + i * 0.5 for i in range(80)])
    _, _, hist_up = evidence._macd(up)
    assert evidence._last(hist_up) > 0
    down = pd.Series([100 - i * 0.5 for i in range(80)])
    _, _, hist_dn = evidence._macd(down)
    assert evidence._last(hist_dn) < 0


def test_obv_direction_accumulates():
    close = pd.Series([1.0, 2.0, 3.0, 2.0, 4.0])
    vol = pd.Series([10.0] * 5)
    assert evidence._obv(close, vol).iloc[-1] == pytest.approx(20.0)


# --------------------------------------------------------------------------- #
# Typed contract: SMA/EMA distinct, explicit MACD/OBV/ratio/range-position
# --------------------------------------------------------------------------- #

def test_sma_and_ema_are_distinct_typed_fields():
    ind = _evidence("CV", _curved()).indicators
    for p in (20, 50, 200):
        assert getattr(ind, f"sma_{p}") is not None
        assert getattr(ind, f"ema_{p}") is not None
        # On a non-linear trend, the recent-weighted EMA differs from the SMA — and each
        # lives in its own typed field (an EMA value never occupies an SMA field).
        assert getattr(ind, f"sma_{p}") != getattr(ind, f"ema_{p}")


def test_macd_fields_are_explicit_and_consistent():
    ind = _evidence().indicators
    assert ind.macd is not None and ind.macd_signal is not None
    assert ind.macd_histogram is not None
    assert ind.macd - ind.macd_signal == pytest.approx(ind.macd_histogram, abs=1e-3)


def test_obv_volume_ratio_and_range_position_are_typed_fields():
    ind = _evidence().indicators
    assert ind.obv is not None
    assert ind.volume_ratio is not None
    assert ind.expected_range_position is not None
    assert 0.0 <= ind.expected_range_position <= 100.0
    assert ind.average_volume_20 is not None and ind.turnover is not None


def test_no_market_value_numbers_are_hidden_in_strings():
    """Every technical/market number is typed; reasons carry no float market values and
    each key level's price is a typed field (its basis is only a derivation label)."""
    r = _evidence()
    import re
    decimal = re.compile(r"\d+\.\d")
    for reason in r.recommendation_reasons:
        assert not decimal.search(reason), f"reason leaks a numeric value: {reason!r}"
    # Every level exposes its value in the typed `price` field — the UI never parses
    # `basis` (a label that may cite a derivation constant like the ATR multiplier).
    for lv in r.key_levels:
        assert lv.price is not None


# --------------------------------------------------------------------------- #
# Evidence: determinism, provenance, recommendation
# --------------------------------------------------------------------------- #

def test_evidence_is_deterministic():
    df = _uptrend()
    r1 = evidence.build_evidence(_request("UP"), df, market_phase=MarketPhase.CONTINUOUS,
                                 generated_at=GEN_AT)
    r2 = evidence.build_evidence(_request("UP"), df, market_phase=MarketPhase.CONTINUOUS,
                                 generated_at="2099-01-01T00:00:00+03:00")
    assert r1.evidence_hash == r2.evidence_hash == r1.evidence_hash
    assert r1.evidence_version == r2.evidence_version
    assert r1.evidence_hash.startswith("sha256:")


def test_live_quote_does_not_change_evidence_hash():
    df = _uptrend()
    r_no_live = evidence.build_evidence(_request("UP"), df,
                                        market_phase=MarketPhase.CONTINUOUS, generated_at=GEN_AT)
    r_live = evidence.build_evidence(
        _request("UP"), df, market_phase=MarketPhase.CONTINUOUS, generated_at=GEN_AT,
        live_quote={"available": True, "last": 999.0, "bid": 998.0, "ask": 1000.0,
                    "spread_percent": 0.2, "quote_timestamp": "2026-07-22T13:39:58+03:00"})
    assert r_no_live.evidence_hash == r_live.evidence_hash    # live is volatile, excluded
    assert r_live.price.last == pytest.approx(999.0)
    assert r_live.price.bid == pytest.approx(998.0)


def test_price_missing_values_stay_none_not_zero():
    r = evidence.build_insufficient_evidence(
        _request("NONE"), market_phase=MarketPhase.CLOSED, generated_at=GEN_AT)
    assert r.price.close is None
    assert r.price.open is None and r.price.volume is None
    assert r.recommendation == Recommendation.DATA_INSUFFICIENT


def test_uptrend_recommendation_is_constructive():
    r = _evidence()
    assert r.recommendation in (Recommendation.READY_WITH_CONDITIONS,
                                Recommendation.NEAR_READY, Recommendation.WATCH,
                                Recommendation.WAIT)
    assert r.confidence.overall == pytest.approx(
        sum(c.weight * c.score for c in r.confidence.components), abs=0.1)


def test_downtrend_avoids():
    r = _evidence("DN", _downtrend())
    assert r.recommendation == Recommendation.AVOID
    assert r.scenarios[0].state == ScenarioState.AVOID


def test_scenario_typed_levels_are_consistent():
    primary = _evidence().scenarios[0]
    assert primary.trigger is not None
    assert primary.stop < primary.entry_low < primary.target
    assert primary.risk_reward is not None and primary.risk_reward > 0
    assert primary.remaining_room_percent is not None
    assert primary.confidence is not None
    assert primary.confirmation_requirements and primary.invalidation_conditions


def test_key_levels_have_sma_and_ema_bases():
    r = _evidence()
    bases = {lv.basis for lv in r.key_levels}
    assert any(b.startswith("EMA") for b in bases)
    assert any(b.startswith("SMA") for b in bases)
    assert any("high" in b for b in bases)


# --------------------------------------------------------------------------- #
# Volume safety — Core respects the router-supplied series, no re-derivation
# --------------------------------------------------------------------------- #

def test_core_uses_router_supplied_volume_series_verbatim():
    vols = [1_000.0 + i for i in range(260)]
    df = _frame([50 + i * 0.15 for i in range(260)], volume=pd.Series(
        vols, index=pd.date_range("2024-01-01", periods=260, freq="B", name="Date")),
        market_data={"volume_series": "TRUE_SPLIT_ADJUSTED"})
    ind = evidence.build_evidence(_request("UP"), df, market_phase=MarketPhase.CONTINUOUS,
                                  generated_at=GEN_AT).indicators
    assert ind.volume_series == "TRUE_SPLIT_ADJUSTED"       # provenance echoed, not changed
    assert ind.volume_safe is True
    # average_volume_20 is exactly the mean of the last 20 supplied volumes (no transform).
    assert ind.average_volume_20 == pytest.approx(round(float(np.mean(vols[-20:])), 2))


def test_volume_unsafe_suppresses_all_volume_fields():
    df = _uptrend()
    df.attrs["market_data"]["volume_safe_for_lookback"] = False
    df.attrs["market_data"]["latest_action_in_lookback"] = "2024-06-01"
    r = evidence.build_evidence(_request("UP"), df, market_phase=MarketPhase.CONTINUOUS,
                                generated_at=GEN_AT)
    ind = r.indicators
    assert ind.volume_safe is False
    assert ind.average_volume_20 is None
    assert ind.volume_ratio is None
    assert ind.obv is None
    assert ind.turnover is None
    assert r.data_quality.status == DataStatus.VOLUME_UNSAFE
    assert any("OBV suppressed" in x for x in r.recommendation_reasons)
    assert not any("OBV rising" in x or "OBV falling" in x for x in r.recommendation_reasons)


def test_unsafe_volume_does_not_increase_confidence():
    safe = _evidence()
    df = _uptrend()
    df.attrs["market_data"]["volume_safe_for_lookback"] = False
    unsafe = evidence.build_evidence(_request("UP"), df, market_phase=MarketPhase.CONTINUOUS,
                                     generated_at=GEN_AT)
    liq_safe = next(c.score for c in safe.confidence.components if c.name == "liquidity")
    liq_unsafe = next(c.score for c in unsafe.confidence.components if c.name == "liquidity")
    assert liq_unsafe < liq_safe
    assert unsafe.confidence.overall <= safe.confidence.overall


# --------------------------------------------------------------------------- #
# Provenance derived from metadata (not hard-coded); Yahoo never operational
# --------------------------------------------------------------------------- #

def test_provenance_is_derived_from_frame_metadata():
    df = _uptrend()
    df.attrs["market_data"].update({
        "data_domain": "CURRENT_RESEARCH_V2", "provider": "local_plus_rubix",
        "yahoo_network_used": False, "yahoo_seed_present": True,
        "live_provider": "rubix", "data_quality_status": "LOCAL_PLUS_RUBIX_READY",
    })
    dq = evidence.build_evidence(_request("UP"), df, market_phase=MarketPhase.CONTINUOUS,
                                 generated_at=GEN_AT).data_quality
    assert dq.data_domain == "CURRENT_RESEARCH_V2"
    assert dq.provider == "local_plus_rubix"          # derived, not assumed eodhd
    assert dq.yahoo_seed_present is True              # frozen bootstrap seed allowed
    assert dq.yahoo_network_used is False             # invariant still holds
    assert any("router_status=LOCAL_PLUS_RUBIX_READY" in n for n in dq.notes)


def test_yahoo_network_never_used_even_if_metadata_absent():
    df = _uptrend()
    df.attrs["market_data"].pop("yahoo_network_used", None)
    dq = evidence.build_evidence(_request("UP"), df, market_phase=MarketPhase.CONTINUOUS,
                                 generated_at=GEN_AT).data_quality
    assert dq.yahoo_network_used is False


def test_insufficient_history_is_flagged():
    df = _frame([10.0 + i * 0.1 for i in range(30)])
    r = evidence.build_evidence(_request("SHORT"), df, market_phase=MarketPhase.CONTINUOUS,
                                generated_at=GEN_AT)
    assert r.data_quality.status == DataStatus.HISTORY_INSUFFICIENT
    assert r.recommendation == Recommendation.DATA_INSUFFICIENT


# --------------------------------------------------------------------------- #
# Narrative: tightened numeric hallucination validation + fallback
# --------------------------------------------------------------------------- #

def test_fallback_narrative_contains_no_original_numbers():
    result = _evidence()
    nar = narrative.build_fallback_narrative(result)
    allowed = narrative.allowed_numbers(result)
    for field in (nar.headline, nar.summary, nar.rationale, nar.risks):
        ok, offending = narrative.validate_no_original_numbers(field, allowed)
        assert ok, f"fallback introduced untraceable numbers: {offending}"
    assert nar.contains_no_original_numbers is True
    assert nar.model == narrative.FALLBACK_MODEL
    assert nar.derived_from_evidence_version == result.evidence_version


def test_validator_rejects_unrelated_number_near_large_volume():
    """A figure within the old broad relative tolerance of a large volume must NOT pass."""
    result = _evidence()
    allowed = narrative.allowed_numbers(result)
    avg_vol = result.indicators.average_volume_20
    assert avg_vol and avg_vol > 500_000
    bogus = round(avg_vol * 0.995, 2)                 # ~0.5% off — old 1% tol would accept
    ok, offending = narrative.validate_no_original_numbers(f"حجم {bogus}", allowed)
    assert not ok and offending


def test_validator_accepts_formatted_versions_of_known_values():
    result = _evidence()
    allowed = narrative.allowed_numbers(result)
    close = result.price.close
    for rendering in (f"{close}", f"{close:.0f}", f"{close:.2f}"):
        ok, _ = narrative.validate_no_original_numbers(f"الإغلاق {rendering}", allowed)
        assert ok, f"formatted known value rejected: {rendering}"


def test_validator_allows_session_year_but_rejects_fake_price():
    result = _evidence()
    allowed = narrative.allowed_numbers(result)
    # A year embedded in the session/quote timestamps is traceable; a made-up price is not.
    ok_year, _ = narrative.validate_no_original_numbers("عام 2024", allowed)
    assert ok_year
    ok_price, off = narrative.validate_no_original_numbers("السعر 4242.42", allowed)
    assert not ok_price and off


def test_generator_accepted_when_numbers_are_traceable():
    result = _evidence()
    close = result.price.close

    def clean_generator(res, lang):
        return {"headline": f"{res.request.symbol} في اتجاه صاعد",
                "summary": f"الإغلاق عند {close} جنيه.",
                "rationale": "الاتجاه صاعد والزخم إيجابي.",
                "risks": "فشل الإغلاق أعلى المقاومة يبطل الفرصة.",
                "model": "test-model@1"}

    nar = narrative.generate_narrative(result, generator=clean_generator)
    assert nar.model == "test-model@1"
    assert str(close) in nar.summary


def test_generator_rejected_falls_back():
    result = _evidence()

    def hallucinating(res, lang):
        return {"headline": "سعر مستهدف 424242.42", "summary": "x",
                "rationale": "y", "risks": "z"}

    assert narrative.generate_narrative(result, generator=hallucinating).model \
        == narrative.FALLBACK_MODEL


def test_generator_exception_falls_back():
    result = _evidence()

    def boom(res, lang):
        raise RuntimeError("model down")

    assert narrative.generate_narrative(result, generator=boom).model \
        == narrative.FALLBACK_MODEL


def test_data_insufficient_narrative_is_safe():
    result = evidence.build_insufficient_evidence(
        _request("NONE"), market_phase=MarketPhase.CLOSED, generated_at=GEN_AT)
    nar = narrative.build_fallback_narrative(result)
    allowed = narrative.allowed_numbers(result)
    for field in (nar.headline, nar.summary, nar.rationale, nar.risks):
        ok, _ = narrative.validate_no_original_numbers(field, allowed)
        assert ok
    assert "غير كافية" in nar.headline


# --------------------------------------------------------------------------- #
# Append-only history store
# --------------------------------------------------------------------------- #

def test_history_store_is_append_only(tmp_path):
    store = AnalysisHistoryStore(tmp_path / "history.jsonl")
    r1 = _evidence()
    nar = narrative.build_fallback_narrative(r1)
    store.record_analysis(r1, nar, created_at="2026-07-22T13:40:03+03:00")
    store.record_analysis(r1, nar, created_at="2026-07-22T14:00:03+03:00")
    lines = (tmp_path / "history.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert store.latest("UP").created_at == "2026-07-22T14:00:03+03:00"
    for line in lines:
        obj = json.loads(line)
        assert obj["symbol"] == "UP" and obj["evidence_version"].startswith("evidence@UP@")


def test_history_tolerates_corrupt_line(tmp_path):
    path = tmp_path / "history.jsonl"
    store = AnalysisHistoryStore(path)
    store.record_analysis(_evidence(), created_at="2026-07-22T13:40:03+03:00")
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not valid json\n")
    assert len(store.records()) == 1


# --------------------------------------------------------------------------- #
# Service orchestration (one-symbol-only, injected deps → no network)
# --------------------------------------------------------------------------- #

def test_service_end_to_end_with_injected_loader(tmp_path):
    store = AnalysisHistoryStore(tmp_path / "h.jsonl")
    resp = analyze_symbol("up", now=datetime(2026, 7, 22, 12, 0, tzinfo=CAIRO),
                          history_loader=lambda s: _uptrend(),
                          live_quote_provider=lambda s: None, history_store=store)
    assert isinstance(resp, AnalysisResponse)
    assert resp.result.request.symbol == "UP"
    assert resp.result.market_phase == MarketPhase.CONTINUOUS
    assert resp.narrative.derived_from_evidence_version == resp.result.evidence_version
    assert store.latest("UP") is not None


def test_service_analyzes_one_symbol_and_does_not_scan_universe():
    calls = []

    def spy_loader(symbol):
        calls.append(symbol)
        return _uptrend()

    analyze_symbol("comi", history_loader=spy_loader, live_quote_provider=lambda s: None,
                   now=datetime(2026, 7, 22, 12, 0, tzinfo=CAIRO))
    assert calls == ["COMI"]        # exactly one symbol loaded, once — no universe sweep


def test_service_rejects_collection_of_symbols():
    with pytest.raises(ValueError):
        analyze_symbol(["COMI", "SWDY"], history_loader=lambda s: _uptrend())


def test_service_rejects_empty_symbol():
    with pytest.raises(ValueError):
        analyze_symbol("   ", history_loader=lambda s: _uptrend())


def test_service_handles_loader_data_block_gracefully():
    class _Unavailable(RuntimeError):
        status = "DATA_UNAVAILABLE"

    resp = analyze_symbol("XYZ", history_loader=lambda s: (_ for _ in ()).throw(
        _Unavailable("EODHD returned no bars")), live_quote_provider=lambda s: None)
    assert resp.result.recommendation == Recommendation.DATA_INSUFFICIENT
    assert resp.result.data_quality.status == DataStatus.DATA_UNAVAILABLE
    assert resp.result.data_quality.yahoo_network_used is False
    assert resp.narrative.model == narrative.FALLBACK_MODEL


def test_service_live_overlay_populates_price_and_quality():
    quote = {"available": True, "last": 99.5, "bid": 99.4, "ask": 99.6,
             "spread_percent": 0.2, "quote_timestamp": "2026-07-22T13:39:58+03:00"}
    resp = analyze_symbol("up", history_loader=lambda s: _uptrend(),
                          live_quote_provider=lambda s: quote,
                          now=datetime(2026, 7, 22, 12, 0, tzinfo=CAIRO))
    assert resp.result.price.last == pytest.approx(99.5)
    assert resp.result.price.spread_percent == pytest.approx(0.2)
    assert resp.result.data_quality.live_available is True


def test_service_live_failure_is_non_fatal():
    resp = analyze_symbol("up", history_loader=lambda s: _uptrend(),
                          live_quote_provider=lambda s: (_ for _ in ()).throw(
                              RuntimeError("rubix down")),
                          now=datetime(2026, 7, 22, 12, 0, tzinfo=CAIRO))
    assert resp.result.data_quality.live_available is False
    assert resp.result.recommendation != Recommendation.DATA_INSUFFICIENT


# --------------------------------------------------------------------------- #
# Market-phase mapping + auction boundary tests (14:15 / 14:25 / 14:30)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("when,expected", [
    (datetime(2026, 7, 22, 8, 0, tzinfo=CAIRO), MarketPhase.PRE_SESSION),
    (datetime(2026, 7, 22, 10, 0, tzinfo=CAIRO), MarketPhase.CONTINUOUS),
    (datetime(2026, 7, 22, 12, 0, tzinfo=CAIRO), MarketPhase.CONTINUOUS),
    (datetime(2026, 7, 24, 12, 0, tzinfo=CAIRO), MarketPhase.WEEKEND),   # Friday
])
def test_resolve_market_phase_basic(when, expected):
    assert resolve_market_phase(when) == expected


@pytest.mark.parametrize("when,expected", [
    (datetime(2026, 7, 22, 14, 14, 59, tzinfo=CAIRO), MarketPhase.CONTINUOUS),
    (datetime(2026, 7, 22, 14, 15, 0, tzinfo=CAIRO), MarketPhase.CLOSING_AUCTION),
    (datetime(2026, 7, 22, 14, 24, 59, tzinfo=CAIRO), MarketPhase.CLOSING_AUCTION),
    (datetime(2026, 7, 22, 14, 25, 0, tzinfo=CAIRO), MarketPhase.CLOSED),
    (datetime(2026, 7, 22, 14, 30, 0, tzinfo=CAIRO), MarketPhase.CLOSED),
])
def test_resolve_market_phase_auction_boundaries(when, expected):
    # The 14:25-14:30 tail is CLOSED, never CONTINUOUS; the auction stays distinct.
    assert resolve_market_phase(when) == expected


# --------------------------------------------------------------------------- #
# Fixture conformance + JSON serialization
# --------------------------------------------------------------------------- #

def test_fixture_matches_typed_contract():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    ar = data["analysis_result"]
    # Each fixture sub-object must construct its dataclass exactly (no stray/missing keys).
    PriceSummary(**ar["price"])
    IndicatorSummary(**ar["indicators"])
    for lv in ar["key_levels"]:
        KeyLevel(**lv)
    for sc in ar["scenarios"]:
        ScenarioResult(**{**sc, "state": ScenarioState(sc["state"])})
    for comp in ar["confidence"]["components"]:
        ConfidenceComponent(**comp)
    # Spot-check the corrected fields are present and SMA/EMA are separate.
    ind = ar["indicators"]
    assert ind["sma_20"] != ind["ema_20"]
    assert {"macd", "macd_signal", "macd_histogram", "obv", "volume_ratio",
            "expected_range_position", "turnover", "volume_safe"} <= set(ind)
    assert {"trigger", "entry_low", "entry_high", "target", "stop",
            "remaining_room_percent", "risk_reward", "confidence",
            "confirmation_requirements", "invalidation_conditions"} <= set(ar["scenarios"][0])


def test_full_result_is_json_serializable():
    resp = analyze_symbol("up", history_loader=lambda s: _uptrend(),
                          live_quote_provider=lambda s: {
                              "available": True, "last": 89.2,
                              "quote_timestamp": "2026-07-22T13:39:58+03:00"},
                          now=datetime(2026, 7, 22, 12, 0, tzinfo=CAIRO))
    blob = json.dumps({"result": asdict(resp.result),
                       "narrative": asdict(resp.narrative)}, default=str,
                      ensure_ascii=False)
    assert len(blob) > 0
    # Round-trips back to a dict with the typed indicator fields.
    parsed = json.loads(blob)
    assert "ema_20" in parsed["result"]["indicators"]
    assert "macd_histogram" in parsed["result"]["indicators"]
