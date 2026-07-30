"""Watchlist workspace with the same frozen live scanner."""

import pandas as pd
import streamlit as st

from core.scanner import scan_symbols
from core.watchlist import Watchlist
from dashboard.formatting import NAME_COLUMN, with_company_name_column
from dashboard.ui import empty_state, page_header, section_header


def show_watchlist():
    page_header(
        "Watchlist",
        "Focused monitoring for the symbols you care about most",
        icon="⭐",
        badge="MARKET",
    )
    symbols = Watchlist().load()
    if not symbols:
        empty_state(
            "Your watchlist is empty",
            "Open a stock from the Dashboard and add it to the watchlist.",
            icon="☆",
        )
        return

    count_col, action_col = st.columns([1, 3])
    count_col.metric("Tracked Symbols", len(symbols))
    with action_col:
        if st.button(
            "🔍 Scan Watchlist",
            type="primary",
            use_container_width=True,
        ):
            with st.spinner("Scanning watchlist symbols..."):
                st.session_state.watchlist_results = scan_symbols(symbols)

    results = st.session_state.get("watchlist_results")
    if results is None:
        section_header("Symbols", "Ready for a focused scan")
        st.dataframe(
            with_company_name_column(pd.DataFrame({"Ticker": symbols}), "Ticker"),
            hide_index=True,
            use_container_width=True,
        )
        return
    if not results:
        empty_state("No results", "No watchlist market data was returned.", icon="⚠️")
        return

    frame = pd.DataFrame(results).sort_values(
        ["Confidence", "Score"], ascending=False
    )
    metrics = st.columns(4)
    metrics[0].metric("BUY", int((frame["Signal"] == "BUY").sum()))
    metrics[1].metric("WATCH", int((frame["Signal"] == "WATCH").sum()))
    metrics[2].metric("AVOID", int((frame["Signal"] == "AVOID").sum()))
    metrics[3].metric("Average Score", f"{frame['Score'].mean():.1f}")

    section_header("Latest Watchlist Scan", f"{len(frame)} symbols evaluated")
    st.dataframe(
        with_company_name_column(frame[[
            "Rank", "Ticker", "Rating", "Regime", "Signal", "Confidence",
            "Score", "AIProbability", "Price", "RR",
        ]], "Ticker"),
        hide_index=True,
        use_container_width=True,
        column_config={
            NAME_COLUMN: st.column_config.TextColumn("اسم السهم", width="large"),
            "Confidence": st.column_config.ProgressColumn(
                "Confidence", min_value=0, max_value=100, format="%d%%"
            ),
            "Score": st.column_config.ProgressColumn(
                "Score", min_value=0, max_value=100
            ),
            "Price": st.column_config.NumberColumn("Price", format="%.2f"),
            "RR": st.column_config.NumberColumn("R/R", format="%.2f"),
        },
    )
