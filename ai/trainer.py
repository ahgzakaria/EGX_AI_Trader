import joblib
import os

from sklearn.ensemble import RandomForestClassifier

from ai.dataset import DatasetBuilder


class AITrainer:

    def __init__(self):

        self.dataset = DatasetBuilder()

        self.model = RandomForestClassifier(

            n_estimators=300,

            max_depth=10,

            random_state=42

        )

    def train(

        self,

        csv_path="reports/backtest_results.csv"

    ):

        X, y = self.dataset.build(csv_path)

        self.model.fit(X, y)

        return self.model

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

            f"Model saved -> {filename}"

        )


if __name__ == "__main__":

    trainer = AITrainer()

    trainer.train()

    trainer.save()