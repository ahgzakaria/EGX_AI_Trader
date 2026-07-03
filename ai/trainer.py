import os
import joblib
import pandas as pd

from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import (
    train_test_split,
    StratifiedKFold,
    cross_val_score
)
from sklearn.metrics import (
    accuracy_score,
    classification_report
)

from ai.dataset import DatasetBuilder


class AITrainer:

    def __init__(self):

        self.dataset = DatasetBuilder()

        self.model = RandomForestClassifier(

            n_estimators=500,

            max_depth=12,

            min_samples_leaf=5,

            random_state=42,

            n_jobs=-1

        )

    # ==================================

    def train(

        self,

        csv_path="reports/backtest_results.csv"

    ):

        X, y = self.dataset.build(csv_path)

        # ==================================
        # Cross Validation
        # ==================================

        cv = StratifiedKFold(

            n_splits=5,

            shuffle=True,

            random_state=42

        )

        scores = cross_val_score(

            self.model,

            X,

            y,

            cv=cv,

            scoring="accuracy",

            n_jobs=-1

        )

        print("\n==============================")
        print("5-FOLD CROSS VALIDATION")
        print("==============================")

        for i, score in enumerate(scores, start=1):

            print(

                f"Fold {i} : {score:.4f}"

            )

        print()

        print(

            f"Average Accuracy : {scores.mean():.4f}"

        )

        print(

            f"Std Deviation    : {scores.std():.4f}"

        )

        # ==================================
        # Final Train/Test
        # ==================================

        X_train, X_test, y_train, y_test = train_test_split(

            X,

            y,

            test_size=0.2,

            random_state=42,

            stratify=y

        )

        self.model.fit(

            X_train,

            y_train

        )

        prediction = self.model.predict(

            X_test

        )

        accuracy = accuracy_score(

            y_test,

            prediction

        )

        print("\n==============================")
        print("TEST SET")
        print("==============================")

        print(

            f"Accuracy : {accuracy:.4f}\n"

        )

        print(

            classification_report(

                y_test,

                prediction

            )

        )

        # ==================================
        # Feature Importance
        # ==================================

        importance = pd.DataFrame({

            "Feature": X.columns,

            "Importance": self.model.feature_importances_

        })

        importance = importance.sort_values(

            "Importance",

            ascending=False

        )

        print("\n==============================")
        print("FEATURE IMPORTANCE")
        print("==============================")

        print(

            importance.to_string(index=False)

        )

        os.makedirs(

            "reports",

            exist_ok=True

        )

        importance.to_csv(

            "reports/feature_importance.csv",

            index=False,

            encoding="utf-8-sig"

        )

        print(

            "\nFeature report saved -> reports/feature_importance.csv"

        )

        return self.model

    # ==================================

    def save(

        self,

        filename="ai/models/trading_model.pkl"

    ):

        os.makedirs(

            "ai/models",

            exist_ok=True

        )

        joblib.dump(

            self.model,

            filename

        )

        print(

            f"\nModel saved -> {filename}"

        )


if __name__ == "__main__":

    trainer = AITrainer()

    trainer.train()

    trainer.save()