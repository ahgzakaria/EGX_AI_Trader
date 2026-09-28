"""The chart-pattern study: detectors that see no future, and a verdict fixed first.

`scripts/research/chart_patterns.py` measures classic chart patterns against
the market and against a plain breakout. Its detectors carry many parameters, so
the two things that could make its answer wrong without anyone noticing are
pinned here: a detector reading a bar after its own trigger, and the survival
rule bending after the result.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.research import chart_patterns as study


def frame(closes, spread=0.005):
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "Open": closes, "Close": closes,
        "High": closes * (1 + spread), "Low": closes * (1 - spread),
        "Volume": np.full(len(closes), 1000.0),
    })


def random_walk(n=600, seed=7):
    rng = np.random.default_rng(seed)
    return 20 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))


# --- no look-ahead --------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(study.DETECTORS))
@pytest.mark.parametrize("seed", [1, 7, 42])
def test_no_detector_reads_a_bar_after_its_trigger(name, seed):
    """A trigger on day t must not depend on anything after t: cutting the
    series at t leaves every trigger up to t unchanged."""
    full = frame(random_walk(seed=seed))
    detector = study.DETECTORS[name]
    fired = detector(full)
    for t in range(60, len(full), 37):
        cut = detector(full.iloc[:t + 1].reset_index(drop=True))
        assert (cut == fired[:t + 1]).all(), f"{name} changed its past at t={t}"


def test_a_swing_point_is_the_strict_extreme_of_its_neighbourhood():
    values = [5, 4, 3, 2, 3, 4, 5, 6, 7, 6, 5, 4, 5]
    assert study.swing_points(values, "low", k=3) == [3]
    assert study.swing_points(values, "high", k=3) == [8]


def test_a_flat_run_is_not_a_swing_point():
    assert study.swing_points([5, 4, 3, 3, 3, 4, 5], "low", k=2) == []


# --- each detector finds its shape ----------------------------------------------

def double_bottom_shape():
    fall = np.linspace(30, 20, 60)
    rally = np.linspace(20, 23, 12)[1:]
    back = np.linspace(23, 20.2, 12)[1:]
    rise = np.linspace(20.2, 24, 15)[1:]
    return np.concatenate([np.full(60, 30), fall, rally, back, rise])


def test_a_double_bottom_fires_on_the_neckline_break_and_only_there():
    closes = double_bottom_shape()
    fired = study.double_bottom(frame(closes))
    assert fired.sum() == 1
    t = int(np.argmax(fired))
    assert closes[t] > 23 * 1.005 >= closes[t - 1], "the first close above the rally's high"


def test_a_second_low_well_below_the_first_is_not_a_double_bottom():
    closes = double_bottom_shape()
    closes[120:140] = np.minimum(closes[120:140], 17.0)
    assert study.double_bottom(frame(closes)).sum() == 0


def test_a_bull_flag_fires_when_price_leaves_the_flag():
    closes = np.concatenate([np.full(40, 10.0), np.linspace(10, 12, 10),
                             np.full(10, 11.9), [12.4, 12.5]])
    fired = study.bull_flag(frame(closes, spread=0.002))
    assert fired.sum() == 1
    assert closes[int(np.argmax(fired))] == 12.4


def test_a_flag_as_deep_as_its_pole_is_not_a_flag():
    closes = np.concatenate([np.full(40, 10.0), np.linspace(10, 12, 10),
                             np.linspace(12, 10.2, 10), [12.4]])
    assert study.bull_flag(frame(closes, spread=0.002)).sum() == 0


def test_the_plain_breakout_fires_on_the_first_close_through_only():
    closes = np.concatenate([np.full(30, 10.0), [10.5, 10.6, 10.7]])
    fired = study.plain_breakout_20(frame(closes, spread=0.0))
    assert fired.tolist().count(True) == 1 and fired[30]


# --- the verdict was registered before the result -----------------------------------

def stats(before, after):
    return {"<": before, ">=": after}


def test_a_bullish_pattern_must_also_beat_the_plain_breakout():
    baseline = stats((500, 1.0, 5.0), (500, 1.5, 5.0))
    beats_market_not_breakout = stats((100, 0.8, 3.0), (100, 1.2, 3.0))
    assert study.verdict("bull_flag", beats_market_not_breakout, baseline).startswith(
        "does not beat the plain breakout")
    beats_both = stats((100, 1.4, 3.0), (100, 1.9, 3.0))
    assert study.verdict("bull_flag", beats_both, baseline) == "SURVIVES"


def test_one_era_is_not_enough():
    baseline = stats((500, 0.1, 1.0), (500, 0.1, 1.0))
    assert study.verdict("double_bottom", stats((100, 2.0, 4.0), (100, -0.5, -1.0)),
                         baseline) == "does not beat the market in both eras"
    assert study.verdict("double_bottom", stats((100, 2.0, 4.0), (100, 0.5, 1.2)),
                         baseline).startswith("t below")


def test_a_bearish_pattern_survives_only_by_lagging_the_market():
    assert study.verdict("double_top", stats((80, -1.0, -3.0), (80, -0.8, -2.5)),
                         None) == "SURVIVES"
    assert study.verdict("double_top", stats((80, -1.0, -3.0), (80, 0.2, 0.5)),
                         None) == "does not lag the market in both eras"


def test_too_few_events_decides_nothing():
    assert study.verdict("bull_flag", stats((10, 5.0, 9.0), (500, 5.0, 9.0)),
                         None) == "too few events"


def test_the_registered_constants():
    assert study.PRIMARY_HOLD == 20 and study.MIN_EVENTS == 40 and study.MIN_T == 2.0
    assert set(study.BULLISH) == {"bull_flag", "ascending_triangle", "double_bottom",
                                  "inverse_head_shoulders"}
    assert set(study.BEARISH) == {"double_top", "head_shoulders_top"}


def test_a_zero_close_is_missing_not_a_price():
    """The frozen record holds 13 of them; one put an infinite forward return
    into the base and left a whole era's lift undefined."""
    panel = pd.DataFrame({"Symbol": ["A"] * 3, "Date": pd.date_range("2020-01-01", periods=3),
                          "Open": [10.0, 0.0, 10.0], "High": [10.5, 0.0, 10.5],
                          "Low": [9.5, 0.0, 9.5], "Close": [10.0, 0.0, 10.2],
                          "Volume": [100.0, 100.0, 100.0]})
    cleaned = study.without_zero_prices(panel)
    assert cleaned["Close"].isna().tolist() == [False, True, False]
    assert len(cleaned) == 3, "the row stays, so the hold is still counted in sessions"
    assert cleaned["Volume"].tolist() == [100.0] * 3
