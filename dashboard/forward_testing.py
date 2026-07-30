"""Streamlit monitoring pages for Phase 7 persisted forward evidence."""

import pandas as pd
import streamlit as st

from forward_testing.service import ForwardTestingService
from dashboard.formatting import with_company_name_column
from dashboard.ui import empty_state, page_header, section_header


def show_forward_testing():
    page_header(
        "Forward Testing",
        "Immutable live signals evaluated only when future candles become available",
        icon="🛰️",
        badge="OUT-OF-SAMPLE LIVE",
    )
    service = ForwardTestingService()
    data = service.dataframes()
    pending_tab, closed_tab, performance_tab, alerts_tab = st.tabs([
        "⏳ Pending Signals", "✅ Closed Signals",
        "📈 Daily Performance", "🔔 Alerts",
    ])

    with pending_tab:
        _frame_or_message(data["pending"], "No pending BUY signals.")
    with closed_tab:
        _frame_or_message(data["closed"], "No closed signal outcomes yet.")
        _outcome_metrics(data["outcomes"])
        _rolling_signal_charts(data["closed"])
    with performance_tab:
        snapshots = data["snapshots"]
        if snapshots.empty:
            empty_state(
                "No portfolio snapshots yet",
                "A snapshot is created automatically after each Live Scan.",
                icon="📈",
            )
        else:
            snapshots = snapshots.copy()
            snapshots["Timestamp"] = pd.to_datetime(
                snapshots["snapshot_date"] + " " + snapshots["snapshot_time"],
                errors="coerce",
            )
            section_header("Equity Curve", "Forward paper equity")
            st.line_chart(snapshots.set_index("Timestamp")[["equity"]])
            c1, c2, c3 = st.columns(3)
            c1.metric("Latest Equity", f"{snapshots.iloc[-1]['equity']:,.2f}")
            c2.metric("Exposure", f"{snapshots.iloc[-1]['exposure_pct']:.2f}%")
            c3.metric("Current DD", f"{snapshots.iloc[-1]['current_drawdown_pct']:.2f}%")
            section_header("Risk and Exposure", "Open risk, exposure and current drawdown")
            st.line_chart(snapshots.set_index("Timestamp")[[
                "open_risk", "exposure_pct", "current_drawdown_pct"
            ]])
            daily = snapshots.set_index("Timestamp")[["equity"]].copy()
            daily["DailyReturnPercent"] = daily["equity"].pct_change() * 100
            st.dataframe(daily.reset_index(), use_container_width=True, hide_index=True)
    with alerts_tab:
        _frame_or_message(data["alerts"], "No alerts generated yet.")


def show_paper_portfolio():
    page_header(
        "Paper Portfolio",
        "Resume-safe virtual capital using the frozen execution rules",
        icon="💼",
        badge="FORWARD EXECUTION",
    )
    service = ForwardTestingService()
    data = service.dataframes()
    status = service.portfolio_status()
    columns = st.columns(6)
    columns[0].metric("Cash", f"{status['cash']:,.2f}")
    columns[1].metric("Equity", f"{status['equity']:,.2f}")
    columns[2].metric("Open", status["open_positions"])
    columns[3].metric("Exposure", f"{status['exposure_pct']:.2f}%")
    columns[4].metric("Current DD", f"{status['current_drawdown_pct']:.2f}%")
    columns[5].metric("Open Risk", f"{status['open_risk']:,.2f}")

    positions = data["positions"]
    if positions.empty:
        empty_state(
            "No paper positions yet",
            "A valid strategy BUY creates a pending paper entry automatically.",
            icon="💼",
        )
    else:
        open_positions = positions[positions["status"].isin(["PENDING_ENTRY", "OPEN"])]
        closed = positions[positions["status"] == "CLOSED"]
        section_header("Open Positions", "Pending and active paper positions")
        # Historical rows keep their recorded ticker; only the DISPLAY is enriched,
        # so a symbol that has left the active universe stays readable.
        _frame_or_message(with_company_name_column(open_positions, "ticker"),
                          "No open or pending positions.")
        section_header("Closed Trades", "Completed paper executions")
        _frame_or_message(with_company_name_column(closed, "ticker"),
                          "No closed trades.")

    allocation = status["sector_allocation"]
    section_header("Sector Allocation", "Current marked-to-market allocation")
    if allocation:
        st.bar_chart(pd.Series(allocation, name="MarketValue"))
    else:
        empty_state(
            "No active allocation",
            "Sector exposure appears when paper positions are open.",
            icon="◫",
        )

    snapshots = data["snapshots"]
    if not snapshots.empty:
        snapshots = snapshots.copy()
        snapshots["Timestamp"] = pd.to_datetime(
            snapshots["snapshot_date"] + " " + snapshots["snapshot_time"]
        )
        indexed = snapshots.set_index("Timestamp")
        section_header("Equity Curve", "Marked-to-market portfolio value")
        st.line_chart(indexed[["equity"]])
        section_header("Open Risk", "Capital currently at risk")
        st.line_chart(indexed[["open_risk"]])
        section_header("Exposure", "Invested market value as a percentage of equity")
        st.line_chart(indexed[["exposure_pct"]])
        section_header("Rolling Drawdown", "Distance from the running equity peak")
        st.line_chart(indexed[["current_drawdown_pct"]])


def _outcome_metrics(outcomes):
    section_header("Signal Outcome Report", "1, 3, 5, 10 and 20-candle evaluation")
    if outcomes.empty:
        empty_state(
            "No outcomes yet",
            "Results appear only after strictly future candles become available.",
            icon="⏳",
        )
        return
    st.dataframe(outcomes, use_container_width=True, hide_index=True)


def _rolling_signal_charts(closed):
    if closed.empty or "return_pct" not in closed:
        return
    values = closed.copy()
    values["signal_date"] = pd.to_datetime(values["signal_date"], errors="coerce")
    values = values.sort_values("signal_date")
    values["Win"] = (values["return_pct"] > 0).astype(float)
    values["RollingWinRate"] = values["Win"].rolling(20, min_periods=1).mean() * 100
    values["RollingExpectancy"] = values["return_pct"].rolling(20, min_periods=1).mean()
    indexed = values.set_index("signal_date")
    section_header("Rolling Win Rate", "20-signal rolling window")
    st.line_chart(indexed[["RollingWinRate"]])
    section_header("Rolling Expectancy", "20-signal average return")
    st.line_chart(indexed[["RollingExpectancy"]])


def _frame_or_message(frame, message):
    if frame.empty:
        empty_state("Nothing to display yet", message, icon="○")
    else:
        # JSON indicator payloads remain available for audit without forcing a
        # wide nested object into the primary operational tables.
        display = frame.drop(columns=["indicators_json"], errors="ignore")
        st.dataframe(display, use_container_width=True, hide_index=True)
