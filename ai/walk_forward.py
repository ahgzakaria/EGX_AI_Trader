"""Leakage-safe walk-forward AI validation and historical inference."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline

from ai.dataset import DatasetBuilder


@dataclass
class WalkForwardResult:
    folds: pd.DataFrame
    predictions: pd.DataFrame
    aggregate: dict
    historical_filter: object


def build_model():
    """A model whose only learned preprocessing is fitted per train fold."""
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("model", RandomForestClassifier(
            n_estimators=300,
            max_depth=12,
            min_samples_leaf=5,
            random_state=42,
            # Deterministic single-process folds avoid nested worker pools
            # while the backtest itself is iterating across symbols.
            n_jobs=1,
        )),
    ])


class HistoricalAIFilter:
    """Use only the model assigned to an out-of-sample chronological fold."""

    def __init__(self, folds, features):
        self.folds = folds
        self.features = features
        self._cache = {}

    def predict(self, feature_values, prediction_date):
        prediction_date = pd.Timestamp(prediction_date)
        normalized = {
            str(key).lower(): value
            for key, value in feature_values.items()
        }
        cache_key = (
            prediction_date.date().isoformat(),
            tuple(normalized.get(feature, np.nan) for feature in self.features),
        )
        if cache_key in self._cache:
            return self._cache[cache_key]
        for fold in self.folds:
            deployment_end = fold.get("deployment_end", fold["test_end"])
            if fold["test_start"] <= prediction_date <= deployment_end:
                values = pd.DataFrame(
                    [[normalized.get(feature, np.nan) for feature in self.features]],
                    columns=self.features,
                )
                model = fold["model"]
                probabilities = model.predict_proba(values)[0]
                classes = list(model.named_steps["model"].classes_)
                probability = float(probabilities[classes.index(1)]) * 100 if 1 in classes else 0.0
                result = {
                    "Probability": round(probability, 1),
                    "AILevel": _ai_level(probability),
                }
                self._cache[cache_key] = result
                return result
        self._cache[cache_key] = None
        return None


class WalkForwardValidator:
    """Chronological, label-availability-aware validation for trade samples."""

    def __init__(self, features=None, n_splits=5):
        self.features = features or DatasetBuilder.FEATURES
        self.n_splits = int(n_splits)

    def validate(self, samples, persist=True):
        samples = samples.copy()
        required = {"entry_date", "exit_date", "result", *self.features}
        missing = required.difference(samples.columns)
        if missing:
            raise ValueError(f"Walk-forward samples missing columns: {sorted(missing)}")

        samples["entry_date"] = pd.to_datetime(
            samples["entry_date"], errors="coerce"
        )
        samples["exit_date"] = pd.to_datetime(
            samples["exit_date"], errors="coerce"
        )
        samples = samples.dropna(subset=["entry_date", "exit_date"])
        samples = samples.sort_values("entry_date").reset_index(drop=True)
        if len(samples) < self.n_splits + 2:
            raise ValueError("Not enough samples for the requested walk-forward folds")

        splitter = TimeSeriesSplit(n_splits=self.n_splits)
        fold_rows, prediction_rows, deployed_folds = [], [], []

        for fold_number, (train_positions, test_positions) in enumerate(splitter.split(samples), 1):
            test = samples.iloc[test_positions]
            test_start = test["entry_date"].min()
            # Crucial anti-leakage rule: outcomes must be known before the
            # first prediction in a test fold, not merely entered earlier.
            train = samples.iloc[train_positions]
            train = train[train["exit_date"] < test_start]

            if train.empty or train["result"].nunique() < 2:
                fold_rows.append(_skipped_fold(fold_number, train, test, test_start))
                continue

            model = build_model()
            X_train = train[self.features]
            y_train = train["result"].astype(int)
            X_test = test[self.features]
            y_test = test["result"].astype(int)
            model.fit(X_train, y_train)

            probabilities = _positive_probability(model, X_test)
            predicted = (probabilities >= 0.5).astype(int)
            matrix = confusion_matrix(y_test, predicted, labels=[0, 1]).tolist()
            auc = roc_auc_score(y_test, probabilities) if y_test.nunique() == 2 else np.nan

            fold_rows.append({
                "fold": fold_number,
                "status": "evaluated",
                "train_rows": len(train),
                "test_rows": len(test),
                "train_start": train["entry_date"].min().date().isoformat(),
                "train_end": train["exit_date"].max().date().isoformat(),
                "test_start": test_start.date().isoformat(),
                "test_end": test["entry_date"].max().date().isoformat(),
                "train_positive_rate": round(float(y_train.mean()), 4),
                "test_positive_rate": round(float(y_test.mean()), 4),
                "accuracy": round(accuracy_score(y_test, predicted), 4),
                "precision": round(precision_score(y_test, predicted, zero_division=0), 4),
                "recall": round(recall_score(y_test, predicted, zero_division=0), 4),
                "f1": round(f1_score(y_test, predicted, zero_division=0), 4),
                "roc_auc": round(float(auc), 4) if not np.isnan(auc) else np.nan,
                "confusion_matrix": str(matrix),
            })

            for sample_index, probability, label, prediction in zip(
                test.index, probabilities, y_test, predicted
            ):
                prediction_rows.append({
                    "sample_index": sample_index,
                    "fold": fold_number,
                    "entry_date": samples.loc[sample_index, "entry_date"].date().isoformat(),
                    "exit_date": samples.loc[sample_index, "exit_date"].date().isoformat(),
                    "actual": int(label),
                    "probability": round(float(probability), 6),
                    "prediction": int(prediction),
                })

            deployed_folds.append({
                "test_start": test_start,
                "test_end": test["entry_date"].max(),
                "model": model,
            })

        _assign_deployment_windows(deployed_folds)
        folds = pd.DataFrame(fold_rows)
        predictions = pd.DataFrame(prediction_rows)
        aggregate = _aggregate_metrics(predictions)
        historical_filter = HistoricalAIFilter(deployed_folds, self.features)

        if persist:
            _persist_reports(folds, predictions, aggregate)

        return WalkForwardResult(folds, predictions, aggregate, historical_filter)


def _positive_probability(model, X):
    probabilities = model.predict_proba(X)
    classes = list(model.named_steps["model"].classes_)
    return probabilities[:, classes.index(1)] if 1 in classes else np.zeros(len(X))


def _assign_deployment_windows(deployed_folds):
    for index, fold in enumerate(deployed_folds):
        if index + 1 < len(deployed_folds):
            fold["deployment_end"] = deployed_folds[index + 1]["test_start"] - pd.Timedelta(days=1)
        else:
            fold["deployment_end"] = fold["test_end"]


def _skipped_fold(number, train, test, test_start):
    return {
        "fold": number, "status": "skipped_insufficient_classes",
        "train_rows": len(train), "test_rows": len(test),
        "train_start": train["entry_date"].min().date().isoformat() if len(train) else "",
        "train_end": train["exit_date"].max().date().isoformat() if len(train) else "",
        "test_start": test_start.date().isoformat(),
        "test_end": test["entry_date"].max().date().isoformat(),
        "train_positive_rate": float(train["result"].mean()) if len(train) else np.nan,
        "test_positive_rate": float(test["result"].mean()),
        "accuracy": np.nan, "precision": np.nan, "recall": np.nan,
        "f1": np.nan, "roc_auc": np.nan, "confusion_matrix": "[]",
    }


def _aggregate_metrics(predictions):
    if predictions.empty:
        return {
            "predictions": 0, "accuracy": np.nan, "precision": np.nan,
            "recall": np.nan, "f1": np.nan, "roc_auc": np.nan,
            "confusion_matrix": [], "class_distribution": {},
        }
    actual, predicted = predictions["actual"], predictions["prediction"]
    auc = roc_auc_score(actual, predictions["probability"]) if actual.nunique() == 2 else np.nan
    return {
        "predictions": len(predictions),
        "accuracy": round(accuracy_score(actual, predicted), 4),
        "precision": round(precision_score(actual, predicted, zero_division=0), 4),
        "recall": round(recall_score(actual, predicted, zero_division=0), 4),
        "f1": round(f1_score(actual, predicted, zero_division=0), 4),
        "roc_auc": round(float(auc), 4) if not np.isnan(auc) else np.nan,
        "confusion_matrix": confusion_matrix(actual, predicted, labels=[0, 1]).tolist(),
        "class_distribution": actual.value_counts().sort_index().to_dict(),
    }


def _persist_reports(folds, predictions, aggregate):
    reports = Path("reports")
    reports.mkdir(exist_ok=True)
    folds.to_csv(reports / "ai_walk_forward_folds.csv", index=False, encoding="utf-8-sig")
    predictions.to_csv(reports / "ai_walk_forward_predictions.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([aggregate]).to_csv(
        reports / "ai_walk_forward_summary.csv", index=False, encoding="utf-8-sig"
    )


def _ai_level(probability):
    if probability >= 90:
        return "Very High"
    if probability >= 80:
        return "High"
    if probability >= 70:
        return "Good"
    if probability >= 60:
        return "Moderate"
    return "Weak"
