"""The Daily Dashboard capacity sweep: its policies, fixed before the result.

`scripts/research/daily_capacity_sweep.py` asked whether the five-position cap
the shipped settings produce is what binds this strategy, as it bound
CONFIRMED_VOLUME_BREAKOUT. It is not: the shipped policy already takes 609 of
662 signals, and every alternative lowered Sharpe and the >=2023 profit. These
pin the policies and the simulator arithmetic, so neither can be adjusted to
make a different answer appear.
"""

from __future__ import annotations

from scripts.research import daily_capacity_sweep as sweep


def test_the_policies_were_registered_before_the_result():
    assert [p[0][0] for p in sweep.POLICIES] == ["A", "B", "C", "D"]
    assert sweep.POLICIES[0][1:] == (2.0, 10.0, 10), "A is the shipped policy"
    # B and C spend A's 10% budget, so their returns compare with A's directly.
    assert sweep.POLICIES[1][2] == sweep.POLICIES[2][2] == 10.0
    assert sweep.POLICIES[3][1:] == (1.0, 15.0, 15), "D is the breakout policy"


def test_the_shipped_settings_cap_the_book_at_five():
    """The arithmetic the sweep exists for: ten positions are never reached."""
    assert sweep.effective_cap(2.0, 10.0, 10) == 5


def test_the_other_caps_are_what_the_table_reports():
    assert sweep.effective_cap(1.0, 10.0, 10) == 10
    assert sweep.effective_cap(0.67, 10.0, 15) == 14
    assert sweep.effective_cap(1.0, 15.0, 15) == 15


def test_the_era_split_is_the_one_every_study_here_uses():
    assert sweep.ERA_SPLIT == "2023-01-01"
