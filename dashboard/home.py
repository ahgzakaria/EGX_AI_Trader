import streamlit as st
import pandas as pd

from core.scanner import scan_symbols


def show_dashboard():

    st.title("📈 EGX AI Trader")

    st.caption("Egyptian Stock Market AI Scanner")

    if st.button("🔍 Scan Market", use_container_width=True):

        results = scan_symbols("data/symbols.csv")

        df = pd.DataFrame(results)

        buy = (df["Signal"] == "BUY").sum()
        watch = (df["Signal"] == "WATCH").sum()
        avoid = (df["Signal"] == "AVOID").sum()

        avg_confidence = round(df["Confidence"].mean(), 1)

        c1, c2, c3, c4, c5 = st.columns(5)

        c1.metric("🟢 BUY", buy)
        c2.metric("🟡 WATCH", watch)
        c3.metric("🔴 AVOID", avoid)
        c4.metric("📈 Stocks", len(df))
        c5.metric("🎯 Avg Confidence", f"{avg_confidence}%")

        st.divider()

        display = df[
            [
                "Ticker",
                "Signal",
                "Stars",
                "Confidence",
                "Score",
                "Price",
                "RR",
                "Reasons",
            ]
        ]

        st.dataframe(
            display,
            use_container_width=True,
            hide_index=True,
        )