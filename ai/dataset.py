import pandas as pd


class DatasetBuilder:

    FEATURES = [
        "score",
        "confidence",
        "trend_score",
        "volume_score",
        "momentum_score",
        "candle_score",
        "breakout_score",
        "rsi",
        "adx",
        "atr",
        "macd",
        "rr"
    ]

    TARGET = "result"

    def load(self, path):

        df = pd.read_csv(path)

        df["result"] = (
            df["result"]
            .replace({
                "WIN": 1,
                "LOSS": 0,
                "BREAKEVEN": 0
            })
            .astype(int)
        )

        return df

    def build(self, path):

        df = self.load(path)

        X = df[self.FEATURES]

        y = df[self.TARGET]

        return X, y

    def save(self, input_csv, output_csv):

        X, y = self.build(input_csv)

        dataset = X.copy()

        dataset["target"] = y

        dataset.to_csv(
            output_csv,
            index=False,
            encoding="utf-8-sig"
        )

        print(f"Dataset saved -> {output_csv}")