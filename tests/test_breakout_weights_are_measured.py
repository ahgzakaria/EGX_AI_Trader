"""The breakout weights must stay measured, not drift back to intuition.

They were hand-assigned: 20 points for a resistance breakout, 15 for volume,
15 for consolidation. Measuring them over 166,173 stock-days -- trained before
2024-01-01, validated after, net of a 0.80% round trip -- found the allocation
close to backwards. Volume was the strongest feature and carried 15;
consolidation carried 15 and reversed sign out of sample.

These tests do not re-run the study. They assert the properties the study
established, so a later edit that quietly restores a plausible-looking number
fails and has to justify itself with a new measurement. Re-derive with
`scripts/research/breakout_features.py` and `breakout_volume_bands.py`.
"""

from __future__ import annotations

import pytest

from strategy_breakout.breakout_scoring import (
    UNMEASURED_FEATURES,
    WEIGHTS,
    WEIGHTS_PROVENANCE,
)
from strategy_breakout.breakout_strategy import BreakoutConfig


def test_volume_confirmation_outweighs_the_breakout_itself():
    """The measured lift of a high-volume breakout was roughly 2.5x that of a
    breakout alone, in validation and at every horizon."""

    assert WEIGHTS["high_volume_breakout"] > WEIGHTS["previous_resistance_breakout"] * 2


def test_features_that_failed_out_of_sample_carry_no_weight():
    """`consolidation_breakout` reversed sign in validation, on 362 days.
    `higher_high_breakout` lifted +1.21% in training and -0.10% after.
    Between them they carried 25 of the original 100 points."""

    assert WEIGHTS["consolidation_breakout"] == 0
    assert WEIGHTS["higher_high_breakout"] == 0


def test_unmeasured_features_stay_small():
    """Two features are absent from the archived daily data and could not be
    measured. A weight nobody has evidence for must not be able to carry a
    signal on its own."""

    for feature in UNMEASURED_FEATURES:
        assert feature in WEIGHTS, feature
        assert WEIGHTS[feature] <= 5, (
            f"{feature} is unmeasured; it may not carry a large weight"
        )

    unmeasured_total = sum(WEIGHTS[f] for f in UNMEASURED_FEATURES)
    assert unmeasured_total < sum(WEIGHTS.values()) * 0.15


def test_the_measured_weights_dominate_the_score():
    measured = {k: v for k, v in WEIGHTS.items() if k not in UNMEASURED_FEATURES}
    assert sum(measured.values()) >= 90


def test_the_volume_threshold_sits_above_the_band_that_loses():
    """Twenty-day validation return, net of cost: a breakout on 1.0-1.5x
    volume returned -0.11%, worse than not trading at all (+2.91%). The
    1.5-2.5x band returned +2.26%, no better than sitting out. Only above
    2.5x (+5.73%, 59% win) is there an edge, so that is where the gate goes.
    """

    assert BreakoutConfig().minimum_volume_ratio >= 2.5


def test_the_weights_say_where_they_came_from():
    """A weight without provenance is indistinguishable from a guess."""

    for token in ("frozen_eodhd_seed", "2024-01-01", "166,173"):
        assert token in WEIGHTS_PROVENANCE, token


@pytest.mark.parametrize("feature,weight", sorted(WEIGHTS.items()))
def test_no_weight_is_negative_or_absurd(feature, weight):
    assert 0 <= weight <= 100, feature


# --- the dashboard must show the basis, not just the number ------------


def test_the_scoring_basis_panel_renders_and_names_its_source():
    """A score whose basis is invisible is indistinguishable from a guess.

    The panel is what lets a reader tell a weight that was earned from one
    that was picked, so it has to actually render -- not merely import.
    """

    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string(
        "from dashboard.home import show_scoring_basis\n"
        "show_scoring_basis()\n"
    )
    app.run(timeout=30)

    assert not app.exception, app.exception

    text = " ".join(
        block.value for block in list(app.markdown) + list(app.caption)
        if isinstance(getattr(block, "value", None), str)
    )
    # Where the weights came from.
    assert "frozen_eodhd_seed" in text
    assert "2024-01-01" in text
    # The finding that moved the entry gate.
    assert "2.5" in text
    # And the honest limits.
    assert "not a probability" in text
    assert "does not subtract the cost" in text


def test_the_panel_lists_every_weight_including_the_retired_ones():
    """Retired features stay visible at zero. Deleting them would hide the
    measurement that retired them, and the next reader would re-add them."""

    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string(
        "from dashboard.home import show_scoring_basis\n"
        "show_scoring_basis()\n"
    )
    app.run(timeout=30)
    assert not app.exception, app.exception

    rendered = app.dataframe
    assert len(rendered) >= 1, "the weight table did not render"
    frame = rendered[0].value
    assert len(frame) == len(WEIGHTS)
    assert (frame["Weight"] == 0).any(), "no retired feature is shown"
