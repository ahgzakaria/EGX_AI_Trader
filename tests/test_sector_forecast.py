"""Tests for the next-session sector liquidity forecast and its baselines."""

import numpy as np
import pandas as pd
import pytest

from sector_flow.forecast import (
    FEATURES,
    TARGET,
    baseline_forecast,
    build_dataset,
    engineer_features,
    forecast_next_session,
    normalise_per_session,
    walk_forward,
    _skill,
    _verdict,
)
from sector_flow.history import sector_history


SECTORS = ["Banks", "Real Estate", "Food and Beverages", "Construction"]


def synthetic_history(sessions=400, seed=11):
    """A tidy sector history with stable, persistent shares."""

    generator = np.random.default_rng(seed)
    dates = pd.bdate_range("2022-01-03", periods=sessions)
    rows = []
    levels = {sector: 1.0 + index for index, sector in enumerate(SECTORS)}
    for date in dates:
        turnovers = {}
        for sector in SECTORS:
            levels[sector] = max(0.2, levels[sector] * generator.normal(1.0, 0.05))
            turnovers[sector] = levels[sector] * 1_000_000
        total = sum(turnovers.values())
        for sector in SECTORS:
            rows.append({
                "SessionDate": date,
                "Sector": sector,
                "Turnover": turnovers[sector],
                "Symbols": 10,
                "Advancers": int(generator.integers(0, 11)),
                "Decliners": int(generator.integers(0, 11)),
                "MeanReturn": float(generator.normal(0, 0.01)),
                "MarketTurnover": total,
                "TurnoverShare": turnovers[sector] / total,
                "Breadth": float(generator.normal(0, 0.3)),
            })
    frame = pd.DataFrame(rows)
    frame["MarketSymbols"] = frame.groupby("SessionDate")["Symbols"].transform("sum")
    from sector_flow.history import add_flow_features
    return add_flow_features(frame)


@pytest.fixture(scope="module")
def history():
    return synthetic_history()


@pytest.fixture(scope="module")
def dataset(history):
    return build_dataset(history)


# --------------------------------------------------------------------------- #
# Target construction
# --------------------------------------------------------------------------- #

def test_target_is_the_same_sector_next_session(dataset):
    sample = dataset.sample(25, random_state=3)
    for _, row in sample.iterrows():
        expected = dataset[
            (dataset["Sector"] == row["Sector"])
            & (dataset["SessionDate"] == row["NextSession"])
        ]
        if expected.empty:
            continue
        assert row[TARGET] == pytest.approx(expected["TurnoverShare"].iloc[0])


def test_next_session_skips_an_excluded_session(history):
    """Dropping an incomplete session must join the sessions either side of it."""

    contaminated = history.copy()
    sessions = sorted(contaminated["SessionDate"].unique())
    dropped = sessions[50]
    contaminated.loc[contaminated["SessionDate"] == dropped, "SessionCoverage"] = 0.1

    dataset = build_dataset(contaminated)
    assert dropped not in set(dataset["SessionDate"])
    assert dropped not in set(dataset["NextSession"])

    bridged = dataset[dataset["SessionDate"] == sessions[49]]
    assert not bridged.empty
    assert (bridged["NextSession"] == sessions[51]).all()


def test_features_are_present_and_finite_enough(dataset):
    assert set(FEATURES).issubset(dataset.columns)
    assert not dataset[TARGET].isna().any()
    # The target is a share, so it must stay inside [0, 1].
    assert dataset[TARGET].between(0, 1).all()


def test_engineer_features_is_the_single_feature_path(history):
    """Training and inference must derive features identically."""

    engineered = engineer_features(history)
    dataset = build_dataset(history)
    latest = engineered["SessionDate"].max()
    shared = dataset[dataset["SessionDate"] == dataset["SessionDate"].max()]
    row = engineered[
        (engineered["SessionDate"] == shared["SessionDate"].iloc[0])
        & (engineered["Sector"] == shared["Sector"].iloc[0])
    ]
    for feature in FEATURES:
        assert row[feature].iloc[0] == pytest.approx(shared[feature].iloc[0], nan_ok=True)
    assert latest >= dataset["SessionDate"].max()


# --------------------------------------------------------------------------- #
# Leakage
# --------------------------------------------------------------------------- #

def test_folds_never_split_a_session_across_train_and_test(dataset):
    result = walk_forward(dataset, n_splits=4)
    for fold in result.predictions["Fold"].unique():
        fold_rows = result.predictions[result.predictions["Fold"] == fold]
        # Every sector of a tested session must be tested in the same fold.
        counts = fold_rows.groupby("SessionDate")["Sector"].nunique()
        assert (counts == len(SECTORS)).all()


def test_test_sessions_are_strictly_after_training_sessions(dataset):
    result = walk_forward(dataset, n_splits=4)
    for _, fold in result.folds.iterrows():
        assert fold["train_end"] < fold["test_start"]


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #

def test_predicted_shares_sum_to_one_per_session():
    frame = pd.DataFrame({
        "NextSession": ["d1"] * 3 + ["d2"] * 3,
        "Predicted": [0.5, 0.25, 0.25, 2.0, 1.0, 1.0],
    })
    normalised = normalise_per_session(frame)
    totals = normalised.groupby("NextSession")["Predicted"].sum()
    assert np.allclose(totals, 1.0)


def test_negative_predictions_are_clipped_before_normalising():
    frame = pd.DataFrame({"NextSession": ["d1"] * 3, "Predicted": [-1.0, 1.0, 1.0]})
    normalised = normalise_per_session(frame)
    assert (normalised["Predicted"] >= 0).all()
    assert normalised["Predicted"].sum() == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Scoring honesty
# --------------------------------------------------------------------------- #

def test_perfect_model_scores_full_skill():
    actual = np.array([0.1, 0.2, 0.3, 0.4])
    reference = np.array([0.2, 0.2, 0.2, 0.2])
    assert _skill(actual, actual, reference) == pytest.approx(1.0)


def test_model_equal_to_the_baseline_scores_zero_skill():
    actual = np.array([0.1, 0.2, 0.3, 0.4])
    reference = np.array([0.2, 0.25, 0.25, 0.3])
    assert _skill(actual, reference, reference) == pytest.approx(0.0)


def test_verdict_reports_no_skill_when_the_best_baseline_wins():
    verdict = _verdict({"skill_vs_best_baseline": -0.01, "skill_vs_persistence": 0.24})
    assert verdict.startswith("NO SKILL")
    # Beating the weaker baseline must not be presented as success.
    assert "persistence" in verdict


def test_verdict_reports_marginal_below_the_threshold():
    assert _verdict({"skill_vs_best_baseline": 0.005, "skill_vs_persistence": 0.005}).startswith(
        "MARGINAL"
    )


def test_verdict_reports_skill_only_on_the_best_baseline():
    assert _verdict({"skill_vs_best_baseline": 0.15, "skill_vs_persistence": 0.30}).startswith(
        "SKILL"
    )


def test_walk_forward_reports_both_baselines(dataset):
    metrics = walk_forward(dataset, n_splits=4).metrics
    for key in ("mae_persistence", "mae_mean5", "skill_vs_persistence",
                "skill_vs_mean5", "skill_vs_best_baseline"):
        assert key in metrics


def test_skill_against_best_baseline_never_exceeds_the_weaker_one(dataset):
    metrics = walk_forward(dataset, n_splits=4).metrics
    assert metrics["skill_vs_best_baseline"] <= metrics["skill_vs_persistence"] + 1e-9
    assert metrics["skill_vs_best_baseline"] <= metrics["skill_vs_mean5"] + 1e-9


# --------------------------------------------------------------------------- #
# Forecast output
# --------------------------------------------------------------------------- #

def test_baseline_forecast_is_a_normalised_partition(history):
    forecast = baseline_forecast(history)
    assert forecast["Predicted"].sum() == pytest.approx(1.0)
    assert set(forecast["Sector"]) == set(SECTORS)


def test_forecast_returns_model_and_baseline_side_by_side(history):
    forecast = forecast_next_session(history)
    assert {"Predicted", "Baseline", "Change", "BaselineChange"}.issubset(forecast.columns)
    assert forecast["Predicted"].sum() == pytest.approx(1.0)
    assert forecast["Baseline"].sum() == pytest.approx(1.0)


def test_empty_history_yields_no_forecast():
    assert forecast_next_session(pd.DataFrame()).empty
    assert baseline_forecast(pd.DataFrame()).empty


def test_walk_forward_rejects_too_few_sessions(dataset):
    tiny = dataset[dataset["SessionDate"].isin(sorted(dataset["SessionDate"].unique())[:4])]
    with pytest.raises(ValueError):
        walk_forward(tiny, n_splits=5)


def test_unknown_target_mode_is_rejected(dataset):
    with pytest.raises(ValueError):
        walk_forward(dataset, n_splits=4, target_mode="magic")
