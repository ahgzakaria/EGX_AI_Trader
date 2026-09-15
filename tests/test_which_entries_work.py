"""The entry-feature study's rules, pinned before its result was read.

`scripts/research/which_entries_work.py` pre-registers what counts as a finding:
a feature survives only with the same sign and p < 0.05 in BOTH eras, and
nothing is fitted. These tests fix that rule in code, so it cannot be quietly
loosened after a result disappoints -- which is how a study that found nothing
turns into one that found something.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.research import which_entries_work as study


def frame(n=400, *, early=None, late=None, seed=7):
    """Synthetic trades: `signal` drives the outcome in the eras asked for."""
    rng = np.random.default_rng(seed)
    rows = []
    for era, strength in (("early", early), ("late", late)):
        x = rng.normal(size=n)
        noise = rng.normal(size=n)
        y = (strength or 0.0) * x + noise
        for xi, yi in zip(x, y):
            rows.append({"_era": era, "dist_high20": xi, "rsi": rng.normal(),
                         "profit_percent": yi})
    return pd.DataFrame(rows)


def test_a_feature_that_works_in_both_eras_survives():
    table = study.correlations(frame(early=-0.5, late=-0.5))
    row = table[table.feature == "dist_high20"].iloc[0]
    assert row.survives


def test_working_in_one_era_is_not_surviving():
    """This is what fitting to one era looks like from the other side."""
    table = study.correlations(frame(early=0.6, late=None))
    row = table[table.feature == "dist_high20"].iloc[0]
    assert row.early_p < 0.05
    assert not row.survives


def test_opposite_signs_in_the_two_eras_is_not_surviving():
    table = study.correlations(frame(early=0.5, late=-0.5))
    row = table[table.feature == "dist_high20"].iloc[0]
    assert row.early_p < 0.05 and row.late_p < 0.05
    assert not row.survives


def test_noise_does_not_survive():
    table = study.correlations(frame())
    assert not table[table.feature == "rsi"].iloc[0].survives


def test_too_few_trades_is_not_measured_rather_than_measured_as_nothing():
    small = frame(n=10, early=-2.0, late=-2.0)
    row = study.correlations(small).query("feature == 'dist_high20'").iloc[0]
    assert np.isnan(row.early_rho) and not row.survives


def test_a_feature_absent_from_the_data_is_skipped_not_invented():
    table = study.correlations(frame())
    assert "breakout_score" not in set(table.feature)


def test_quintiles_split_each_era_into_five_and_report_the_outcome():
    q = study.quintiles(frame(early=-0.5, late=-0.5), "dist_high20")
    assert set(q.era) == {"early", "late"}
    assert sorted(q[q.era == "early"].fifth) == [1, 2, 3, 4, 5]
    early = q[q.era == "early"].set_index("fifth")
    assert early.loc[1, "mean%"] > early.loc[5, "mean%"]   # negative relation


def test_profit_factor_has_no_losses_case():
    assert study.profit_factor(pd.Series([1.0, 2.0])) == float("inf")
    assert study.profit_factor(pd.Series([2.0, -1.0])) == pytest.approx(2.0)


def test_the_hypothesis_was_registered_before_the_result():
    """Negative: nearer the twenty-day high did better in the earlier study."""
    assert study.HYPOTHESIS == ("dist_high20", -1)
