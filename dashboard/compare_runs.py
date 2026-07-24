"""Streamlit comparison page using only saved experiment artifacts."""

import pandas as pd
import streamlit as st

from services.experiment_tracking import RunRepository
from dashboard.ui import empty_state, page_header, section_header


def show_compare_runs():
    page_header(
        "Compare Runs",
        "Side-by-side saved results without recalculating a single trade",
        icon="⚖️",
        badge="REPRODUCIBLE RESEARCH",
    )

    runs = [
        run for run in RunRepository.list_runs()
        if run.get("run_type") == "BACKTEST"
        and isinstance(run.get("metrics"), dict)
        and "TotalReturn" in run.get("metrics", {})
    ]
    if len(runs) < 2:
        empty_state(
            "Two backtests are required",
            "Create another completed Backtest run to unlock comparison.",
            icon="⚖️",
        )
        return

    run_ids = [run["run_id"] for run in runs]
    preferred = st.session_state.get("compare_run_a")
    first_index = run_ids.index(preferred) if preferred in run_ids else 0
    first_col, second_col = st.columns(2)
    first = first_col.selectbox("First Run", run_ids, index=first_index)
    second_choices = [run_id for run_id in run_ids if run_id != first]
    second = second_col.selectbox("Second Run", second_choices)

    comparison = RunRepository.compare(first, second)
    section_header("Performance Metrics", "Saved values from both immutable runs")
    metric_frame = comparison["metrics"].copy()
    first_values = pd.to_numeric(metric_frame[first], errors="coerce")
    second_values = pd.to_numeric(metric_frame[second], errors="coerce")
    metric_frame["Difference"] = second_values - first_values
    st.dataframe(
        metric_frame, use_container_width=True, hide_index=True,
        column_config={
            "Difference": st.column_config.NumberColumn("Δ Second − First", format="%.2f")
        },
    )

    equity = comparison["equity"]
    if not equity.empty and "Equity" in equity:
        x_column = "Date" if "Date" in equity and equity["Date"].notna().any() else "_Sequence"
        if x_column == "_Sequence":
            # Old report formats have no dates, so comparison falls back to a
            # deterministic point sequence rather than inventing timestamps.
            equity = equity.copy()
            equity["_Sequence"] = equity.groupby("RunID").cumcount()
        chart = equity.pivot_table(
            index=x_column,
            columns="RunID",
            values="Equity",
            aggfunc="last",
        )
        section_header("Equity Curves", "Portfolio path comparison")
        st.line_chart(chart)

    for label, key in (("Monthly Returns", "monthly"), ("Yearly Returns", "yearly")):
        frame = comparison[key]
        section_header(label, "Period return comparison")
        if frame.empty:
            st.info(f"No {label.lower()} are available for these runs.")
            continue
        st.dataframe(frame, use_container_width=True, hide_index=True)
        st.bar_chart(frame.pivot(
            index="Period", columns="RunID", values="ReturnPercent"
        ))
