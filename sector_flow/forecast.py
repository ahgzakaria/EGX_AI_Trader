"""Next-session sector liquidity forecast, benchmarked against naive baselines.

What is predicted: each sector's share of the next session's market turnover.
Share -- not turnover -- is the target because it is self-normalising, so a
market-wide quiet or busy day cannot masquerade as a rotation signal.

Why the baselines are not optional: turnover is strongly autocorrelated, so a
model that simply repeats today's share already scores well on MAE. Every result
here is therefore reported as a *skill score* against persistence (share
tomorrow = share today). A skill score at or below zero means the model adds
nothing, and this module says so rather than reporting a flattering MAE.

Only complete sessions take part. See SECTOR_LIQUIDITY_FLOW_REPORT.md: EODHD is
missing Sunday bars for roughly 40% of EGX symbols before 2026, and training
across those sessions would teach the model a spurious weekday pattern.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import TimeSeriesSplit

from sector_flow.history import DEFAULT_MIN_COVERAGE, complete_sessions


FEATURES = [
    "TurnoverShare",
    "SharePrev",
    "ShareChange",
    "ShareRank",
    "TurnoverZ",
    "RVOL",
    "Breadth",
    "MeanReturn",
    "Symbols",
    "ShareMean5",
    "ShareMean20",
    "ShareStd20",
    "MarketTurnoverZ",
]
TARGET = "NextTurnoverShare"
DEFAULT_SPLITS = 5
DEFAULT_TOP_K = 3
# "level" learns the next share directly. "residual" learns only the part the
# 5-session mean gets wrong. On EGX history to date "residual" scored worse, so
# "level" is the default; see SECTOR_LIQUIDITY_FORECAST_REPORT.md.
TARGET_MODES = ("level", "residual")
DEFAULT_TARGET_MODE = "level"
RESIDUAL_ANCHOR = "ShareMean5"


@dataclass
class ForecastResult:
    """Out-of-sample predictions, per-fold metrics and the honest verdict."""

    folds: pd.DataFrame
    predictions: pd.DataFrame
    metrics: dict
    verdict: str
    features: list = field(default_factory=lambda: list(FEATURES))


def engineer_features(history, min_coverage=DEFAULT_MIN_COVERAGE):
    """Return complete sessions with the model's derived features attached.

    Training and inference both go through this single function. If the two ever
    computed features separately they could drift apart, and the model would be
    served inputs that do not mean what it learned.
    """

    usable = complete_sessions(history, min_coverage)
    if usable is None or usable.empty:
        return pd.DataFrame()

    frame = usable.sort_values(["Sector", "SessionDate"]).reset_index(drop=True)
    share = frame.groupby("Sector", sort=False)["TurnoverShare"]
    frame["ShareMean5"] = share.transform(lambda values: values.rolling(5, min_periods=2).mean())
    frame["ShareMean20"] = share.transform(lambda values: values.rolling(20, min_periods=5).mean())
    frame["ShareStd20"] = share.transform(lambda values: values.rolling(20, min_periods=5).std())

    market = frame.groupby("SessionDate", sort=True)["MarketTurnover"].first()
    log_market = np.log(market.where(market > 0))
    trailing = log_market.shift(1).rolling(20, min_periods=20)
    trailing_std = trailing.std()
    market_z = (log_market - trailing.mean()) / trailing_std.where(trailing_std > 0)
    frame["MarketTurnoverZ"] = frame["SessionDate"].map(market_z)
    return frame


def build_dataset(history, min_coverage=DEFAULT_MIN_COVERAGE):
    """Return a supervised panel: features known at session t, target at t+1.

    ``t+1`` is the next *retained* session, so dropping an incomplete session
    joins the sessions either side of it rather than silently shifting the
    target onto a partial day.
    """

    frame = engineer_features(history, min_coverage)
    if frame.empty:
        return pd.DataFrame()

    sessions = pd.Index(sorted(frame["SessionDate"].unique()))
    next_session = pd.Series(sessions[1:], index=sessions[:-1])
    frame["NextSession"] = frame["SessionDate"].map(next_session)

    target = frame[["SessionDate", "Sector", "TurnoverShare"]].rename(
        columns={"SessionDate": "NextSession", "TurnoverShare": TARGET}
    )
    dataset = frame.merge(target, on=["NextSession", "Sector"], how="inner")
    return dataset.dropna(subset=[TARGET]).sort_values(
        ["SessionDate", "Sector"]
    ).reset_index(drop=True)


def build_model():
    """Gradient boosting on a small tabular panel; NaNs handled natively."""

    return HistGradientBoostingRegressor(
        max_depth=4,
        max_iter=300,
        learning_rate=0.05,
        min_samples_leaf=20,
        l2_regularization=1.0,
        early_stopping=False,
        random_state=42,
    )


def normalise_per_session(predictions, session_column="NextSession", column="Predicted"):
    """Rescale each session's predicted shares so they sum to one.

    Sectors are predicted independently, but their shares are a partition of the
    same day's turnover. Renormalising restores that constraint.
    """

    frame = predictions.copy()
    clipped = frame[column].clip(lower=0.0)
    totals = clipped.groupby(frame[session_column]).transform("sum")
    frame[column] = np.where(totals > 0, clipped / totals, np.nan)
    return frame


def _fit_predict(train, test, target_mode):
    """Fit one fold and return its predicted next-session shares."""

    if target_mode not in TARGET_MODES:
        raise ValueError(f"Unknown target mode: {target_mode!r}")

    model = build_model()
    if target_mode == "level":
        model.fit(train[FEATURES], train[TARGET])
        return model.predict(test[FEATURES])

    usable = train[train[RESIDUAL_ANCHOR].notna()]
    if usable.empty:
        raise ValueError("No rows with a residual anchor to train on.")
    model.fit(usable[FEATURES], usable[TARGET] - usable[RESIDUAL_ANCHOR])
    anchor = test[RESIDUAL_ANCHOR].to_numpy(dtype="float64")
    # Where the anchor is undefined the model has nothing to correct, so the
    # prediction falls back to today's share rather than to an arbitrary zero.
    anchor = np.where(np.isnan(anchor), test["TurnoverShare"].to_numpy(dtype="float64"), anchor)
    return anchor + model.predict(test[FEATURES])


def walk_forward(dataset, n_splits=DEFAULT_SPLITS, top_k=DEFAULT_TOP_K,
                 target_mode=DEFAULT_TARGET_MODE):
    """Chronological, session-blocked validation against persistence and mean-5."""

    if dataset is None or dataset.empty:
        raise ValueError("Empty dataset: build_dataset produced no supervised rows.")

    sessions = pd.Index(sorted(dataset["SessionDate"].unique()))
    if len(sessions) < n_splits + 2:
        raise ValueError(
            f"Not enough sessions ({len(sessions)}) for {n_splits} walk-forward folds."
        )

    splitter = TimeSeriesSplit(n_splits=int(n_splits))
    fold_rows, prediction_frames = [], []

    for fold_number, (train_positions, test_positions) in enumerate(splitter.split(sessions), 1):
        # Folds are split on sessions, never on rows: every sector of a given
        # session must fall on the same side of the boundary.
        train_sessions = set(sessions[train_positions])
        test_sessions = set(sessions[test_positions])
        train = dataset[dataset["SessionDate"].isin(train_sessions)]
        test = dataset[dataset["SessionDate"].isin(test_sessions)]
        if train.empty or test.empty:
            continue

        predicted = test[[
            "SessionDate", "NextSession", "Sector", TARGET, "TurnoverShare", "ShareMean5",
        ]].copy()
        predicted["Predicted"] = _fit_predict(train, test, target_mode)
        predicted = normalise_per_session(predicted)
        predicted["Fold"] = fold_number
        prediction_frames.append(predicted)

        fold_rows.append({
            "fold": fold_number,
            "train_sessions": len(train_sessions),
            "test_sessions": len(test_sessions),
            "train_rows": len(train),
            "test_rows": len(test),
            "train_start": str(min(train_sessions).date()),
            "train_end": str(max(train_sessions).date()),
            "test_start": str(min(test_sessions).date()),
            "test_end": str(max(test_sessions).date()),
            **_scores(predicted, top_k),
        })

    if not prediction_frames:
        raise ValueError("Walk-forward produced no evaluated folds.")

    predictions = pd.concat(prediction_frames, ignore_index=True)
    metrics = _scores(predictions, top_k)
    metrics["predictions"] = len(predictions)
    metrics["sessions"] = int(predictions["NextSession"].nunique())
    metrics["target_mode"] = target_mode
    return ForecastResult(pd.DataFrame(fold_rows), predictions, metrics, _verdict(metrics))


def _scores(predictions, top_k):
    """Return model and baseline errors plus skill scores over the same rows."""

    actual = predictions[TARGET].to_numpy(dtype="float64")
    model = predictions["Predicted"].to_numpy(dtype="float64")
    persistence = predictions["TurnoverShare"].to_numpy(dtype="float64")
    mean5 = predictions["ShareMean5"].to_numpy(dtype="float64")

    scores = {
        "mae": _mae(actual, model),
        "rmse": _rmse(actual, model),
        "mae_persistence": _mae(actual, persistence),
        "rmse_persistence": _rmse(actual, persistence),
        "mae_mean5": _mae(actual, mean5),
        "rmse_mean5": _rmse(actual, mean5),
        "skill_vs_persistence": _skill(actual, model, persistence),
        "skill_vs_mean5": _skill(actual, model, mean5),
        # Scored against whichever naive baseline is hardest to beat, so a weak
        # baseline can never be used to advertise skill the model lacks.
        "skill_vs_best_baseline": _skill_vs_best(actual, model, persistence, mean5),
        "rank_correlation": _rank_correlation(predictions),
        "rank_correlation_persistence": _rank_correlation(predictions, column="TurnoverShare"),
        f"top{top_k}_hit_rate": _top_k_hit_rate(predictions, top_k),
        f"top{top_k}_hit_rate_persistence": _top_k_hit_rate(
            predictions, top_k, column="TurnoverShare"
        ),
    }
    return {key: _round(value) for key, value in scores.items()}


def _mae(actual, predicted):
    mask = ~np.isnan(actual) & ~np.isnan(predicted)
    return float(np.mean(np.abs(actual[mask] - predicted[mask]))) if mask.any() else np.nan


def _rmse(actual, predicted):
    mask = ~np.isnan(actual) & ~np.isnan(predicted)
    return float(np.sqrt(np.mean((actual[mask] - predicted[mask]) ** 2))) if mask.any() else np.nan


def _skill_vs_best(actual, model, persistence, mean5):
    """Skill against the stronger of the two naive baselines."""

    candidates = []
    for reference in (persistence, mean5):
        mask = ~np.isnan(actual) & ~np.isnan(reference)
        if mask.any():
            candidates.append(float(np.mean((actual[mask] - reference[mask]) ** 2)))
    if not candidates:
        return np.nan
    best = min(candidates)
    mask = ~np.isnan(actual) & ~np.isnan(model)
    if not mask.any() or best <= 0:
        return np.nan
    return 1.0 - float(np.mean((actual[mask] - model[mask]) ** 2)) / best


def _skill(actual, model, reference):
    """1 - MSE(model)/MSE(reference), scored only on rows both can predict."""

    mask = ~np.isnan(actual) & ~np.isnan(model) & ~np.isnan(reference)
    if not mask.any():
        return np.nan
    reference_mse = float(np.mean((actual[mask] - reference[mask]) ** 2))
    if reference_mse <= 0:
        return np.nan
    model_mse = float(np.mean((actual[mask] - model[mask]) ** 2))
    return 1.0 - model_mse / reference_mse


def _rank_correlation(predictions, column="Predicted"):
    """Mean per-session Spearman correlation between predicted and actual shares."""

    values = []
    for _, group in predictions.groupby("NextSession", sort=False):
        if len(group) < 3:
            continue
        correlation = spearmanr(group[column], group[TARGET]).statistic
        if not np.isnan(correlation):
            values.append(float(correlation))
    return float(np.mean(values)) if values else np.nan


def _top_k_hit_rate(predictions, top_k, column="Predicted"):
    """Mean overlap between the predicted and the actual top-k sectors per session."""

    hits = []
    for _, group in predictions.groupby("NextSession", sort=False):
        if len(group) <= top_k:
            continue
        predicted_top = set(group.nlargest(top_k, column)["Sector"])
        actual_top = set(group.nlargest(top_k, TARGET)["Sector"])
        hits.append(len(predicted_top & actual_top) / top_k)
    return float(np.mean(hits)) if hits else np.nan


def _verdict(metrics):
    """State plainly whether the model beat the *strongest* naive baseline.

    Beating persistence alone is not evidence of skill: a 5-session mean is a
    harder and equally trivial competitor, and a model that clears one but not
    the other has learned smoothing, not rotation.
    """

    skill = metrics.get("skill_vs_best_baseline")
    persistence = metrics.get("skill_vs_persistence")
    if skill is None or (isinstance(skill, float) and np.isnan(skill)):
        return "INCONCLUSIVE: skill against the naive baselines could not be computed."

    context = ""
    if persistence is not None and not np.isnan(persistence) and persistence > skill + 0.02:
        context = (
            f" It does beat persistence ({persistence:+.3f}), but persistence is the"
            " weaker baseline and that margin is mostly smoothing."
        )
    if skill <= 0:
        return (
            f"NO SKILL: the model does not beat the best naive baseline "
            f"(skill {skill:+.3f}). Do not use these forecasts.{context}"
        )
    if skill < 0.02:
        return (
            f"MARGINAL: skill {skill:+.3f} over the best naive baseline is too small "
            f"to rely on. Treat as unproven.{context}"
        )
    return f"SKILL: {skill:+.3f} improvement in MSE over the best naive baseline."


def _round(value):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    return round(float(value), 6) if isinstance(value, float) else value


def baseline_forecast(history, min_coverage=DEFAULT_MIN_COVERAGE):
    """Return the 5-session mean forecast for the next session.

    This is not a fallback. On EGX history it is the most accurate forecaster
    measured here, and the learned model has not been shown to beat it.
    """

    engineered = engineer_features(history, min_coverage)
    if engineered.empty:
        return pd.DataFrame()

    latest = engineered[engineered["SessionDate"] == engineered["SessionDate"].max()].copy()
    latest["Predicted"] = latest[RESIDUAL_ANCHOR].fillna(latest["TurnoverShare"])
    latest = normalise_per_session(latest, session_column="SessionDate")
    latest["Change"] = latest["Predicted"] - latest["TurnoverShare"]
    return latest[["SessionDate", "Sector", "TurnoverShare", "Predicted", "Change"]].sort_values(
        "Predicted", ascending=False
    ).reset_index(drop=True)


def forecast_next_session(history, min_coverage=DEFAULT_MIN_COVERAGE,
                          target_mode=DEFAULT_TARGET_MODE):
    """Predict the session after the latest complete one, beside the baseline.

    Both the learned model and the 5-session mean are returned so the two can be
    compared directly. Read the walk-forward verdict before trusting the model
    column over the baseline column.

    This is a liquidity-activity forecast only. It says where trading value is
    likely to concentrate, not which way any price will move.
    """

    dataset = build_dataset(history, min_coverage)
    if dataset.empty:
        return pd.DataFrame()

    model = build_model()
    if target_mode == "residual":
        usable = dataset[dataset[RESIDUAL_ANCHOR].notna()]
        model.fit(usable[FEATURES], usable[TARGET] - usable[RESIDUAL_ANCHOR])
    else:
        model.fit(dataset[FEATURES], dataset[TARGET])

    # The newest session carries no target yet, so it is absent from the
    # supervised panel. Rebuild the same feature rows and score the last one.
    engineered = engineer_features(history, min_coverage)
    if engineered.empty:
        return pd.DataFrame()
    features = engineered[engineered["SessionDate"] == engineered["SessionDate"].max()]
    if features.empty:
        return pd.DataFrame()

    forecast = features[["SessionDate", "Sector", "TurnoverShare"]].copy()
    raw = model.predict(features[FEATURES])
    if target_mode == "residual":
        anchor = features[RESIDUAL_ANCHOR].fillna(features["TurnoverShare"]).to_numpy(dtype="float64")
        raw = anchor + raw
    forecast["Predicted"] = raw
    forecast = normalise_per_session(forecast, session_column="SessionDate")

    baseline = baseline_forecast(history, min_coverage)[["Sector", "Predicted"]].rename(
        columns={"Predicted": "Baseline"}
    )
    forecast = forecast.merge(baseline, on="Sector", how="left")
    forecast["Change"] = forecast["Predicted"] - forecast["TurnoverShare"]
    forecast["BaselineChange"] = forecast["Baseline"] - forecast["TurnoverShare"]
    return forecast.sort_values("Baseline", ascending=False).reset_index(drop=True)
