import streamlit as st
import pandas as pd

from core.scanner import scan_symbols
from dashboard.stock_details import show_stock_details


def show_dashboard():

    st.set_page_config(
        page_title="EGX AI Trader",
        page_icon="📈",
        layout="wide"
    )

    st.title("📈 EGX AI Trader")
    st.caption("Egyptian Stock Market AI Scanner")

    # ===================================
    # Session State
    # ===================================

    if "results" not in st.session_state:

        st.session_state.results = None

    # ===================================
    # Scan Button
    # ===================================

    if st.button("🔍 Scan Market", use_container_width=True):

        with st.spinner("Scanning EGX Stocks..."):

            st.session_state.results = scan_symbols("data/symbols.csv")

    # ===================================
    # No Results Yet
    # ===================================

    if st.session_state.results is None:

        st.info("Press 'Scan Market' to start scanning.")

        return

    results = st.session_state.results

    if not results:

        st.error("No data found.")

        return

    df = pd.DataFrame(results)

    # ===================================
    # Sort
    # ===================================

    df = df.sort_values(

        ["AIProbability", "Confidence", "Score"],

        ascending=False

    ).reset_index(drop=True)

    # ===================================
    # Statistics
    # ===================================

    buy = (df["Signal"] == "BUY").sum()
    watch = (df["Signal"] == "WATCH").sum()
    avoid = (df["Signal"] == "AVOID").sum()

    avg_confidence = round(df["Confidence"].mean(), 1)
    avg_score = round(df["Score"].mean(), 1)
    avg_ai = round(df["AIProbability"].mean(), 1)

    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)

    c1.metric("🟢 BUY", buy)
    c2.metric("🟡 WATCH", watch)
    c3.metric("🔴 AVOID", avoid)
    c4.metric("📈 Stocks", len(df))
    c5.metric("🎯 Avg Confidence", f"{avg_confidence}%")
    c6.metric("⭐ Avg Score", avg_score)
    c7.metric("🤖 Avg AI", f"{avg_ai}%")

    st.divider()

    # ===================================
    # Top BUY
    # ===================================

    top_buy = df[df["Signal"] == "BUY"].head(10)

    if len(top_buy):

        st.subheader("🟢 Top BUY Opportunities")

        st.dataframe(

            top_buy[

                [

                    "Rank",
                    "Rating",
                    "Ticker",
                    "AIProbability",
                    "AILevel",
                    "Confidence",
                    "Score",
                    "Price",
                    "RR"

                ]

            ],

            use_container_width=True,

            hide_index=True

        )

    # ===================================
    # Filter
    # ===================================

    signal_filter = st.selectbox(

        "Signal Filter",

        [

            "ALL",
            "BUY",
            "WATCH",
            "AVOID"

        ]

    )

    filtered_df = df.copy()

    if signal_filter != "ALL":

        filtered_df = filtered_df[

            filtered_df["Signal"] == signal_filter

        ]

    # ===================================
    # Market Scan
    # ===================================

    st.subheader("📋 Market Scan")

    display = filtered_df[

        [

            "Rank",
            "Rating",
            "Ticker",
            "Signal",
            "Stars",

            "AIProbability",
            "AILevel",

            "Confidence",
            "Score",

            "Price",
            "RR",

            "Reasons"

        ]

    ]

    st.dataframe(

        display,

        use_container_width=True,

        hide_index=True

    )

    # ===================================
    # Stock Details
    # ===================================

    st.divider()

    st.subheader("📊 Stock Details")

    selected = st.selectbox(

        "Choose a Stock",

        filtered_df["Ticker"].tolist()

    )

    stock = next(

        s for s in results

        if s["Ticker"] == selected

    )

    show_stock_details(stock)