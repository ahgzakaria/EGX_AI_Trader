"""Tests for Scalping V2 range detector + entry model (synthetic bars).

Synthetic data validates the geometry, no-look-ahead, dynamic stop/target,
cost-adjusted RR, and sparse-data rejection logic deterministically. Real-data
behavior (coverage on the live Rubix DB) is covered by the audit report, not
these unit tests.
"""

from datetime import datetime, timezone

import pandas as pd
import pytest

from scalping.range_config import RangeScalperConfig
from scalping.range_detector import RangeClass, detect_range
from scalping.range_entry import (
    EntryPattern,
    OpportunityStatus,
    evaluate_entry,
)


def _bars(prices, start="2026-07-20 10:00", tz="Africa/Cairo", volume=1000):
    """Build a 1-minute OHLCV frame from a list of close prices."""
    idx = pd.date_range(start=start, periods=len(prices), freq="1min", tz=tz)
    rows = []
    for p in prices:
        rows.append({"Open": p, "High": p + 0.05, "Low": p - 0.05,
                     "Close": p, "Volume": volume})
    return pd.DataFrame(rows, index=idx)


def _cfg(**over):
    base = RangeScalperConfig()
    if over:
        return RangeScalperConfig.from_mapping({**base.__dict__, **over})
    return base


# -- range detector ----------------------------------------------------------

def test_detect_range_computes_levels_and_position():
    # A clean oscillating range 100-102, currently near the low.
    prices = [101, 102, 101, 100, 101, 102, 101, 100, 101, 102,
              101, 100, 101, 102, 101, 100.2]
    levels = detect_range(_bars(prices), min_bars=10)
    assert levels.session_high == pytest.approx(102.05, abs=0.01)
    assert levels.session_low == pytest.approx(99.95, abs=0.01)
    assert levels.midpoint == pytest.approx(101.0, abs=0.05)
    # last ~100.2 is near the low -> low range position
    assert levels.range_position_percent < 40


def test_detect_range_rejects_too_few_bars():
    levels = detect_range(_bars([100, 101, 102]), min_bars=15)
    assert levels.classification == RangeClass.DATA_INSUFFICIENT
    assert "valid bars" in levels.reason


def test_detect_range_rejects_empty():
    levels = detect_range(pd.DataFrame(), min_bars=15)
    assert levels.classification == RangeClass.DATA_INSUFFICIENT


def test_detect_range_no_lookahead_uses_only_supplied_bars():
    # Levels must only reflect the bars passed in, not any later data.
    early = _bars([100, 101, 100, 101, 100, 101, 100, 101, 100, 101,
                   100, 101, 100, 101, 100.5])
    levels_early = detect_range(early, min_bars=10)
    # Adding a much higher bar later changes the high — proving it only sees input.
    extended = pd.concat([early, _bars([120], start="2026-07-20 10:20")])
    levels_late = detect_range(extended, min_bars=10)
    assert levels_late.session_high > levels_early.session_high


def test_trending_up_classification():
    prices = [100 + i * 0.5 for i in range(20)]  # steady strong uptrend
    levels = detect_range(_bars(prices), min_bars=10)
    assert levels.classification in {RangeClass.TRENDING_UP, RangeClass.BREAKOUT_IN_PROGRESS}


# -- entry model -------------------------------------------------------------

def test_lower_range_bounce_produces_long_with_targets():
    prices = [102, 101, 100, 101, 102, 101, 100, 101, 102, 101,
              100, 101, 102, 101, 100.3]
    levels = detect_range(_bars(prices), min_bars=10)
    opp = evaluate_entry("COMI.CA", levels, atr_value=0.1, config=_cfg(minimum_net_rr=0.1))
    assert opp.pattern == EntryPattern.LOWER_RANGE_BOUNCE.value
    assert opp.stop is not None and opp.stop < opp.entry_zone_high
    assert opp.target_1 is not None


def test_no_chase_rule_blocks_top_of_range():
    # Currently near the session high -> RANGE_CONSUMED, never a bounce BUY.
    prices = [100, 101, 100, 101, 100, 101, 100, 101, 100, 101,
              100, 101, 100, 101, 101.95]
    levels = detect_range(_bars(prices), min_bars=10)
    opp = evaluate_entry("COMI.CA", levels, atr_value=0.1, config=_cfg())
    assert opp.status == OpportunityStatus.RANGE_CONSUMED
    assert "no-chase" in opp.reason or "consumed" in opp.reason


def test_data_insufficient_propagates_to_opportunity():
    levels = detect_range(_bars([100, 101]), min_bars=15)
    opp = evaluate_entry("COMI.CA", levels, atr_value=0.1, config=_cfg())
    assert opp.status == OpportunityStatus.DATA_INSUFFICIENT


def test_net_rr_is_cost_adjusted():
    prices = [102, 101, 100, 101, 102, 101, 100, 101, 102, 101,
              100, 101, 102, 101, 100.3]
    levels = detect_range(_bars(prices), min_bars=10)
    # High cost should reduce net RR vs zero cost.
    cheap = evaluate_entry("X", levels, atr_value=0.1,
                           config=_cfg(commission_estimate=0.0, slippage_estimate=0.0,
                                       minimum_net_rr=0.01))
    pricey = evaluate_entry("X", levels, atr_value=0.1,
                            config=_cfg(commission_estimate=0.01, slippage_estimate=0.01,
                                        minimum_net_rr=0.01))
    assert cheap.net_rr_t2 is not None and pricey.net_rr_t2 is not None
    assert pricey.net_rr_t2 < cheap.net_rr_t2


def test_wait_when_net_rr_below_minimum():
    prices = [102, 101, 100, 101, 102, 101, 100, 101, 102, 101,
              100, 101, 102, 101, 100.3]
    levels = detect_range(_bars(prices), min_bars=10)
    opp = evaluate_entry("X", levels, atr_value=0.1, config=_cfg(minimum_net_rr=99.0))
    assert opp.status == OpportunityStatus.RANGE_BUY_WAIT


def test_config_loads_from_json_defaults():
    cfg = RangeScalperConfig.load()
    assert cfg.enabled is False
    assert cfg.mode == "PAPER_ONLY"
    assert 0 < cfg.minimum_data_coverage <= 1
    assert cfg.minimum_net_rr > 0
