import streamlit as st
import pandas as pd

from core.watchlist import Watchlist
from portfolio.sizing import PositionSizer


def show_stock_details(stock):

    watchlist = Watchlist()

    symbols = watchlist.load()

    st.subheader(f"📊 {stock['Ticker']} Analysis")

    # ==================================
    # Watchlist
    # ==================================

    c1, c2 = st.columns(2)

    if stock["Ticker"] in symbols:

        if c1.button(
            "❌ Remove from Watchlist",
            width="stretch"
        ):

            watchlist.remove(stock["Ticker"])

            st.success("Removed from Watchlist")

            st.rerun()

    else:

        if c1.button(
            "⭐ Add to Watchlist",
            width="stretch"
        ):

            watchlist.add(stock["Ticker"])

            st.success("Added to Watchlist")

            st.rerun()

    c2.metric("Watchlist Size", len(symbols))

    st.divider()

    # ==================================
    # General
    # ==================================

    c1, c2, c3, c4, c5, c6 = st.columns(6)

    c1.metric("Signal", stock["Signal"])

    c2.metric("Rating", stock["Rating"])

    c3.metric("Confidence", f"{stock['Confidence']}%")

    c4.metric("Score", stock["Score"])

    c5.metric("AI", f"{stock['AIProbability']}%")

    c6.metric("Level", stock["AILevel"])

    st.divider()

    # ==================================
    # Price Levels
    # ==================================

    c1, c2, c3 = st.columns(3)

    c1.metric("Current Price", stock["Price"])

    c2.metric("Support", stock["Support"])

    c3.metric("Resistance", stock["Resistance"])

    c1, c2, c3 = st.columns(3)

    c1.metric("Buy Low", stock["BuyLow"])

    c2.metric("Buy High", stock["BuyHigh"])

    c3.metric("Risk / Reward", stock["RR"])

    c1, c2, c3 = st.columns(3)

    c1.metric("Stop Loss", stock["StopLoss"])

    c2.metric("Target 1", stock["Target1"])

    c3.metric("Target 2", stock["Target2"])

    st.divider()

    # ==================================
    # AI Summary
    # ==================================

    st.subheader("🤖 AI Summary")

    if stock["Signal"] == "BUY":

        st.success(

            f"""
AI Probability : **{stock['AIProbability']}%**

Rating : **{stock['Rating']}**

Risk / Reward : **{stock['RR']}**
"""
        )

    elif stock["Signal"] == "WATCH":

        st.warning(
            "The stock looks promising, but confirmation is recommended before entering."
        )

    else:

        st.error(
            "Current setup is weak. Waiting is preferable."
        )

    st.divider()

    # ==================================
    # Portfolio
    # ==================================

    st.subheader("💰 Position Sizing")

    capital = st.number_input(

        "Capital",

        min_value=1000.0,

        value=100000.0,

        step=1000.0

    )

    risk = st.slider(

        "Risk Per Trade (%)",

        0.25,

        5.0,

        1.0,

        0.25

    )

    sizing = PositionSizer(

        capital,

        risk

    )

    summary = sizing.calculate(

        stock["BuyHigh"],

        stock["StopLoss"]

    )

    c1, c2, c3 = st.columns(3)

    c1.metric(

        "Suggested Shares",

        summary["Shares"]

    )

    c2.metric(

        "Position Value",

        f"{summary['PositionValue']:.2f}"

    )

    c3.metric(

        "Maximum Loss",

        f"{summary['MaximumLoss']:.2f}"

    )

    c1, c2 = st.columns(2)

    c1.metric(

        "Cash Remaining",

        f"{summary['CashRemaining']:.2f}"

    )

    if summary["EnoughCapital"]:

        c2.success("✅ Enough Capital")

    else:

        c2.error("❌ Not Enough Capital")

    st.divider()

    # ==================================
    # Strategy Scores
    # ==================================

    st.subheader("Strategy Scores")

    scores = pd.DataFrame({

        "Module": [

            "Trend",

            "Volume",

            "Momentum",

            "Candles",

            "Breakout"

        ],

        "Score": [

            stock["Trend"],

            stock["Volume"],

            stock["Momentum"],

            stock["Candles"],

            stock["Breakout"]

        ]

    })

    st.dataframe(

        scores,

        hide_index=True,

        width="stretch"

    )

    st.divider()

    # ==================================
    # Reasons
    # ==================================

    st.subheader("Reasons")

    reasons = [

        r.strip()

        for r in stock["Reasons"].split("|")

        if r.strip()

    ]

    for reason in reasons:

        st.success(reason)

    st.divider()

    # ==================================
    # Indicators
    # ==================================

    df = stock["Data"]

    last = df.iloc[-1]

    indicators = pd.DataFrame({

        "Indicator": [

            "EMA20",

            "EMA50",

            "EMA200",

            "RSI",

            "MACD",

            "ADX",

            "ATR",

            "OBV"

        ],

        "Value": [

            round(last["EMA20"], 2),

            round(last["EMA50"], 2),

            round(last["EMA200"], 2),

            round(last["RSI"], 2),

            round(last["MACD"], 2),

            round(last["ADX"], 2),

            round(last["ATR"], 2),

            round(last["OBV"], 2)

        ]

    })

    st.subheader("Indicators")

    st.dataframe(

        indicators,

        hide_index=True,

        width="stretch"

    )

    st.divider()

    # ==================================
    # Chart
    # ==================================

    st.subheader("Price Chart")

    st.line_chart(

        df[

            [

                "Close",

                "EMA20",

                "EMA50",

                "EMA200"

            ]

        ],

        width="stretch"

    )