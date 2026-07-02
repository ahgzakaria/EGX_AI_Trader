import streamlit as st
import pandas as pd

from core.watchlist import Watchlist
from core.scanner import scan_symbols


def show_watchlist():

    st.title("⭐ My Watchlist")

    watchlist = Watchlist()

    symbols = watchlist.load()

    if not symbols:

        st.info("Watchlist is empty.")

        return

    st.write(f"Stocks in Watchlist: {len(symbols)}")

    if st.button(
        "🔍 Scan Watchlist",
        use_container_width=True
    ):

        results = scan_symbols("data/symbols.csv")

        results = [

            stock

            for stock in results

            if stock["Ticker"] in symbols

        ]

        if not results:

            st.warning("No watchlist stocks found.")

            return

        df = pd.DataFrame(results)

        df = df.sort_values(

            ["Confidence", "Score"],

            ascending=False

        )

        st.dataframe(

            df[
                [

                    "Rank",

                    "Ticker",

                    "Signal",

                    "Confidence",

                    "Score",

                    "Price",

                    "RR"

                ]

            ],

            hide_index=True,

            use_container_width=True

        )