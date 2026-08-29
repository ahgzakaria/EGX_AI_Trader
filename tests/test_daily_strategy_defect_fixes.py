"""Three defects the Daily Dashboard strategy carried, and their fixes.

All three were diagnosed in `docs/audits/strategies/DAILY_STRATEGY_DIAGNOSIS.md`
without being touched, because none of them is why the strategy underperforms.
They were fixed afterwards on their own merits, and these tests exist so that
none can come back quietly:

* a gate switched on in the settings, unable to fire, reporting PASS;
* two definitions of the same level, consulted in the same decision;
* prices rounded to a precision this exchange does not quote.

The first two are provably neutral on trade selection and the tests say so; the
third is not, and its cost is recorded in the audit rather than hidden here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy.entry import PRICE_PRECISION, entry_signal
from strategy.support import support_resistance


def frame(highs, lows=None, closes=None, volumes=None, atr=0.05):
    n = len(highs)
    closes = closes if closes is not None else list(highs)
    lows = lows if lows is not None else [h * 0.97 for h in highs]
    volumes = volumes if volumes is not None else [1_000_000.0] * n
    return pd.DataFrame({
        "Open": [closes[0]] + list(closes[:-1]),
        "High": [float(h) for h in highs],
        "Low": [float(x) for x in lows],
        "Close": [float(c) for c in closes],
        "Volume": [float(v) for v in volumes],
        "ATR": [atr] * n,
    }, index=pd.date_range("2025-01-01", periods=n, freq="B"))


# ---------------------------------------------------------------------------
# One definition of resistance, not two
# ---------------------------------------------------------------------------

def test_resistance_excludes_today_in_both_places_that_compute_it():
    """`entry.py` places the targets on it; `quality_filter` measures room to it.

    They disagreed on 22.4% of real bars. Today's own high is not overhead
    supply -- it is a level no seller has yet defended -- and including it let a
    bar that printed a new high raise the ceiling it was then measured against.
    """
    # A flat band, then today prints a much higher high.
    highs = [10.0] * 25 + [15.0]
    data = frame(highs, closes=[10.0] * 25 + [11.0])
    last = len(highs) - 1

    from_support = support_resistance(data, last)["resistance"]
    from_entry = entry_signal(data, last)["Target1"]

    # The prior band's high, not today's 15.0.
    assert from_support == pytest.approx(10.0)
    assert from_entry == pytest.approx(10.0)
    assert from_support == from_entry


def test_a_new_high_today_cannot_raise_its_own_ceiling():
    """The self-referential half of the bug, isolated."""
    band = [10.0] * 25
    quiet = support_resistance(frame(band + [10.0]), len(band))["resistance"]
    spike = support_resistance(frame(band + [99.0]), len(band))["resistance"]

    assert quiet == spike == pytest.approx(10.0)


def test_the_first_bar_still_has_a_resistance():
    """`i = 0` has no prior window; it must not raise or return NaN."""
    value = support_resistance(frame([10.0]), 0)["resistance"]

    assert value == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Prices at the precision this exchange actually quotes
# ---------------------------------------------------------------------------

def test_prices_are_rounded_to_three_decimals_not_two():
    """EGX quotes low-priced stocks in thousandths.

    Across the universe's own history only 24% of closes under 1 EGP sit
    exactly on two decimals, against 71% on three. Rounding to two moved the
    sixteen universe names under 1 EGP by a mean of 2.31% of price, and `RR` is
    a quotient of two rounded differences, so the error compounded.
    """
    assert PRICE_PRECISION == 3

    # A sub-1-EGP name, where the old rounding did the damage.
    highs = [0.6] * 25 + [0.6]
    data = frame(highs, lows=[0.5432] * 26, closes=[0.5678] * 26, atr=0.004)
    signal = entry_signal(data, 25)

    for field in ("BuyLow", "BuyHigh", "StopLoss", "Target1", "Target2"):
        value = signal[field]
        assert value == pytest.approx(round(value, 3)), field
    # Two decimals would have flattened the entry to 0.57.
    assert signal["BuyHigh"] == pytest.approx(0.568)


def test_the_reward_ratio_stays_a_ratio():
    """`RR` is not a quote, so it keeps two decimals.

    A bulk edit of this module's rounding caught it by shape once; that is the
    kind of change worth a test rather than a memory.
    """
    highs = list(np.linspace(10.0, 12.0, 25)) + [11.0]
    data = frame(highs, closes=list(np.linspace(10.0, 12.0, 25)) + [11.0])
    signal = entry_signal(data, 25)

    assert signal["RR"] == pytest.approx(round(signal["RR"], 2))


# ---------------------------------------------------------------------------
# A gate that cannot fire says so
# ---------------------------------------------------------------------------

def test_an_unavailable_index_reports_unavailable_not_pass(monkeypatch):
    """`^CASE30` has one bar in the cache and neither live provider serves it.

    So `analyze` takes its fail-open branch on every bar in every mode, while
    `require_market_analyzer` reads true in the settings UI and the decision
    trace reported `MarketAnalyzer: PASS`. It allowed the trade and said the
    index had approved it.
    """
    from strategy import decision_engine

    monkeypatch.setattr(
        decision_engine, "analyze_market",
        lambda date, cfg: {"Passed": True, "Available": False,
                           "Regime": "UNKNOWN",
                           "Reasons": ["Market Analyzer unavailable (test)"]})

    verdict = decision_engine._market_analyzer_verdict(
        {"Passed": True, "Available": False, "Regime": "UNKNOWN"})

    assert verdict == "UNAVAILABLE"


def test_a_working_index_still_reports_pass_and_fail():
    from strategy.decision_engine import _market_analyzer_verdict

    assert _market_analyzer_verdict(
        {"Passed": True, "Available": True, "Regime": "BULL"}) == "PASS"
    assert _market_analyzer_verdict(
        {"Passed": False, "Available": True, "Regime": "BEAR"}) == "FAIL"


def test_a_caller_without_the_availability_field_is_unchanged():
    """Older callers and archived fixtures must not become UNAVAILABLE."""
    from strategy.decision_engine import _market_analyzer_verdict

    assert _market_analyzer_verdict({"Passed": True, "Regime": "BULL"}) == "PASS"
    assert _market_analyzer_verdict({"Passed": False, "Regime": "BEAR"}) == "FAIL"


def test_the_unavailable_reason_reaches_the_row(monkeypatch):
    """The explanation used to be discarded unless the gate failed.

    Which is exactly the case that never happens, so nothing anywhere said the
    index could not be read.
    """
    from strategy import decision_engine

    monkeypatch.setattr(
        decision_engine, "analyze_market",
        lambda date, cfg: {"Passed": True, "Available": False,
                           "Regime": "UNKNOWN",
                           "Reasons": ["Market Analyzer unavailable (test)"]})

    n = 260
    closes = list(np.linspace(8.0, 10.0, n))
    data = frame([c * 1.01 for c in closes], closes=closes)
    for column, value in (("EMA20", 9.5), ("EMA50", 9.0), ("EMA200", 8.0),
                          ("ADX", 30.0), ("RSI", 55.0), ("MACD", 0.2),
                          ("MACD_Signal", 0.1), ("BB_MIDDLE", 9.4),
                          ("BB_UPPER", 10.5), ("BB_LOWER", 8.5),
                          ("OBV", 1.0), ("VOLUME_RATIO", 1.2),
                          ("ATR_PERCENT", 2.0)):
        data[column] = value

    result = decision_engine.evaluate(data, n - 1)

    assert result["DecisionTrace"]["MarketAnalyzer"] == "UNAVAILABLE"
    assert result["IndexRegime"] == "UNKNOWN"
    assert any("Market Analyzer unavailable" in reason
               for reason in result["Reasons"])


# ---------------------------------------------------------------------------
# The summary says what not trading was worth
# ---------------------------------------------------------------------------

def test_the_summary_carries_a_benchmark():
    """No summary this project produced had one.

    The Daily Dashboard strategy reports profit factor 1.29 and CAGR 5.43% over
    a window in which the median EGX name returned 17.51% a year simply held.
    """
    from backtesting.statistics import BacktestStatistics
    from tests.test_equity_marks_open_positions import FakeTrade, price_frame

    trade = FakeTrade("AAA.CA", "2024-01-01", "2024-01-03", 10.0, 11.0, 100)
    prices = price_frame({
        "2024-01-01": {"AAA.CA": 10.0, "BBB.CA": 50.0},
        "2024-01-02": {"AAA.CA": 10.5, "BBB.CA": 55.0},
        "2024-01-03": {"AAA.CA": 11.0, "BBB.CA": 60.0},
    })

    summary = BacktestStatistics(
        [trade], initial_capital=100_000, prices=prices).summary()

    assert summary["BenchmarkReturn"] is not None
    assert summary["BenchmarkSymbols"] == 2
    assert summary["ExcessReturn"] == pytest.approx(
        round(summary["TotalReturn"] - summary["BenchmarkReturn"], 2))


def test_an_unmeasurable_benchmark_reports_unknown_not_zero():
    """An unmeasured benchmark is not a flat market.

    The same rule the annualised statistics already follow when the backtest
    span cannot be determined.
    """
    from backtesting.statistics import BacktestStatistics
    from tests.test_equity_marks_open_positions import FakeTrade

    trade = FakeTrade("AAA.CA", "2024-01-01", "2024-01-03", 10.0, 11.0, 100)

    summary = BacktestStatistics([trade], initial_capital=100_000).summary()

    assert summary["BenchmarkReturn"] is None
    assert summary["ExcessReturn"] is None
