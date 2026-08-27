"""Sector liquidity — where the market's turnover is concentrated.

A read-only research view over the sector history built by
`scripts/build_sector_flow.py`. It places no order and produces no signal.

Two things are on this page that a sector screen usually leaves off, because
building it turned on them:

* **the naive baseline, next to the forecast.** Sector turnover share is
  strongly autocorrelated, so a forecast that merely repeats a smoothed version
  of yesterday already looks accurate. The 5-session mean is shown as its own
  column, and the learned model is shown beside it rather than instead of it,
  because the model has not been shown to beat it.
* **which sessions were thrown away.** Sector shares on a half-observed session
  describe the symbols that happened to report, not the market. The panel
  coverage behind every number is stated, and incomplete sessions are excluded.

Turnover is a proxy: EGX reports value traded from intraday VWAP, while the
daily candle contract carries only OHLCV, so `(High+Low+Close)/3 x Volume` is
used throughout. It is not the exchange's own figure.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from config.settings_manager import settings
from dashboard.ui import empty_state, metric_card, page_header, section_header, status_bar
from decision_support.sector_analysis import load_sector_map
from sector_flow.builder import DEFAULT_DATABASE, latest_build_metadata, load_saved
from sector_flow.forecast import baseline_forecast, forecast_next_session
from sector_flow.history import complete_sessions, latest_snapshot, rotation_matrix
from sector_flow.strength import FULL_STRENGTH_RVOL, strength_frame
from sector_flow.intraday import (
    MIN_OPENING_MINUTES,
    forecast_rest_of_day,
    load_minute_turnover,
    session_coverage,
)


HEATMAP_SESSIONS = 60
BUILD_COMMAND = "venv/Scripts/python.exe scripts/build_sector_flow.py"


@st.cache_data(ttl=300, show_spinner=False)
def _daily_history(database_path):
    history = load_saved(database_path)
    return history, latest_build_metadata(database_path)


@st.cache_data(ttl=120, show_spinner=False)
def _intraday(rubix_path, sector_file):
    sector_map = load_sector_map(sector_file)
    if not sector_map:
        return pd.DataFrame()
    try:
        return load_minute_turnover(rubix_path, sector_map)
    except Exception:
        # The live collector database may be locked or absent. The daily view
        # below does not depend on it and must still render.
        return pd.DataFrame()


def _percent(value, digits=2):
    return "—" if pd.isna(value) else f"{float(value) * 100:.{digits}f}%"


def _signed_percent(value, digits=2):
    if pd.isna(value):
        return "—"
    return f"{float(value) * 100:+.{digits}f}%"


def _millions(value):
    return "—" if pd.isna(value) else f"{float(value) / 1_000_000:,.0f}M"


def show_sector_flow():
    page_header(
        "سيولة القطاعات",
        "Where the market's turnover sits, and where it is heading — descriptive only",
        icon="🌊",
        badge="RESEARCH",
    )

    database_path = DEFAULT_DATABASE
    history, metadata = _daily_history(database_path)
    if history is None or history.empty:
        empty_state(
            "No sector history yet",
            f"Build it first: {BUILD_COMMAND}",
            icon="🌊",
        )
        return

    usable = complete_sessions(history)
    snapshot = latest_snapshot(history)
    if snapshot.empty:
        empty_state("No complete session", "Every stored session failed the coverage check.", icon="🌊")
        return

    session = snapshot["SessionDate"].iloc[0].date()
    stored_latest = history["SessionDate"].max().date()
    status_bar([
        ("Session", str(session), "blue"),
        ("Sectors", str(int(snapshot["Sector"].nunique())), "gray"),
        ("Symbols", str(int(snapshot["Symbols"].sum())), "gray"),
        ("Complete sessions", f"{usable['SessionDate'].nunique():,}", "gray"),
        ("Built", str(metadata.get("built_at", "—"))[:16], "gray"),
    ])
    if stored_latest != session:
        st.info(
            f"The most recent stored session ({stored_latest}) did not have enough of the "
            "panel reporting to be ranked — it is in progress or was a partial day. "
            f"Everything below describes {session}."
        )

    _latest_session(snapshot)
    _strength(history)
    _rotation(history)
    _next_session(history)
    _intraday_section(history)
    _provenance(metadata, history, usable)


def _latest_session(snapshot):
    section_header(
        "الجلسة الأخيرة · Latest complete session",
        "Share of market turnover, and how it compares with each sector's own recent norm",
    )

    top = snapshot.head(3)
    columns = st.columns(len(top))
    for column, (_, row) in zip(columns, top.iterrows()):
        with column:
            rvol = row.get("RVOL")
            metric_card(
                row["Sector"],
                _percent(row["TurnoverShare"], 1),
                label_en="of market turnover",
                sub=f"RVOL {rvol:.2f}×" if pd.notna(rvol) else "RVOL —",
                tone="green" if pd.notna(rvol) and rvol > 1 else None,
                tag=_signed_percent(row.get("ShareChange"), 1) + " vs 5 sessions ago",
            )

    table = pd.DataFrame({
        "القطاع": snapshot["Sector"],
        "الحصة": snapshot["TurnoverShare"].map(lambda v: _percent(v, 2)),
        "Δ 5 جلسات": snapshot["ShareChange"].map(lambda v: _signed_percent(v, 2)),
        "RVOL": snapshot["RVOL"].map(lambda v: "—" if pd.isna(v) else f"{v:.2f}×"),
        "Z": snapshot["TurnoverZ"].map(lambda v: "—" if pd.isna(v) else f"{v:+.2f}"),
        "الاتساع": snapshot["Breadth"].map(lambda v: "—" if pd.isna(v) else f"{v:+.2f}"),
        "أسهم": snapshot["Symbols"].astype(int),
        "القيمة": snapshot["Turnover"].map(_millions),
    })
    st.dataframe(table, hide_index=True, use_container_width=True)
    st.caption(
        "RVOL compares the session's turnover with the sector's own median over the "
        "20 sessions *before* it, so a sector is never scored against a window "
        "containing itself. Breadth is (advancers − decliners) ÷ symbols that moved."
    )


def _strength(history):
    section_header(
        "قوة القطاع · Sector strength",
        "The one number on this page that leaves it — it feeds the live Edge score",
    )
    frame = strength_frame(history)
    if frame.empty:
        empty_state("No measurement", "No complete session to measure strength from.")
        return

    st.dataframe(
        pd.DataFrame({
            "القطاع": frame["Sector"],
            "RVOL": frame["RVOL"].map(lambda v: "—" if pd.isna(v) else f"{v:.2f}×"),
            "القوة": frame["SectorStrength"].map(
                lambda v: "—" if pd.isna(v) else f"{v:.2f}"
            ),
        }),
        hide_index=True,
        use_container_width=True,
    )
    st.caption(
        f"Strength is RVOL rescaled onto [0, 1]: a normal session (1.00×) is 0.50 and "
        f"{FULL_STRENGTH_RVOL:.0f}.00× or more is 1.00. It replaces the previous "
        "sector-strength input to the Edge score, which was derived from the Edge and "
        "Momentum of the same rows it then scored — a sector looked strong because its "
        "stocks scored well, and its stocks then scored better because the sector "
        "looked strong. This measurement comes from traded value, which the scan "
        "cannot influence. Sectors with no complete-session measurement keep the older "
        "derived value rather than losing the factor."
    )


def _rotation(history):
    section_header(
        "دوران السيولة · Rotation",
        f"Share of market turnover per sector, last {HEATMAP_SESSIONS} complete sessions",
    )
    matrix = rotation_matrix(history, sessions=HEATMAP_SESSIONS)
    if matrix.empty:
        empty_state("No rotation history", "Not enough complete sessions yet.")
        return

    ordered = matrix[matrix.mean().sort_values(ascending=False).index]
    ordered.index = pd.to_datetime(ordered.index).strftime("%Y-%m-%d")
    st.dataframe(
        ordered.style.format("{:.1%}").background_gradient(cmap="Blues", axis=None),
        use_container_width=True,
    )


def _next_session(history):
    section_header(
        "الجلسة القادمة · Next session",
        "The 5-session mean is the shipped forecaster; the model is shown beside it",
    )
    baseline = baseline_forecast(history)
    if baseline.empty:
        empty_state("No forecast", "Not enough complete sessions.")
        return

    st.warning(
        "Walk-forward over 60,212 out-of-sample predictions: the learned model scores "
        "+0.249 against persistence but only **+0.013 against the 5-session mean**, "
        "which still wins on MAE. That is below the +0.02 threshold this project "
        "treats as usable, so the model column is shown for comparison — not to be "
        "acted on ahead of the baseline."
    )

    try:
        combined = forecast_next_session(history)
    except Exception:
        combined = pd.DataFrame()

    frame = combined if not combined.empty else baseline
    table = pd.DataFrame({
        "القطاع": frame["Sector"],
        "اليوم": frame["TurnoverShare"].map(lambda v: _percent(v, 2)),
        "التوقّع (متوسط 5)": frame.get(
            "Baseline", frame["Predicted"]
        ).map(lambda v: _percent(v, 2)),
        "Δ": frame.get(
            "BaselineChange", frame["Change"]
        ).map(lambda v: _signed_percent(v, 2)),
    })
    if "Baseline" in frame:
        table["الموديل (للمقارنة)"] = frame["Predicted"].map(lambda v: _percent(v, 2))
    st.dataframe(table, hide_index=True, use_container_width=True)


def _intraday_section(history):
    section_header(
        "بقية الجلسة · Rest of today",
        "An even blend of the opening 30 minutes and the previous daily session",
    )
    rubix = settings.get("market_data").get("rubix_db_path")
    minutes = _intraday(rubix, "data/sectors.csv")
    if minutes.empty:
        empty_state(
            "No intraday candles",
            "The live collector has not recorded any minute bars for a mapped symbol.",
        )
        return

    coverage = session_coverage(minutes)
    complete = int(coverage["Complete"].sum())
    st.caption(
        f"{len(coverage)} sessions observed, {complete} complete. Measured over the "
        "complete ones, the blend beats both of its own inputs (MAE 0.0157 against "
        "0.0175 for the previous session alone and 0.0199 for the opening window "
        "alone) — but on only 16 sessions, so it is measured, not established."
    )

    forecast = forecast_rest_of_day(minutes, complete_sessions(history))
    if forecast.empty:
        empty_state(
            "No live forecast",
            f"Fewer than {MIN_OPENING_MINUTES} minutes of the opening window were observed.",
        )
        return

    st.dataframe(
        pd.DataFrame({
            "القطاع": forecast["Sector"],
            "أمس": forecast["PreviousShare"].map(lambda v: _percent(v, 2)),
            "الافتتاح": forecast["OpeningShare"].map(lambda v: _percent(v, 2)),
            "بقية الجلسة": forecast["Forecast"].map(lambda v: _percent(v, 2)),
            "Δ": forecast["Change"].map(lambda v: _signed_percent(v, 2)),
        }),
        hide_index=True,
        use_container_width=True,
    )
    st.caption(
        f"Session {forecast['SessionDate'].iloc[0]} · "
        f"{int(forecast['OpeningMinutesObserved'].iloc[0])} opening minutes observed."
    )


def _provenance(metadata, history, usable):
    with st.expander("المصدر · Data provenance", expanded=False):
        sessions = history["SessionDate"].nunique()
        complete = usable["SessionDate"].nunique() if usable is not None else 0
        st.write(
            {
                "universe_source": metadata.get("universe_source"),
                "symbols_loaded": metadata.get("loaded_symbols"),
                "symbols_classified": metadata.get("classified_symbols"),
                "symbols_unavailable": metadata.get("unavailable_symbols"),
                "symbols_held_for_review": metadata.get("held_symbols"),
                "providers": metadata.get("providers"),
                "sessions_stored": int(sessions),
                "sessions_complete": int(complete),
                "sessions_excluded": int(sessions - complete),
                "turnover_method": metadata.get("turnover_method"),
                "built_at": metadata.get("built_at"),
            }
        )
        st.caption(
            f"{int(sessions - complete):,} stored sessions are excluded for low panel "
            "coverage. The overwhelming majority are Sundays: EODHD is missing Sunday "
            "bars for roughly 40% of EGX symbols before 2026. See "
            "SECTOR_LIQUIDITY_FLOW_REPORT.md."
        )
        held = metadata.get("held_tickers") or []
        if held:
            st.caption(
                f"{len(held)} symbol(s) contribute turnover under a TIER_D hold: "
                f"{', '.join(held)}. Their routing tier keeps them out of automatic "
                "strategy use, but the value they traded is real and belongs in the "
                "market total — excluding it would understate their sectors."
            )
        st.caption(f"Rebuild with: {BUILD_COMMAND}")
