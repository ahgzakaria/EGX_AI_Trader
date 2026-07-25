"""Train the live model only after leakage-safe walk-forward validation."""

import json
import os

import joblib
import pandas as pd

from ai.dataset import DatasetBuilder
from ai.walk_forward import WalkForwardValidator, build_model


class AITrainer:
    def __init__(self):
        self.dataset = DatasetBuilder()
        self.model = None
        self.metadata = {}

    def train(self, csv_path="reports/backtest_results.csv", n_splits=5):
        samples = self.dataset.load(csv_path)
        required = {"entry_date", "exit_date", "result"}
        missing = required.difference(samples.columns)
        if missing:
            raise ValueError(f"Training report missing columns: {sorted(missing)}")

        samples["entry_date"] = pd.to_datetime(samples["entry_date"], errors="coerce")
        samples["exit_date"] = pd.to_datetime(samples["exit_date"], errors="coerce")
        samples = samples.dropna(subset=["entry_date", "exit_date"]).sort_values(
            "entry_date"
        ).reset_index(drop=True)

        if samples["result"].nunique() < 2:
            raise ValueError("AI training requires both WIN and non-WIN samples")

        validation = WalkForwardValidator(n_splits=n_splits).validate(samples)
        if validation.aggregate["predictions"] == 0:
            raise ValueError("No valid out-of-sample walk-forward predictions were produced")

        # A deployed live model may train on all labels known at deployment
        # time. Historical backtests never use this global model.
        self.model = build_model()
        self.model.fit(samples[self.dataset.FEATURES], samples["result"].astype(int))

        forest = self.model.named_steps["model"]
        importance = pd.DataFrame({
            "Feature": self.dataset.FEATURES,
            "Importance": forest.feature_importances_,
        }).sort_values("Importance", ascending=False)
        os.makedirs("reports", exist_ok=True)
        importance.to_csv("reports/feature_importance.csv", index=False, encoding="utf-8-sig")

        self.metadata = {
            "trained_through": samples["exit_date"].max().date().isoformat(),
            "samples": len(samples),
            "class_distribution": samples["result"].value_counts().sort_index().to_dict(),
            "walk_forward": validation.aggregate,
        }

        return {
            "model": self.model,
            "accuracy": round(validation.aggregate["accuracy"] * 100, 2),
            "precision": round(validation.aggregate["precision"] * 100, 2),
            "recall": round(validation.aggregate["recall"] * 100, 2),
            "f1": round(validation.aggregate["f1"] * 100, 2),
            "roc_auc": validation.aggregate["roc_auc"],
            "predictions": validation.aggregate["predictions"],
            "folds": validation.folds,
            "features": importance,
        }

    def save(self, filename="ai/models/trading_model.pkl"):
        if self.model is None:
            raise RuntimeError("Train the AI model before saving it")

        os.makedirs("ai/models", exist_ok=True)
        joblib.dump(self.model, filename)
        metadata_path = os.path.splitext(filename)[0] + ".metadata.json"
        with open(metadata_path, "w", encoding="utf-8") as file:
            json.dump(self.metadata, file, indent=2, default=str)


if __name__ == "__main__":
    trainer = AITrainer()
    print(trainer.train())
    trainer.save()
