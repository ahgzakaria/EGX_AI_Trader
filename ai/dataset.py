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

        X = df[self.FEATURES].astype("float32")

        y = df[self.TARGET].astype("int32")

        return X, y