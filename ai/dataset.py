import pandas as pd


class DatasetBuilder:

    FEATURES = [

        # ==========================
        # Original
        # ==========================

        "rsi",
        "adx",
        "atr",
        "macd",

        "ema20_dist",
        "ema50_dist",
        "ema200_dist",

        "volume_ratio",

        "atr_percent",

        "bb_position",

        "obv",

        "rr",

        # ==========================
        # AI Features V2
        # ==========================

        "rsi7",

        "ema20_slope",
        "ema50_slope",

        "rsi_slope",

        "adx_rising",

        "bb_width",

        "obv_slope",

        "dist_high20",
        "dist_low20",

        # ==========================
        # AI Features V3
        # ==========================

        "macd_cross_age"

    ]

    TARGET = "result"

    # ==================================

    def load(self, path):

        df = pd.read_csv(path)

        if "result" not in df.columns:
            raise ValueError("Training data must include a 'result' column")

        mapping = {

            "WIN": 1,
            "LOSS": 0,
            "BREAKEVEN": 0

        }

        df["result"] = (

            df["result"]

            .astype(str)

            .str.strip()

            .map(mapping)

            .fillna(0)

            .astype("int32")

        )

        # ==================================
        # Rename Columns
        # ==================================

        rename = {

            "RSI7": "rsi7",

            "EMA20_SLOPE": "ema20_slope",
            "EMA50_SLOPE": "ema50_slope",

            "RSI_SLOPE": "rsi_slope",

            "ADX_RISING": "adx_rising",

            "BB_WIDTH": "bb_width",

            "OBV_SLOPE": "obv_slope",

            "DIST_HIGH20": "dist_high20",
            "DIST_LOW20": "dist_low20",

            "MACD_CROSS_AGE": "macd_cross_age"

        }

        df = df.rename(columns=rename)

        # ==================================
        # تأكد إن كل الـ Features موجودة
        # ==================================

        for feature in self.FEATURES:

            if feature not in df.columns:

                df[feature] = 0

        # ==================================
        # Numeric
        # ==================================

        df[self.FEATURES] = (

            df[self.FEATURES]

            .apply(

                pd.to_numeric,

                errors="coerce"

            )

            .fillna(0)

        )

        return df

    # ==================================

    def build(self, path):

        df = self.load(path)

        # ==================================
        # ترتيب زمني إجباري (مهم جدًا لتقييم صحيح)
        # ==================================
        # لازم الداتا تتاخد بترتيب حدوثها الزمني الحقيقي، عشان
        # الـ Trainer يقدر يعمل Time-Based Split (يتدرب على
        # الماضي، يتقيّم على المستقبل) بدل تقسيم عشوائي بيخلط
        # صفقات من نفس الفترة الزمنية بين التدريب والتقييم
        # (Data Leakage).
        # ==================================

        if "entry_date" in df.columns:

            df = df.sort_values(
                "entry_date"
            ).reset_index(drop=True)

        X = df[self.FEATURES].astype("float32")

        y = df[self.TARGET].astype("int32")

        return X, y

    @classmethod
    def from_trades(cls, trades):
        """Build labelled samples from completed strategy-only trades.

        A row becomes eligible for future model training only after its
        `exit_date`, because that is when the WIN/LOSS label is knowable.
        """
        rows = []
        for trade in trades:
            row = {feature: getattr(trade, feature, 0) for feature in cls.FEATURES}
            row["entry_date"] = trade.entry_date
            row["exit_date"] = trade.exit_date
            row["result"] = 1 if trade.result == "WIN" else 0
            rows.append(row)

        if not rows:
            return pd.DataFrame(columns=[
                "entry_date", "exit_date", "result", *cls.FEATURES
            ])

        df = pd.DataFrame(rows)
        df[cls.FEATURES] = (
            df[cls.FEATURES]
            .apply(pd.to_numeric, errors="coerce")
            .replace([float("inf"), float("-inf")], pd.NA)
        )
        df["entry_date"] = pd.to_datetime(df["entry_date"], errors="coerce")
        df["exit_date"] = pd.to_datetime(df["exit_date"], errors="coerce")
        return df.dropna(subset=["entry_date", "exit_date"]).sort_values(
            "entry_date"
        ).reset_index(drop=True)
