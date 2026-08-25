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


# --- the page must show the basis, not just the names ------------------


def _rendered_page():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string(
        "from dashboard.swing_signals import _show_basis\n"
        "from services.swing_breakout import SwingConfig\n"
        "_show_basis(SwingConfig())\n"
    )
    app.run(timeout=30)
    assert not app.exception, app.exception
    return app


def _page_text(app):
    blocks = list(app.markdown) + list(app.caption) + list(app.warning) + list(app.info)
    return " ".join(
        b.value for b in blocks if isinstance(getattr(b, "value", None), str)
    )


def test_the_page_states_the_lift_and_the_benchmark_it_is_against():
    """An absolute return in a bull market says nothing. The page has to name
    what the strategy is being compared to."""

    text = _page_text(_rendered_page())

    assert "5.22" in text
    assert "owning every name" in text


def test_the_page_states_what_the_strategy_is_not():
    """Four of fourteen years lost money and the median trade returns 0.88%.
    A screen that shows only the average is showing the good half."""

    text = _page_text(_rendered_page())

    assert "0.88" in text
    assert "lost money" in text
    assert "not an order" in text


def test_the_page_records_what_the_value_test_actually_found():
    """Value is the strongest factor the frontier literature reports.

    It used to be absent here because EODHD returns 403 on fundamentals. Yahoo
    carries them for EGX, and annual equity read months after publication is
    untouched by the late-candle problem that retired Yahoo operationally -- so
    it was tested. It failed, and the page has to say so with its numbers
    rather than leave a reader thinking it is still an open question.
    """

    text = _page_text(_rendered_page())

    assert "Value was tested" in text
    # The two eras disagreeing is the finding, so both halves must be shown.
    assert "5.23" in text and "5.31" in text, "the training halves, which tie"
    assert "6.14" in text and "2.94" in text, "the validation halves, which do not"
    # Coverage settles it regardless of the averages.
    assert "23%" in text
    assert "value_factor.py" in text


def test_the_page_never_claims_value_is_untested():
    """The old text said fundamentals were unavailable. Leaving that in beside
    a result would be two answers to one question."""

    text = _page_text(_rendered_page())

    assert "could not be tested" not in text
    assert "not dismissed" not in text


def test_the_page_shows_that_the_momentum_filter_was_not_fitted():
    text = _page_text(_rendered_page())

    assert "monotonically" in text
    assert "1.49" in text


def test_the_universe_is_bounded_by_liquidity_not_by_a_count():
    """A count is arbitrary and goes stale; a floor scales with the position.

    Ranked by count, the 200th name on the exchange trades 20,000 EGP a day.
    A backtest can buy it and the operator cannot, so the 0.80% cost assumed
    for it is fiction and any lift measured on it is unclaimable.
    """
    import pandas as pd

    from services.swing_breakout import SwingConfig, most_traded

    def history(turnover):
        return pd.DataFrame({"close": [10.0] * 300,
                             "volume": [turnover / 10.0] * 300})

    config = SwingConfig()
    floor = config.minimum_daily_turnover_egp
    histories = {
        "LIQUID": history(floor * 4),
        "AT_THE_FLOOR": history(floor),
        "TOO_THIN": history(floor / 10),
    }

    kept = most_traded(histories)

    assert "LIQUID" in kept
    assert "AT_THE_FLOOR" in kept, "the floor is inclusive"
    assert "TOO_THIN" not in kept


def test_the_floor_keeps_the_position_under_two_percent_of_turnover():
    """The floor is not a round number: it is what a 100,000 EGP position can
    take without being the market."""
    from services.swing_breakout import SwingConfig

    position = 100_000.0
    floor = SwingConfig().minimum_daily_turnover_egp
    assert position / floor <= 0.02


def test_a_count_is_still_available_for_research():
    """The sweep that chose the floor needs a fixed population to sweep."""
    import pandas as pd

    from services.swing_breakout import most_traded

    def history(turnover):
        return pd.DataFrame({"close": [10.0] * 300,
                             "volume": [turnover / 10.0] * 300})

    histories = {f"S{i}": history(50_000_000 - i * 1_000_000) for i in range(20)}
    assert len(most_traded(histories, count=5)) == 5


def test_the_page_shows_what_the_widening_cost_and_bought():
    """More trades at a lower per-trade edge is a trade-off, not a free win,
    and the page has to show both sides of it."""
    text = _page_text(_rendered_page())

    assert "bounded by liquidity" in text
    assert "+277%" in text, "the annual lift the floor was chosen on"
    assert "20,000 EGP a day" in text, "why the count-based universe was fiction"
    assert "survivorship" in text


def test_a_breakout_below_the_long_trend_is_declined():
    """A breakout inside a long downtrend is a bounce in something still
    falling. Over 25 years those are 159 trades carrying a lift of -2.64%:
    not a smaller edge, a negative one."""
    frame = rising(12.0, volume=5000.0)
    # The high prices have to sit *inside* the 200-day window or they never
    # reach the average being tested. A first version of this put them outside
    # it and the test passed a name that was plainly above its own trend.
    frame.loc[len(frame) - 190 : len(frame) - 10, "Close"] = 60.0

    result = scan({"FALLING": frame}, session_date="2026-08-25")

    assert result.candidate_count == 0
    assert result.symbols_skipped["FALLING"] == "BELOW_LONG_TREND"


def test_a_breakout_above_the_long_trend_still_passes():
    result = scan({"UP": rising(12.0, volume=5000.0)}, session_date="2026-08-25")
    assert result.candidate_count == 1


def test_the_long_trend_gate_is_the_names_own_not_the_markets():
    """A market-regime filter was measured and does nothing: it cuts the
    return while leaving the worst fall at -19.5%, because the drawdown comes
    from forty positions moving together, not from any one breaking down."""
    from pathlib import Path

    source = Path("services/swing_breakout.py").read_text(encoding="utf-8")
    assert "long_trend_window" in source
    assert SwingConfig().long_trend_window == 200
    # The gate reads the symbol's own frame, never an index or a peer group.
    assert "close.rolling(config.long_trend_window)" in source
