import os
import pandas as pd


class ResultsManager:

    def __init__(self):

        self.results = []

    # ==================================

    def add(self, result):

        if result is not None:

            self.results.append(result)

    # ==================================

    def dataframe(self):

        if not self.results:

            return pd.DataFrame()

        return pd.DataFrame(self.results)

    # ==================================

    def save(

        self,

        filename="reports/optimization_results.csv"

    ):

        os.makedirs(

            "reports",

            exist_ok=True

        )

        df = self.dataframe()

        if df.empty:

            return df

        df = df.sort_values(

            [

                "ProfitFactor",

                "NetProfit",

                "Expectancy",

                "WinRate"

            ],

            ascending=False

        )

        df.to_csv(

            filename,

            index=False,

            encoding="utf-8-sig"

        )

        return df

    # ==================================

    def best(self):

        df = self.dataframe()

        if df.empty:

            return None

        df = df.sort_values(

            [

                "ProfitFactor",

                "NetProfit",

                "Expectancy",

                "WinRate"

            ],

            ascending=False

        )

        return df.iloc[0].to_dict()

    # ==================================

    def top(

        self,

        n=10

    ):

        df = self.dataframe()

        if df.empty:

            return df

        return df.sort_values(

            [

                "ProfitFactor",

                "NetProfit",

                "Expectancy",

                "WinRate"

            ],

            ascending=False

        ).head(n)