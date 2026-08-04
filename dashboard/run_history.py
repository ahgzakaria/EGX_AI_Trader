"""Streamlit Run History page for immutable Phase 6 experiments."""

from pathlib import Path

import pandas as pd
import streamlit as st

from services.experiment_tracking import REPORTS_ROOT, RunRepository
from services.run_status import (
    COMPLETED,
    RECOVERED_COMPLETED,
    status_label,
)
from dashboard.scan_archive_panel import (
    render_archive_summary,
    render_download_controls,
)
from dashboard.formatting import with_company_name_column
from dashboard.ui import empty_state, page_header, section_header
from core.universe import read_symbol_frame


def _history_frame(runs):
    rows = []
    for run in runs:
        metrics = run.get("metrics", {})
        # Multi-mode comparison runs contain nested metrics; their overview is
        # intentionally left blank rather than selecting a mode implicitly.
        flat = metrics if not any(isinstance(value, dict) for value in metrics.values()) else {}
        rows.append({
            "Run ID": run.get("run_id"),
            "Date": run.get("created_at"),
            "Execution Time": run.get("execution_time_seconds"),
            "Mode": run.get("mode"),
            "Strategy": run.get("strategy"),
            "Return": flat.get("TotalReturn"),
            "Profit Factor": flat.get("ProfitFactor"),
            "Drawdown": flat.get("MaxDrawdown"),
            "Trades": flat.get("Trades"),
            "Win Rate": flat.get("WinRate"),
            "AI Mode": run.get("ai_mode"),
            "Walk Forward Status": run.get("walk_forward_status"),
            # The effective status shows a run whose archive was published after an
            # interrupted save as Recovered Completed, never as still running.
            "Status": status_label(
                run.get("effective_status") or run.get("status"))[1],
        })
    return pd.DataFrame(rows)


def show_run_history():
    page_header(
        "Run History",
        "Immutable experiments, artifacts and reproducibility metadata",
        icon="🧪",
        badge="EXPERIMENT TRACKING",
    )

    runs = RunRepository.list_runs()
    if not runs:
        empty_state(
            "No tracked runs yet",
            "Run a Live Scan or Backtest to create the first experiment.",
            icon="🧪",
        )
        return

    # A recovered run finished its work; counting it as completed is the honest
    # summary, and the per-run row still says exactly how it completed.
    effective = [run.get("effective_status") or run.get("status") for run in runs]
    completed = sum(status in (COMPLETED, RECOVERED_COMPLETED) for status in effective)
    recovered = sum(status == RECOVERED_COMPLETED for status in effective)
    failed = sum(status == "FAILED" for status in effective)
    metrics = st.columns(4)
    metrics[0].metric("Total Runs", len(runs))
    metrics[1].metric(
        "Completed", completed,
        help=(f"{recovered} recovered after an interrupted archive save"
              if recovered else None))
    metrics[2].metric("Failed", failed)
    metrics[3].metric("Backtests", sum(run.get("run_type") == "BACKTEST" for run in runs))
    if recovered:
        st.caption(
            f"{recovered} run(s) show **Recovered Completed** · تم الاستكمال بعد انقطاع "
            "الحفظ — the scan finished and its dataset was published afterwards from the "
            "intact staging directory. The original run metadata is preserved unchanged."
        )

    section_header("Experiments", "Select a run to inspect, compare or export")
    history = _history_frame(runs)
    type_col, status_col = st.columns(2)
    run_mode = type_col.selectbox(
        "Mode", ["ALL"] + sorted(history["Mode"].dropna().astype(str).unique().tolist())
    )
    # Options come from the rendered labels, so a recovered run is filterable and no
    # option can silently match nothing.
    run_status = status_col.selectbox(
        "Status",
        ["ALL"] + sorted(history["Status"].dropna().astype(str).unique().tolist()),
    )
    visible = history.copy()
    if run_mode != "ALL":
        visible = visible[visible["Mode"] == run_mode]
    if run_status != "ALL":
        visible = visible[visible["Status"] == run_status]
    st.dataframe(
        visible, use_container_width=True, hide_index=True,
        column_config={
            "Return": st.column_config.NumberColumn("Return", format="%.2f%%"),
            "Drawdown": st.column_config.NumberColumn("Drawdown", format="%.2f%%"),
            "Win Rate": st.column_config.NumberColumn("Win Rate", format="%.2f%%"),
            "Execution Time": st.column_config.NumberColumn("Seconds", format="%.1f"),
        },
    )
    run_ids = visible["Run ID"].tolist()
    if not run_ids:
        empty_state("No matching runs", "Adjust the mode or status filter.", icon="⌕")
        return
    selected = st.selectbox("Select Run", run_ids)
    metadata = RunRepository.get(selected)

    open_col, compare_col, export_col, delete_col = st.columns(4)
    with open_col:
        if st.button("📂 Open Report", use_container_width=True):
            st.session_state.open_run_id = selected
    with compare_col:
        if st.button("⚖️ Compare Runs", use_container_width=True):
            st.session_state.compare_run_a = selected
            st.info("Run selected. Open Compare Runs and choose the second run.")
    with export_col:
        st.download_button(
            "📦 Export Run",
            data=RunRepository.export_zip(selected),
            file_name=f"{selected}.zip",
            mime="application/zip",
            use_container_width=True,
        )
    with delete_col:
        if st.button("🗑️ Delete Run", use_container_width=True):
            st.session_state.pending_delete_run = selected

    if st.session_state.get("pending_delete_run") == selected:
        st.warning(f"Delete {selected}? This removes only this archived run.")
        confirm_col, cancel_col = st.columns(2)
        if confirm_col.button("Confirm Delete", type="primary", use_container_width=True):
            RunRepository.delete(selected)
            st.session_state.pop("pending_delete_run", None)
            st.session_state.pop("open_run_id", None)
            st.success("Run deleted.")
            st.rerun()
        if cancel_col.button("Cancel", use_container_width=True):
            st.session_state.pop("pending_delete_run", None)
            st.rerun()

    if st.session_state.get("open_run_id") == selected:
        _show_run(selected, metadata)


def _render_scan_archive(directory):
    """Show schema-aware coverage and downloads when this run is a scan."""

    from services.daily_scan_archive_reader import SchemaStatus, read_archive

    if not (directory / "run_metadata.json").is_file():
        return
    try:
        archive = read_archive(directory)
    except Exception as error:              # never break the page on a bad run
        st.warning(f"archive could not be read: {type(error).__name__}: {error}")
        return
    if archive.schema_status is SchemaStatus.LEGACY_UNKNOWN and \
            not archive.compatibility_decisions:
        return                              # not a scan archive at all
    render_archive_summary(archive)
    render_download_controls(archive)


def _show_run(run_id, metadata):
    st.divider()
    st.subheader(run_id)
    directory = REPORTS_ROOT / run_id
    # A scan archive is interpreted through the versioned reader, so its
    # coverage and decision counts carry their real semantics rather than
    # whatever a raw row count would imply.
    _render_scan_archive(directory)
    st.json(metadata)
    artifacts = sorted(path.name for path in directory.iterdir() if path.is_file())
    selected_artifact = st.selectbox("Artifact", artifacts, key=f"artifact_{run_id}")
    path = directory / Path(selected_artifact).name
    if path.suffix.lower() == ".csv":
        try:
            frame = read_symbol_frame(path)
        except pd.errors.EmptyDataError:
            st.info("This artifact is empty.")
        else:
            # Saved runs stay byte-identical on disk. A ticker-only historical
            # artifact simply GAINS a company-name column when displayed.
            for column in ("Ticker", "Symbol", "symbol", "ticker"):
                if column in frame.columns:
                    frame = with_company_name_column(frame, column)
                    break
            st.dataframe(frame, use_container_width=True, hide_index=True)
    elif path.suffix.lower() in {".md", ".json", ".txt"}:
        st.code(path.read_text(encoding="utf-8-sig"), language=None)
    else:
        st.info("Use Export Run to open this binary artifact.")
