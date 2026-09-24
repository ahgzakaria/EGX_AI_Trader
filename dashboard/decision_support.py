"""Institutional-style advisory terminal; it cannot execute an order."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from decision_support.analytics import performance_analytics
from decision_support.service import DecisionSupportService
from dashboard.formatting import symbol_option_label, with_company_name_column
from dashboard.ui import empty_state, page_header, section_header


def show_decision_terminal():
    page_header(
        "Decision Support Terminal",
        "Quality ranking over frozen strategy decisions · Paper alerts only",
        icon="🧭",
        badge="ADVISORY ONLY",
    )
    st.info(
        "This screen analyzes and recommends only. It cannot place, submit, or manage an order; "
        "the user always decides Buy, Sell, Wait, Ignore, and position size."
    )
    results = st.session_state.get("results")
    if not results:
        empty_state(
            "No completed market scan",
            "Run Market Scan from Dashboard first. This page never starts a second scan.",
            icon="🔎",
        )
        return

    try:
        service = DecisionSupportService()
        snapshot = service.analyze(results)
    except Exception as error:
        st.error(f"Decision-support analysis could not be generated: {error}")
        return

    rows = snapshot["rows"]
    frame = pd.DataFrame(rows)
    if frame.empty:
        empty_state("No advisory rows", "The completed scan contained no analyzable rows.", icon="⚠️")
        return

    _market_health(snapshot["market_health"], snapshot["sectors"])
    _priority_tabs(frame)
    filtered = _filters(frame)
    if filtered.empty:
        empty_state("No matching opportunities", "Adjust the advisory filters.", icon="⌕")
        return

    _opportunity_table(filtered, snapshot["pinned"])
    _pins(service, frame, snapshot["pinned"])
    _recommendation_card(filtered)
    _heatmaps(frame, snapshot["sectors"])
    _alerts(snapshot["alerts"])
    if snapshot.get("daily_report"):
        st.caption(f"Reproducible daily advisory report: {snapshot['daily_report']}")


def show_decision_analytics():
    page_header(
        "Decision Support Analytics",
        "Closed paper outcomes only · no live order execution",
        icon="📐",
        badge="PAPER EVIDENCE",
    )
    analytics = performance_analytics()
    trades = analytics["trades"]
    if trades.empty:
        empty_state(
            "No closed paper outcomes yet",
            "Analytics will appear after paper positions have immutable exit evidence.",
            icon="🧾",
        )
        return
    summary = st.columns(4)
    summary[0].metric("Closed Paper Trades", len(trades))
    summary[1].metric("Win Rate", f"{(trades['realized_pnl'] > 0).mean() * 100:.1f}%")
    summary[2].metric("Average Return", f"{trades['net_return_percent'].mean():.2f}%")
    summary[3].metric("Average Holding", f"{trades['HoldingMinutes'].mean():.0f} min")
    tabs = st.tabs(["By Setup", "By Hour", "By Weekday", "Limitations"])
    with tabs[0]:
        _analytics_table(analytics["by_setup"], "setup")
    with tabs[1]:
        _analytics_table(analytics["by_hour"], "Hour")
    with tabs[2]:
        _analytics_table(analytics["by_weekday"], "Weekday")
    with tabs[3]:
        st.warning(
            "Sector and market-regime outcome analytics remain unavailable until those immutable "
            "fields are stored with a closed paper position. No missing values are inferred."
        )


def _market_health(health, sectors):
    section_header("Market Health", "Derived from the completed scan; advisory only")
    metrics = st.columns(6)
    metrics[0].metric("Overall", f"{health['overall_market_score']:.2f}/10")
    metrics[1].metric("Bias", health["market_bias"])
    metrics[2].metric("Breadth", f"{health['breadth_percent']:.1f}%")
    metrics[3].metric("Advance / Decline", f"{health['advances']} / {health['declines']}")
    metrics[4].metric("A−D", health["advance_decline"])
    metrics[5].metric(
        "Volume Strength",
        f"{health['volume_strength']:.2f}×" if health["volume_strength"] is not None else "Unavailable",
    )
    if sectors is None or sectors.empty or (sectors["Sector"] == "Unknown").all():
        st.caption("Sector leaders/weakness: unavailable — data/sectors.csv has not been supplied.")
    else:
        st.caption(
            f"Sector leader: {sectors.iloc[0]['Sector']} · Weakest: {sectors.iloc[-1]['Sector']}"
        )


def _priority_tabs(frame):
    section_header("Today's Priority Watchlists", "Edge ordering never replaces Strategy Rank")
    tabs = st.tabs(["Top 5", "Top 10", "Momentum", "Breakouts", "Pullbacks", "Reversals"])
    views = [
        frame.head(5), frame.head(10),
        frame[frame["SetupType"].isin(["MOMENTUM", "MOMENTUM_BREAKOUT"])].head(10),
        frame[frame["SetupType"] == "MOMENTUM_BREAKOUT"].head(10),
        frame[frame["SetupType"] == "PULLBACK"].head(10),
        frame[frame["SetupType"] == "REVERSAL"].head(10),
    ]
    for tab, values in zip(tabs, views):
        with tab:
            if values.empty:
                st.caption("No setup with explicit supporting evidence in this scan.")
            else:
                st.dataframe(
                    values[["EdgeRank", "Ticker", "EdgeScore", "ExistingSignal", "StrategyScore", "RelativeVolume", "SpreadQuality", "LiquidityQuality"]],
                    hide_index=True, width="stretch",
                    column_config=_columns(),
                )


def _filters(frame):
    section_header("Opportunity Explorer", "Quick search and evidence filters")
    search_col, gate_col, signal_col, sector_col = st.columns([2, 1, 1, 1])
    search = search_col.text_input(
        "Quick symbol search", placeholder="COMI.CA", label_visibility="collapsed",
        key="ds_symbol_search",
    ).strip().upper()
    gate = gate_col.selectbox("Quality", ["ALL", "MEETS_CRITERIA", "BLOCKED"], label_visibility="collapsed")
    signals = ["ALL"] + sorted(frame["ExistingSignal"].dropna().astype(str).unique().tolist())
    signal = signal_col.selectbox("Signal", signals, label_visibility="collapsed")
    sectors = ["ALL"] + sorted(frame["Sector"].dropna().astype(str).unique().tolist())
    sector = sector_col.selectbox("Sector", sectors, label_visibility="collapsed")
    edge_col, spread_col, count_col = st.columns([1, 1, 2])
    minimum_edge = edge_col.slider("Minimum Edge", 0.0, 10.0, 0.0, 0.25)
    maximum_spread = spread_col.number_input("Maximum spread %", 0.0, 10.0, 10.0, 0.1)
    filtered = frame.copy()
    if search:
        filtered = filtered[filtered["Ticker"].str.contains(search, regex=False)]
    if gate != "ALL":
        filtered = filtered[filtered["QualityGate"] == gate]
    if signal != "ALL":
        filtered = filtered[filtered["ExistingSignal"] == signal]
    if sector != "ALL":
        filtered = filtered[filtered["Sector"] == sector]
    spread = pd.to_numeric(filtered["SpreadPercent"], errors="coerce")
    filtered = filtered[(filtered["EdgeScore"] >= minimum_edge) & (spread.isna() | (spread <= maximum_spread))]
    count_col.caption(f"Showing {len(filtered)} of {len(frame)} · All original strategy rows remain available")
    return filtered


def _opportunity_table(frame, pinned):
    view = frame.copy()
    view.insert(0, "Pinned", view["Ticker"].isin(pinned))
    available = [
        "Pinned", "EdgeRank", "Ticker", "EdgeScore", "EvidenceCompleteness",
        "ExistingSignal", "StrategyRank", "StrategyScore", "Confidence", "RiskReward",
        "RelativeVolume", "SpreadPercent", "SpreadQuality", "LiquidityScore",
        "LiquidityQuality", "ATRPercent", "Provider", "Freshness", "LatencySeconds",
        "AdvisoryRisk", "QualityGate", "Recommendation",
    ]
    defaults = [
        "Pinned", "EdgeRank", "Ticker", "EdgeScore", "ExistingSignal",
        "StrategyScore", "Confidence", "RelativeVolume", "SpreadPercent",
        "LiquidityQuality", "Provider", "Freshness", "QualityGate", "Recommendation",
        "AdvisoryRisk",
    ]
    columns = st.multiselect(
        "Visible columns", available, default=defaults,
        help="Choose display columns only; this does not change any calculation.",
    )
    if not columns:
        columns = defaults
    st.dataframe(
        with_company_name_column(view[columns], "Ticker")
        if "Ticker" in columns else view[columns],
        hide_index=True, width="stretch",
        height=min(720, 82 + len(view) * 35), column_config=_columns(),
    )


def _pins(service, frame, pinned):
    action_col, pin_col, remove_col = st.columns([2, 1, 1])
    ticker = action_col.selectbox("Pinned-symbol control", frame["Ticker"].tolist(),
                                  format_func=symbol_option_label,
                                  label_visibility="collapsed")
    if pin_col.button("☆ Pin", width="stretch", disabled=ticker in pinned):
        service.database.set_pinned(ticker, True)
        st.rerun()
    if remove_col.button("✕ Unpin", width="stretch", disabled=ticker not in pinned):
        service.database.set_pinned(ticker, False)
        st.rerun()


def _recommendation_card(frame):
    section_header("Recommendation Card", "Neutral criteria-based language; user decision required")
    ticker = st.selectbox("Inspect opportunity", frame["Ticker"].tolist(),
                          format_func=symbol_option_label,
                          label_visibility="collapsed")
    row = frame[frame["Ticker"] == ticker].iloc[0]
    st.subheader(f"#{int(row['EdgeRank'])} · {symbol_option_label(ticker)} · "
                 f"Edge {row['EdgeScore']:.2f}/10")
    if row["QualityGate"] == "MEETS_CRITERIA":
        st.success(row["Recommendation"])
    else:
        st.warning(row["Recommendation"])
    metrics = st.columns(7)
    metrics[0].metric("Current", _format_number(row["CurrentPrice"]))
    metrics[1].metric("Entry Zone", row["EntryZone"])
    metrics[2].metric("Target +2%", _format_number(row["TargetPlus2Percent"]))
    metrics[3].metric("Stop −2%", _format_number(row["StopMinus2Percent"]))
    metrics[4].metric("Frozen R/R", _format_number(row["RiskReward"]))
    metrics[5].metric("Confidence", f"{_format_number(row['Confidence'])}%")
    metrics[6].metric("Advisory Risk", row["AdvisoryRisk"])
    metrics = st.columns(6)
    metrics[0].metric("Liquidity", row["LiquidityQuality"])
    metrics[1].metric("Spread", _percent(row["SpreadPercent"]))
    metrics[2].metric("ATR", _percent(row["ATRPercent"]))
    metrics[3].metric("RVOL", f"{row['RelativeVolume']:.2f}×" if pd.notna(row["RelativeVolume"]) else "Unavailable")
    metrics[4].metric("Sector", row["Sector"])
    metrics[5].metric("Market", row["MarketStatus"])
    st.caption(
        f"Provider: {row['Provider']} · Freshness: {row['Freshness']} · "
        f"Latency: {_seconds(row['LatencySeconds'])} · Evidence: {row['EvidenceCompleteness']:.1f}%"
    )
    info, warning = st.columns(2)
    info.markdown(f"**Technical reasons**  \n{row['TechnicalReasons'] or 'Unavailable'}")
    info.markdown(f"**Invalidation**  \n{row['Invalidation']}")
    info.markdown(f"**Estimated holding**  \n{row['EstimatedHoldingTime']}")
    warning.markdown("**Warnings**")
    warning.write("\n".join(f"- {value}" for value in row["Warnings"]) or "- None observed")
    warning.caption(
        "Bid depth, ask depth and trade count remain unavailable unless the read-only adapter supplies them."
    )


def _heatmaps(frame, sectors):
    section_header("Quality Heat Maps", "Color scales are visual aids, not order signals")
    tabs = st.tabs(["Market", "Sector", "Liquidity", "Momentum", "Edge"])
    with tabs[0]:
        _heat_table(frame, ["Ticker", "EdgeScore", "StrategyScore", "Confidence", "RelativeVolume"])
    with tabs[1]:
        if sectors is None or sectors.empty or (sectors["Sector"] == "Unknown").all():
            st.caption("Sector heat unavailable until data/sectors.csv is supplied.")
        else:
            st.dataframe(sectors, hide_index=True, width="stretch")
    with tabs[2]:
        _heat_table(frame, ["Ticker", "LiquidityScore", "SpreadPercent", "RelativeVolume", "Turnover"])
    with tabs[3]:
        _heat_table(frame, ["Ticker", "MomentumScore", "RelativeVolume", "ATRPercent", "SetupType"])
    with tabs[4]:
        _heat_table(frame, ["Ticker", "EdgeScore", "EvidenceCompleteness", "QualityGate"])


def _heat_table(frame, columns):
    view = frame[columns].copy()
    numeric = view.select_dtypes(include="number").columns.tolist()
    styled = view.style.background_gradient(cmap="RdYlGn", subset=numeric) if numeric else view.style
    st.dataframe(styled, hide_index=True, width="stretch")


def _alerts(rows):
    section_header("Paper Alerts", "Persistent observations only; no order execution")
    if not rows:
        st.caption("No paper alerts recorded.")
        return
    frame = pd.DataFrame(rows)
    st.dataframe(
        frame[["created_at", "alert_type", "ticker", "message", "acknowledged"]],
        hide_index=True, width="stretch",
    )


def _analytics_table(frame, label):
    if frame.empty:
        st.caption("Insufficient closed paper evidence.")
    else:
        st.dataframe(frame, hide_index=True, width="stretch")


def _columns():
    return {
        "Pinned": st.column_config.CheckboxColumn("Pin", width="small"),
        "EdgeRank": st.column_config.NumberColumn("Edge #", width="small"),
        "EdgeScore": st.column_config.ProgressColumn("Edge", min_value=0, max_value=10, format="%.2f"),
        "EvidenceCompleteness": st.column_config.ProgressColumn("Evidence %", min_value=0, max_value=100, format="%.1f%%"),
        "StrategyScore": st.column_config.ProgressColumn("Frozen Score", min_value=0, max_value=100),
        "Confidence": st.column_config.ProgressColumn("Confidence", min_value=0, max_value=100, format="%.0f%%"),
        "RiskReward": st.column_config.NumberColumn("R/R", format="%.2f"),
        "RelativeVolume": st.column_config.NumberColumn("RVOL", format="%.2fx"),
        "SpreadPercent": st.column_config.NumberColumn("Spread %", format="%.3f%%"),
        "LiquidityScore": st.column_config.ProgressColumn("Liquidity", min_value=0, max_value=1, format="%.2f"),
        "ATRPercent": st.column_config.NumberColumn("ATR %", format="%.2f%%"),
        "LatencySeconds": st.column_config.NumberColumn("Latency", format="%.0fs"),
        "Recommendation": st.column_config.TextColumn("Advisory", width="large"),
    }


def _format_number(value):
    return f"{float(value):.2f}" if pd.notna(value) else "Unavailable"


def _percent(value):
    return f"{float(value):.2f}%" if pd.notna(value) else "Unavailable"


def _seconds(value):
    return f"{float(value):.0f}s" if pd.notna(value) else "Unavailable"
