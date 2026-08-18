"""What is measured here, what is not, and why the difference is recorded.

On 2026-08-18 the breakout weights were re-derived from 166,173 stock-days and
then reverted, because the re-weighting backtested worse across three separate
windows. Both facts matter and only one of them is obvious from the code, so
these tests pin the honest labelling as much as the values.

The part that survived validation -- the volume entry gate at 2.5x -- is
asserted against the *loaded* configuration, because an earlier version of
this file asserted the dataclass default and passed while the live scanner
still ran at 1.5.

Re-derive anything here with `scripts/research/breakout_features.py` and
`scripts/research/breakout_volume_bands.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_breakout.breakout_scoring import (
    MEASURED_CHANGES,
    WEIGHTS,
    WEIGHTS_ARE_MEASURED,
    WEIGHTS_PROVENANCE,
)
from strategy_breakout.breakout_strategy import BreakoutConfig, load_breakout_config


# --- the gate that survived validation ---------------------------------


def test_the_volume_gate_stayed_where_it_was():
    """Raising it to 2.5x looked overwhelming on raw breakouts and failed here.

    Varying only this gate across five 260-bar windows, 1.5 returned 4.151%
    net per trade against 3.770% at 2.5, with a higher median and four of the
    five windows. An edge measured on an unfiltered population does not
    transfer to one this strategy has already filtered several other ways.
    """

    assert load_breakout_config().minimum_volume_ratio == pytest.approx(1.5)
    assert "minimum_volume_ratio" not in MEASURED_CHANGES


def test_every_default_the_settings_file_overrides_agrees_with_it():
    """Where the file and the class body disagree, the file silently wins and
    the class body becomes a comment that reads like configuration."""

    stored = json.loads(
        Path("strategy_breakout/settings.json").read_text(encoding="utf-8-sig")
    )
    defaults = BreakoutConfig()
    disagreements = {
        key: (getattr(defaults, key), value)
        for key, value in stored.items()
        if hasattr(defaults, key) and getattr(defaults, key) != value
    }
    assert not disagreements, (
        f"settings.json overrides these class defaults with different values, "
        f"so editing the class body has no effect: {disagreements}"
    )


def test_the_backtest_charges_the_real_broker_fee():
    """0.003 per side was a placeholder. The contract note says 0.1819%."""

    assert load_breakout_config().commission == pytest.approx(0.001819)


# --- the weights, and their honest label -------------------------------


def test_the_weights_do_not_claim_to_be_measured():
    """They are not, and a score that overstates its own basis is worse than
    one that admits it has none."""

    assert WEIGHTS_ARE_MEASURED is False
    assert "not measured" in WEIGHTS_PROVENANCE


def test_the_provenance_records_the_attempt_and_its_result():
    """The failed re-weighting is the more useful half of that work: it says
    per-feature lift is not a basis for these weights. Losing that note would
    invite the next reader to repeat it."""

    for token in ("2.871", "3.498", "breakout_features.py"):
        assert token in WEIGHTS_PROVENANCE, token


def test_the_reverted_weights_are_the_ones_that_backtested_better():
    """Concentrating weight on the two strongest features let a setup qualify
    on those alone, roughly tripling the signal count and diluting it. The
    spread-out weights require several independent confirmations, and that
    count is itself the selectivity."""

    assert WEIGHTS["previous_resistance_breakout"] == 20
    assert WEIGHTS["consolidation_breakout"] == 15
    assert WEIGHTS["higher_high_breakout"] == 10
    # No single feature may carry a setup past the score gate on its own.
    assert max(WEIGHTS.values()) < load_breakout_config().minimum_score


@pytest.mark.parametrize("feature,weight", sorted(WEIGHTS.items()))
def test_no_weight_is_negative_or_absurd(feature, weight):
    assert 0 < weight <= 100, feature


# --- the dashboard must show the basis, not just the number ------------


def _rendered_panel():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string(
        "from dashboard.home import show_scoring_basis\nshow_scoring_basis()\n"
    )
    app.run(timeout=30)
    assert not app.exception, app.exception
    return app


def test_the_panel_admits_the_weights_are_not_measured():
    """A reader who sees a weight table naturally assumes it was derived. The
    panel has to say plainly that it was not."""

    app = _rendered_panel()
    # The admission lives in a warning, the detail in markdown. Collect every
    # text-bearing element so the test does not pass merely because the
    # sentence moved between them.
    blocks = list(app.markdown) + list(app.caption) + list(app.warning) + list(app.info)
    text = " ".join(
        block.value for block in blocks
        if isinstance(getattr(block, "value", None), str)
    )

    assert "not measured" in text
    # And it must still carry the one change that was.
    assert "2.5" in text
    assert "not a probability" in text


def test_the_panel_lists_every_weight():
    app = _rendered_panel()
    assert app.dataframe, "the weight table did not render"
    assert len(app.dataframe[0].value) == len(WEIGHTS)
