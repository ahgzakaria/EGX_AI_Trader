"""Compact tabbed stock details without changing analysis or sizing logic."""

import pandas as pd
import streamlit as st

from core.watchlist import Watchlist
from dashboard.ui import section_header
from portfolio.sizing import PositionSizer


def show_stock_details(stock):
    watchlist = Watchlist()
    symbols = watchlist.load()
    title_col, action_col, count_col = st.columns([2, 1, 1])
    title_col.subheader(f"{stock['Ticker']} · {stock['Rating']} · {stock['Signal']}")
    if stock["Ticker"] in symbols:
        if action_col.button("✕ Remove", use_container_width=True):
            watchlist.remove(stock["Ticker"])
            st.rerun()
    else:
        if action_col.button("☆ Add to Watchlist", use_container_width=True):
            watchlist.add(stock["Ticker"])
            st.rerun()
    count_col.metric("Watchlist", len(symbols))

    overview, decision, sizing_tab, chart_tab = st.tabs([
        "Overview", "Decision Trace", "Position Sizing", "Chart & Indicators"
    ])
    with overview:
        _overview(stock)
    with decision:
        _decision_trace(stock)
    with sizing_tab:
        _position_sizing(stock)
    with chart_tab:
        _chart_and_indicators(stock)


def _overview(stock):
    ai_probability = stock.get("AIProbability")
    ai_label = f"{ai_probability}%" if ai_probability is not None else "Not evaluated"
    metrics = st.columns(3)
    metrics[0].metric("Signal", stock["Signal"])
    metrics[1].metric("Confidence", f"{stock['Confidence']}%")
    metrics[2].metric("Strategy Score", stock["Score"])
    metrics = st.columns(3)
    metrics[0].metric("AI Advisory", ai_label)
    metrics[1].metric("AI Level", stock["AILevel"])
    metrics[2].metric("Risk / Reward", f"{stock['RR']:.2f}")

    section_header("Price Levels", "Frozen strategy execution levels")
    levels = st.columns(4)
    levels[0].metric("Current", stock["Price"])
    levels[1].metric("Buy Range", f"{stock['BuyLow']} – {stock['BuyHigh']}")
    levels[2].metric("Stop Loss", stock["StopLoss"])
    levels[3].metric("Target 1 / 2", f"{stock['Target1']} / {stock['Target2']}")
    support = st.columns(2)
    support[0].metric("Support", stock["Support"])
    support[1].metric("Resistance", stock["Resistance"])

    section_header("Decision Summary", "AI is advisory and never overrides Strategy Only")
    if stock["Signal"] == "BUY":
        st.success(
            f"Strategy BUY · Rating {stock['Rating']} · AI advisory {ai_label} · "
            f"R/R {stock['RR']:.2f}"
        )
    elif stock["Signal"] == "WATCH":
        st.warning("Promising setup, but the frozen strategy requires more confirmation.")
    else:
        st.error("The current setup does not satisfy the frozen strategy entry rules.")

    section_header("Key Reasons", "Decision-engine evidence")
    reasons = [item.strip() for item in str(stock["Reasons"]).split("|") if item.strip()]
    if reasons:
        st.write(" · ".join(f"`{reason}`" for reason in reasons))


def _decision_trace(stock):
    regime = stock.get("Regime", "N/A")
    index_regime = stock.get("IndexRegime", "N/A")
    regime_labels = {
        "BULL": "🟢 BULL", "SIDEWAYS": "🟡 SIDEWAYS", "BEAR": "🔴 BEAR"
    }
    columns = st.columns(2)
    columns[0].metric("EGX30 Regime", regime_labels.get(index_regime, index_regime))
    columns[1].metric("Stock Regime", regime_labels.get(regime, regime))

    trace = stock.get("DecisionTrace", {})
    section_header("Gate Results", "The exact unified decision path")
    if trace:
        trace_frame = pd.DataFrame([
            {"Gate": gate, "Result": result} for gate, result in trace.items()
        ])

        def color_result(value):
            text = str(value)
            if "PASS" in text:
                return "color:#059669;font-weight:700"
            if "FAIL" in text or "LOW" in text:
                return "color:#dc2626;font-weight:700"
            return "color:#64748b"

        styled = trace_frame.style
        try:
            styled = styled.map(color_result, subset=["Result"])
        except AttributeError:
            styled = styled.applymap(color_result, subset=["Result"])
        st.dataframe(styled, hide_index=True, use_container_width=True)

    breakdown = stock.get("ConfidenceBreakdown", {})
    if breakdown:
        section_header("Confidence Breakdown", "Contribution by decision component")
        st.bar_chart(pd.Series(breakdown, name="Confidence"), color="#2563eb")

    section_header("Strategy Module Scores", "Raw module contribution")
    scores = pd.DataFrame({
        "Module": ["Trend", "Volume", "Momentum", "Candles", "Breakout"],
        "Score": [
            stock["Trend"], stock["Volume"], stock["Momentum"],
            stock["Candles"], stock["Breakout"],
        ],
    })
    st.dataframe(
        scores, hide_index=True, use_container_width=True,
        column_config={
            "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=30)
        },
    )


def _position_sizing(stock):
    section_header("Risk Inputs", "Calculator only; it does not place an order")
    capital_col, risk_col = st.columns(2)
    capital = capital_col.number_input(
        "Capital", min_value=1000.0, value=100000.0, step=1000.0
    )
    risk = risk_col.slider("Risk Per Trade (%)", 0.25, 5.0, 1.0, 0.25)
    summary = PositionSizer(capital, risk).calculate(
        stock["BuyHigh"], stock["StopLoss"]
    )
    metrics = st.columns(3)
    metrics[0].metric("Suggested Shares", summary["Shares"])
    metrics[1].metric("Position Value", f"{summary['PositionValue']:,.2f}")
    metrics[2].metric("Maximum Loss", f"{summary['MaximumLoss']:,.2f}")
    metrics = st.columns(2)
    metrics[0].metric("Cash Remaining", f"{summary['CashRemaining']:,.2f}")
    metrics[1].metric("Capital Check", "PASS" if summary["EnoughCapital"] else "INSUFFICIENT")


def _chart_and_indicators(stock):
    frame = stock["Data"]
    last = frame.iloc[-1]
    section_header(
        "Price & Decision Levels",
        "Display-only overlay; frozen entry, target, stop and indicators are unchanged",
    )
    chart = frame[[name for name in ("Close", "EMA20", "EMA50", "EMA200", "VWAP") if name in frame]].copy()
    overlays = {
        "Entry": stock.get("BuyHigh"),
        "Target 1": stock.get("Target1"),
        "Target 2": stock.get("Target2"),
        "Stop": stock.get("StopLoss"),
        "Support": stock.get("Support"),
        "Resistance": stock.get("Resistance"),
    }
    for name, value in overlays.items():
        if value is not None:
            chart[name] = float(value)
    st.line_chart(chart.tail(180))
    if "Volume" in frame:
        section_header("Volume", "Source volume; no transformation")
        st.bar_chart(frame[["Volume"]].tail(180), color="#64748b")
    section_header("Latest Indicators", "Last available source candle")
    names = ["EMA20", "EMA50", "EMA200", "RSI", "MACD", "ADX", "ATR", "OBV"]
    indicators = pd.DataFrame({
        "Indicator": names,
        "Value": [
            round(float(last[name]), 2) if name in last and pd.notna(last[name]) else None
            for name in names
        ],
    })
    st.dataframe(indicators, hide_index=True, use_container_width=True)
