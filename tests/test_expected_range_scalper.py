"""Tests for SCALPING V3 — EXPECTED_RANGE_SCALPER (isolated, synthetic/fast).

Covers the validation checklist: liquidity avg/median + one-day-spike, turnover,
volume consistency, ADR/ATR, 2% frequency, asymmetric expected range, percentile
ranges, range position, fixed target/stop, range-consumed / no-chase, stale
history preservation, spread/executability, no-look-ahead selection, closing-
auction separation, immutable paper signals, and isolation from other strategies.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from scalping_expected_range.config import ExpectedRangeConfig
from scalping_expected_range.expected_range import compute_expected_range
from scalping_expected_range.liquidity_model import (
    DATA_INSUFFICIENT,
    LIQUIDITY_TOO_LOW,
    LIQUIDITY_VALID,
    VOLUME_UNRELIABLE,
    compute_liquidity,
)
from scalping_expected_range.scenario_engine import (
    LiveQuote,
    RANGE_CONSUMED,
    SPREAD_TOO_WIDE,
    evaluate_scenarios,
    range_position_percent,
)
from scalping_expected_range.scoring import add_scores, default_rank
from scalping_expected_range.volatility_model import (
    CONSISTENT_HIGH_VOLATILITY,
    ONE_DAY_SPIKE,
    compute_volatility,
)

CFG = ExpectedRangeConfig.load()


def _daily(n=40, close=100.0, vol=1_000_000, rng=0.03, start="2026-05-01", seed=0):
    """Build a synthetic completed-daily OHLCV frame with a controlled range."""
    rs = np.random.RandomState(seed)
    dates = pd.bdate_range(start=start, periods=n)
    closes = close + np.cumsum(rs.normal(0, close * 0.002, n))
    rows = []
    for i, c in enumerate(closes):
        half = c * rng / 2
        rows.append({"Open": c, "High": c + half, "Low": c - half, "Close": c,
                     "Volume": vol})
    return pd.DataFrame(rows, index=dates)


# --- liquidity ---------------------------------------------------------------

def test_liquidity_avg_and_median_volume():
    daily = _daily(vol=1_000_000)
    prof = compute_liquidity(daily, CFG)
    assert prof.status == LIQUIDITY_VALID
    assert abs(prof.avg_volume_20 - 1_000_000) < 1
    assert abs(prof.median_volume_20 - 1_000_000) < 1


def test_one_exceptional_session_does_not_make_illiquid_stock_liquid():
    # 19 tiny sessions + 1 huge session -> mean inflated, median stays tiny.
    daily = _daily(n=20, vol=1_000)
    daily.iloc[-1, daily.columns.get_loc("Volume")] = 50_000_000
    prof = compute_liquidity(daily, CFG)
    assert prof.median_volume_20 <= 2_000            # median unaffected by the spike
    assert prof.status in (LIQUIDITY_TOO_LOW, VOLUME_UNRELIABLE)   # not promoted


def test_low_turnover_fails_hard_gate():
    daily = _daily(vol=100, close=1.0)               # tiny turnover
    prof = compute_liquidity(daily, CFG)
    assert prof.status == LIQUIDITY_TOO_LOW
    assert any("TURNOVER" in r or "VOLUME" in r for r in prof.reasons)


def test_insufficient_history_flagged():
    prof = compute_liquidity(_daily(n=8), CFG)
    assert prof.status == DATA_INSUFFICIENT


def test_zero_volume_sessions_unreliable():
    daily = _daily(n=25, vol=1_000_000)
    daily.iloc[-4:, daily.columns.get_loc("Volume")] = 0
    prof = compute_liquidity(daily, CFG)
    assert prof.status == VOLUME_UNRELIABLE
    assert prof.zero_volume_sessions >= 3


# --- volatility --------------------------------------------------------------

def test_adr_and_two_percent_frequency():
    daily = _daily(rng=0.03)                          # ~3% range every session
    prof = compute_volatility(daily, CFG)
    assert prof.adr_percent_20 is not None and prof.adr_percent_20 > 2.5
    assert prof.target_2pct_frequency >= 0.9          # nearly always >= 2%
    assert prof.atr_percent_14 is not None


def test_consistent_high_volatility_classification():
    prof = compute_volatility(_daily(rng=0.03), CFG)
    assert prof.classification == CONSISTENT_HIGH_VOLATILITY


def test_one_day_spike_not_classified_as_high_volatility():
    daily = _daily(n=25, rng=0.005)                   # flat ~0.5% range
    # one abnormal 12% range session
    c = float(daily["Close"].iloc[-1])
    daily.iloc[-1, daily.columns.get_loc("High")] = c * 1.06
    daily.iloc[-1, daily.columns.get_loc("Low")] = c * 0.94
    prof = compute_volatility(daily, CFG)
    assert prof.classification in (ONE_DAY_SPIKE, "NORMAL_VOLATILITY", "INSUFFICIENT_VOLATILITY")
    assert prof.classification != CONSISTENT_HIGH_VOLATILITY


# --- expected range ----------------------------------------------------------

def test_expected_range_can_be_asymmetric():
    # Bigger downside excursions than upside -> asymmetric band.
    daily = _daily(n=40, rng=0.02)
    prev = daily["Close"].shift(1)
    daily["Low"] = prev * 0.95     # deep lows
    daily["High"] = prev * 1.01    # shallow highs
    daily = daily.dropna()
    er = compute_expected_range(daily, CFG)
    assert er.base is not None
    assert er.base.expected_downside_percent > er.base.expected_upside_percent


def test_percentile_bands_ordered():
    er = compute_expected_range(_daily(n=40, rng=0.03), CFG)
    assert er.conservative.expected_width_percent <= er.high_volatility.expected_width_percent


# --- range position + fixed target ------------------------------------------

def test_range_position_percent_math():
    assert range_position_percent(105, 100, 110) == 50.0
    assert range_position_percent(100, 100, 110) == 0.0
    assert range_position_percent(115, 100, 110) == 150.0     # extension above range


def test_fixed_two_percent_target_and_stop():
    daily = _daily(n=40, rng=0.04, close=100.0)
    from scalping_expected_range.historical_selector import SymbolAnalysis, Provenance
    analysis = SymbolAnalysis(
        "X", 100.0, compute_liquidity(daily, CFG), compute_volatility(daily, CFG),
        compute_expected_range(daily, CFG, prev_close=100.0),
        Provenance(data_status="OK"))
    live = LiveQuote(last=100.0, bid=99.9, ask=100.1, spread_percent=0.2,
                     quote_age_seconds=5, session_low=99.0, available=True)
    ev = evaluate_scenarios(analysis, live, CFG)
    cont = next(s for s in ev.scenarios if s.name == "TREND_CONTINUATION_INSIDE_RANGE")
    assert cont.take_profit == pytest.approx(102.0, abs=1e-6)   # +2%
    assert cont.stop_loss == pytest.approx(98.0, abs=1e-6)      # -2%


def test_range_consumed_triggers_no_chase():
    # Expected high ~ +2%, but price already at +2.5% -> no room -> RANGE_CONSUMED.
    daily = _daily(n=40, rng=0.02, close=100.0)
    from scalping_expected_range.historical_selector import SymbolAnalysis, Provenance
    er = compute_expected_range(daily, CFG, prev_close=100.0)
    analysis = SymbolAnalysis("X", 100.0, compute_liquidity(daily, CFG),
                              compute_volatility(daily, CFG), er, Provenance(data_status="OK"))
    high = er.high_volatility.expected_high
    live = LiveQuote(last=high * 1.001, bid=high * 0.999, ask=high * 1.001,
                     spread_percent=0.1, quote_age_seconds=5, available=True)
    ev = evaluate_scenarios(analysis, live, CFG)
    assert ev.advisory == RANGE_CONSUMED


def test_wide_spread_rejected():
    daily = _daily(n=40, rng=0.04, close=100.0)
    from scalping_expected_range.historical_selector import SymbolAnalysis, Provenance
    analysis = SymbolAnalysis("X", 100.0, compute_liquidity(daily, CFG),
                              compute_volatility(daily, CFG),
                              compute_expected_range(daily, CFG, prev_close=100.0),
                              Provenance(data_status="OK"))
    live = LiveQuote(last=100.0, bid=99.0, ask=101.0, spread_percent=2.0,
                     quote_age_seconds=5, available=True)
    ev = evaluate_scenarios(analysis, live, CFG)
    assert ev.advisory == SPREAD_TOO_WIDE


# --- scoring: liquidity-first, volatility cannot rescue illiquid -------------

def test_volatility_does_not_promote_illiquid_symbol():
    frame = pd.DataFrame([
        {"Symbol": "LIQUID", "liq_avg_volume_20": 5_000_000, "liq_avg_turnover_egp_20": 5e7,
         "vol_adr_percent_20": 2.0, "vol_target_2pct_frequency": 0.6,
         "liq_volume_consistency": 0.9, "liq_status": LIQUIDITY_VALID, "live_spread_percent": None},
        {"Symbol": "VOLATILE_ILLIQUID", "liq_avg_volume_20": 500, "liq_avg_turnover_egp_20": 5000,
         "vol_adr_percent_20": 9.0, "vol_target_2pct_frequency": 0.99,
         "liq_volume_consistency": 0.2, "liq_status": LIQUIDITY_TOO_LOW, "live_spread_percent": None},
    ])
    scored = add_scores(frame, CFG)
    ranked = default_rank(scored)
    assert ranked.iloc[0]["Symbol"] == "LIQUID"                  # liquid ranks first
    illiquid = scored[scored["Symbol"] == "VOLATILE_ILLIQUID"].iloc[0]
    assert illiquid["EXPECTED_RANGE_SCALPING_SCORE"] == 0.0      # gated to zero
    assert illiquid["TradableCandidate"] == False                # excluded from candidates


# --- Phase 4/6: penny-share raw volume does not top the tradable list --------

def test_default_rank_is_score_first_not_raw_volume():
    frame = pd.DataFrame([
        # penny share: enormous raw volume but modest score
        {"Symbol": "PENNY", "liq_avg_volume_20": 500_000_000, "liq_avg_turnover_egp_20": 8e7,
         "liq_avg_traded_price": 0.16, "vol_adr_percent_20": 2.0,
         "vol_target_2pct_frequency": 0.5, "liq_volume_consistency": 0.8,
         "liq_status": LIQUIDITY_VALID, "live_spread_percent": None},
        # balanced name: less raw volume, higher composite score
        {"Symbol": "BALANCED", "liq_avg_volume_20": 20_000_000, "liq_avg_turnover_egp_20": 2e8,
         "liq_avg_traded_price": 10.0, "vol_adr_percent_20": 6.0,
         "vol_target_2pct_frequency": 0.9, "liq_volume_consistency": 0.95,
         "liq_status": LIQUIDITY_VALID, "live_spread_percent": None},
    ])
    ranked = default_rank(add_scores(frame, CFG))
    assert ranked.iloc[0]["Symbol"] == "BALANCED"     # score-first, not raw volume


def test_tradability_audit_flags_penny_and_single_session():
    from scalping_expected_range.audit import build_tradability_audit
    frame = pd.DataFrame([
        {"Symbol": "PENNY", "TradableCandidate": True, "liq_status": LIQUIDITY_VALID,
         "liq_avg_volume_20": 500_000_000, "liq_median_volume_20": 4e8,
         "liq_avg_turnover_egp_20": 8e7, "liq_median_turnover_egp_20": 7e7,
         "liq_volume_consistency": 0.8, "liq_turnover_consistency": 0.7,
         "liq_avg_traded_price": 0.16, "live_spread_percent": 0.3,
         "liq_low_volume_sessions": 2, "liq_one_session_dominance": 0.2,
         "liq_turnover_one_session_dominance": 0.55, "EXPECTED_RANGE_SCALPING_SCORE": 70},
        {"Symbol": "NORMAL", "TradableCandidate": True, "liq_status": LIQUIDITY_VALID,
         "liq_avg_volume_20": 2_000_000, "liq_median_volume_20": 1.9e6,
         "liq_avg_turnover_egp_20": 4e7, "liq_median_turnover_egp_20": 3.8e7,
         "liq_volume_consistency": 0.9, "liq_turnover_consistency": 0.85,
         "liq_avg_traded_price": 20.0, "live_spread_percent": 0.2,
         "liq_low_volume_sessions": 1, "liq_one_session_dominance": 0.15,
         "liq_turnover_one_session_dominance": 0.2, "EXPECTED_RANGE_SCALPING_SCORE": 65},
    ])
    audit = build_tradability_audit(frame)
    penny = audit[audit["Symbol"] == "PENNY"].iloc[0]
    assert bool(penny["PennyShareFlag"]) is True
    assert bool(penny["SingleSessionInflatedFlag"]) is True


def test_score_sensitivity_volatility_cannot_rescue_liquidity():
    from scalping_expected_range.audit import build_score_sensitivity
    frame = pd.DataFrame([
        {"Symbol": "OK", "TradableCandidate": True, "liq_status": LIQUIDITY_VALID,
         "liq_avg_volume_20": 5e6, "liq_avg_turnover_egp_20": 5e7,
         "vol_adr_percent_20": 3.0, "liq_one_session_dominance": 0.2,
         "EXPECTED_RANGE_SCALPING_SCORE": 60},
        {"Symbol": "GATED", "TradableCandidate": False, "liq_status": LIQUIDITY_TOO_LOW,
         "liq_avg_volume_20": 100, "liq_avg_turnover_egp_20": 1000,
         "vol_adr_percent_20": 9.0, "liq_one_session_dominance": 0.9,
         "EXPECTED_RANGE_SCALPING_SCORE": 0.0},
    ])
    sens = build_score_sensitivity(frame, CFG)
    rescued = sens.loc[sens["Metric"] == "Nontradable_with_positive_score", "Value"].iloc[0]
    assert int(rescued) == 0


# --- stale history preserved, never converted to AVOID -----------------------

def test_stale_history_preserved_with_warning(tmp_path):
    from scalping_expected_range.historical_selector import HistoricalSelector

    class _Cache:
        def load_cached(self, provider, symbol, period, interval, allow_expired=False):
            df = _daily(n=40, rng=0.03)
            df.index = pd.bdate_range(end="2026-07-10", periods=len(df))   # old data
            return df

    # 'now' far after the last candle so trading_session_lag > 0.
    now = datetime(2026, 7, 22, 16, 0, tzinfo=timezone.utc)
    sel = HistoricalSelector(config=CFG, cache=_Cache(), now=now)
    analysis = sel.analyze("STALE")
    assert analysis.provenance.data_status == "DATA_STALE"
    assert analysis.provenance.data_age_sessions > 0
    assert analysis.expected.base is not None      # last calculated range preserved


# --- no-look-ahead cleaning: incomplete/forward-filled dropped ---------------

def test_cleaning_drops_incomplete_and_forward_filled(tmp_path):
    from scalping_expected_range.historical_selector import HistoricalSelector

    class _Cache:
        def load_cached(self, provider, symbol, period, interval, allow_expired=False):
            df = _daily(n=30, rng=0.03)
            df.index = pd.bdate_range(end="2026-07-20", periods=len(df))
            # inject a forward-filled flat zero-volume row
            df.iloc[-2] = {"Open": df["Close"].iloc[-2], "High": df["Close"].iloc[-2],
                           "Low": df["Close"].iloc[-2], "Close": df["Close"].iloc[-2], "Volume": 0}
            return df

    now = datetime(2026, 7, 21, 16, 0, tzinfo=timezone.utc)
    sel = HistoricalSelector(config=CFG, cache=_Cache(), now=now)
    daily, prov = sel._load_clean_daily("X")
    assert prov.rows_dropped >= 1        # the forward-filled flat row removed
    # no zero-volume row survives
    assert (pd.to_numeric(daily["Volume"], errors="coerce") > 0).all()


# --- closing-auction separation (session config lives in the OTHER package) --

def test_closing_auction_config_untouched():
    # The Range Scalper's continuous/auction phase config must remain intact and
    # separate. This strategy never mixes auction movement into daily selection.
    import json
    cfg = json.loads(open("scalping/range_scalper_settings.json", encoding="utf-8").read())
    assert cfg["continuous_trading_close"] == "14:15"
    assert cfg["closing_auction_end"] == "14:25"
    assert cfg["event_gate_enabled"] is False


# --- immutable paper signals -------------------------------------------------

def test_paper_signal_store_is_immutable(tmp_path):
    from scalping_expected_range.scanner import ImmutablePaperSignalStore
    store = ImmutablePaperSignalStore(str(tmp_path / "paper.csv"))
    row = {"Symbol": "X", "Scenario": "TREND_CONTINUATION_INSIDE_RANGE",
           "DecisionTime": "2026-07-22T11:00:00+03:00", "Entry": 100.0, "Advisory": "READY_CONTINUATION"}
    sh = store.record_signal(row, enabled=True)
    # duplicate record must not rewrite/duplicate the signal
    sh2 = store.record_signal(row, enabled=True)
    assert sh == sh2
    rows = store._existing()
    assert sum(1 for r in rows if r["RecordKind"] == "SIGNAL") == 1
    # outcome appended as a SEPARATE row; original untouched
    store.append_outcome(sh, {"Symbol": "X", "MinutesAfter": 5, "TargetHit": True})
    rows = store._existing()
    signal = next(r for r in rows if r["RecordKind"] == "SIGNAL")
    assert signal["Advisory"] == "READY_CONTINUATION"           # unchanged
    assert any(r["RecordKind"] == "OUTCOME" for r in rows)


def test_paper_recording_disabled_by_default(tmp_path):
    from scalping_expected_range.scanner import ImmutablePaperSignalStore
    store = ImmutablePaperSignalStore(str(tmp_path / "paper.csv"))
    sh = store.record_signal({"Symbol": "X", "Scenario": "S", "DecisionTime": "t", "Entry": 1},
                             enabled=False)
    assert sh is None                                            # nothing recorded


# --- isolation ---------------------------------------------------------------

def test_config_safety_state():
    # Paper forward recording is intentionally ENABLED; production and any form of
    # execution must remain OFF.
    assert CFG.production_enabled is False
    assert CFG.paper_enabled is True
    assert CFG.decision_support_only is True
    assert CFG.automatic_execution is False
    assert CFG.broker_orders_enabled is False
