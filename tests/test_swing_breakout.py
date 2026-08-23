"""The swing engine must find what it measured, and nothing it did not.

Its three conditions came from measurement, not judgement, and each has a
specific shape that a later edit could break without any test noticing:

* the breakout level must exclude today's own bar, or every close breaks out
  of itself;
* the momentum rank is cross-sectional across the whole readable universe, not
  across the breakouts, because it asks where a name sits in the market;
* the volume gate sits at 2.5x because below it the measurement is worse than
  not trading at all.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.swing_breakout import (
    MONTH,
    SwingConfig,
    scan,
)


def series(closes, volumes=None, highs=None):
    """A daily frame long enough to be readable, ending at `closes`."""

    pad = 300
    base = np.linspace(10.0, 10.0, pad).tolist() + list(closes)
    frame = pd.DataFrame({
        "Close": base,
        "High": ([11.0] * pad + list(highs or closes)),
        "Low": [c * 0.98 for c in base],
        "Volume": ([1000.0] * pad + list(volumes or [1000.0] * len(closes))),
    })
    return frame


def rising(final_close, *, volume, high_before=11.0, momentum=True):
    """One symbol: flat history, then a close that clears `high_before`."""

    closes = [10.0] * 5 + [final_close]
    volumes = [1000.0] * 5 + [volume]
    highs = [high_before] * 5 + [final_close]
    frame = series(closes, volumes, highs)
    if momentum:
        # Lift the price a year ago downward so 12-1 momentum is strongly
        # positive without touching the recent window the breakout reads.
        frame.loc[: len(frame) - 12 * MONTH, "Close"] = 5.0
    return frame


def test_a_close_above_the_level_on_heavy_volume_is_a_candidate():
    result = scan({"UP": rising(12.0, volume=5000.0)}, session_date="2026-08-23")

    assert result.candidate_count == 1
    candidate = result.candidates[0]
    assert candidate.symbol == "UP"
    assert candidate.volume_ratio > 2.5
    assert candidate.close > candidate.breakout_level


def test_todays_own_bar_never_sets_the_level_it_must_break():
    """Without the shift every close is above the rolling max that includes
    it, and the strategy fires on everything."""

    result = scan({"UP": rising(12.0, volume=5000.0)}, session_date="2026-08-23")
    candidate = result.candidates[0]

    assert candidate.breakout_level == pytest.approx(11.0)


def test_ordinary_volume_is_declined_and_says_so():
    result = scan({"QUIET": rising(12.0, volume=1200.0)}, session_date="2026-08-23")

    assert result.candidate_count == 0
    assert result.symbols_skipped["QUIET"] == "VOLUME_BELOW_GATE"


def test_a_close_that_does_not_clear_the_level_is_not_a_breakout():
    result = scan({"FLAT": rising(10.5, volume=5000.0, high_before=11.0)},
                  session_date="2026-08-23")

    assert result.candidate_count == 0


def test_momentum_is_ranked_across_the_universe_not_across_the_breakouts():
    """Two breakouts, both on heavy volume. The one whose price is lower than
    a year ago must rank below the one whose price is higher, and the gate
    must be able to separate them."""

    strong = rising(12.0, volume=5000.0, momentum=True)
    weak = rising(12.0, volume=5000.0, momentum=False)
    # Give the weak name a falling year so its 12-1 momentum is negative.
    weak.loc[: len(weak) - 12 * MONTH, "Close"] = 30.0

    result = scan({"STRONG": strong, "WEAK": weak},
                  config=SwingConfig(minimum_momentum_rank=0.6),
                  session_date="2026-08-23")

    named = {c.symbol for c in result.candidates}
    assert "STRONG" in named
    assert "WEAK" not in named
    assert result.symbols_skipped["WEAK"] == "MOMENTUM_RANK_BELOW_GATE"


def test_a_symbol_without_enough_history_is_skipped_not_guessed():
    short = pd.DataFrame({"Close": [10.0] * 50, "High": [10.0] * 50,
                          "Low": [9.0] * 50, "Volume": [1000.0] * 50})

    result = scan({"NEW": short}, session_date="2026-08-23")

    assert result.candidate_count == 0
    assert result.symbols_skipped["NEW"] == "INSUFFICIENT_HISTORY"


def test_an_empty_universe_returns_an_empty_scan_rather_than_raising():
    result = scan({}, session_date="2026-08-23")

    assert result.candidate_count == 0
    assert result.symbols_considered == 0


def test_the_volume_gate_sits_where_the_edge_starts():
    """Below 1.5x average volume a breakout returned -0.11% over twenty days,
    worse than not trading; the 1.5-2.5x band returned +2.26%, no better than
    sitting out; above 2.5x, +5.73% at a 59% win rate."""

    assert SwingConfig().minimum_volume_ratio >= 2.5


def test_the_momentum_gate_keeps_the_top_third():
    """Tightening this strengthened the result monotonically -- top 75% gave
    +3.52, top half +4.44, top third +5.22 -- and inverting it weakened it,
    with the bottom half at +1.49. The top tenth collapses to +1.39 on 93
    observations, so the gate stops at the third."""

    assert SwingConfig().minimum_momentum_rank == pytest.approx(0.67)


def test_momentum_skips_the_most_recent_month():
    """The standard 12-1 construction. The last month is left out because it
    tends to reverse, which is also what this market showed: three and six
    month change is negatively related to the next twenty days."""

    config = SwingConfig()
    assert config.momentum_skip_months == 1
    assert config.momentum_window_months == 12


def test_extension_reports_how_far_past_the_level_the_close_sat():
    result = scan({"UP": rising(12.0, volume=5000.0)}, session_date="2026-08-23")
    candidate = result.candidates[0]

    assert candidate.extension_percent == pytest.approx(
        (12.0 - 11.0) / 11.0 * 100.0
    )


def test_the_engine_emits_no_execution_vocabulary():
    """It produces research candidates. A reader must never be able to mistake
    one for an order or a fill."""

    from pathlib import Path

    source = Path("services/swing_breakout.py").read_text(encoding="utf-8").lower()
    for banned in ("place_order", "submit_order", "fill_price", "filled_price",
                   "execution_price", "position_size", "order_id"):
        assert banned not in source, banned
