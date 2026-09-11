"""مراقبة الاختراق · Breakout Watch — the session before Confirmed Breakout.

Confirmed Breakout shows what fired at the last close. This shows what is
approaching the trigger, so a short list can be watched by hand rather than
waited on.

**It decides nothing.** `strategy_momentum_breakout/watch.py` selects; this
formats. Every level, threshold and sentence on screen is produced there from
`signal.measure` and `config.load()`, and the trigger sentence in particular is
rendered exactly as it was built -- rewording it here would put a second version
of the rule in the UI layer, which is the failure `scan.py`'s docstring exists to
prevent.

**It does not rescan on every rerun.** The scan walks the full universe through
`research_router` and takes minutes; a page that ran it on every Streamlit rerun
would be unusable. So the default view reads the newest saved list, both of its
dates are on screen, and a fresh scan is a button.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.ui import empty_state, page_header, section_header
from strategy_momentum_breakout.config import load as load_config
from strategy_momentum_breakout.watch import (DEFAULT_REACH_ATR, HEADER_NOTE,
                                              REACH_MULTIPLES, STRUCTURAL_GATES,
                                              TRIGGER_GATES, as_frame,
                                              available_csvs, dates_from_name,
                                              resize, watch, write_csv)

#: The columns worth a screen, in reading order. The saved file keeps every
#: column; this is what fits without scrolling sideways.
VISIBLE = ("symbol", "close", "prior_high", "distance_percent", "distance_atr",
           "stop_loss_today", "risk_percent", "round_trip_cost_percent",
           "net_2r_after_cost_percent", "shares_at_risk", "position_value",
           "position_percent", "spread_source")

LABELS = {
    "symbol": "Symbol", "close": "Close", "prior_high": "Trigger",
    "distance_percent": "To go %", "distance_atr": "To go (ATR)",
    "stop_loss_today": "Stop today", "risk_percent": "Risk %",
    "round_trip_cost_percent": "Cost %", "net_2r_after_cost_percent": "Net 2R %",
    "shares_at_risk": "Shares", "position_value": "Value",
    "position_percent": "% of capital", "spread_source": "Spread",
}


@st.cache_data(show_spinner=False)
def _fresh_scan(session_key: str, reach_atr: float, capital: float):
    """One universe walk, cached on the session it read and the reach used.

    `session_key` is part of the key rather than decoration: a new session must
    produce a new scan, and the same session must not be walked twice.
    """
    result = watch(reach_atr=reach_atr, capital=capital)
    frame = as_frame(result)
    path = write_csv(result)
    return frame, dict(result.funnel), result.session_date, str(path)


def _load_saved(path):
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except Exception as error:                              # noqa: BLE001
        st.error(f"Could not read {path.name}: {type(error).__name__}")
        return None


def _render_table(frame: pd.DataFrame) -> None:
    columns = [c for c in VISIBLE if c in frame.columns]
    view = frame[columns].rename(columns=LABELS)
    st.dataframe(view, hide_index=True, width="stretch")


def _render_triggers(frame: pd.DataFrame) -> None:
    """The sentence as `watch.py` built it. Not reworded here, by design."""

    if "trigger_sentence" not in frame.columns:
        return
    section_header("عند الإغلاق يوم الزناد · At the close on the trigger day",
                   "All three must be true. Built from the configuration, "
                   "shown unmodified.")
    for _, row in frame.iterrows():
        st.markdown(f"**{row['symbol']}** — {row['trigger_sentence']}")


def _render_funnel(funnel: dict) -> None:
    section_header("قمع البوابات · Gate funnel",
                   "What refused each symbol first. An empty list here means "
                   "nothing is set up, not that the page is broken.")
    structural = pd.DataFrame(
        [{"gate": gate, "refused": funnel.get(gate, 0)} for gate in STRUCTURAL_GATES])
    other = pd.DataFrame([
        {"gate": "already above the trigger — Confirmed Breakout says if it fired",
         "refused": funnel.get("AboveTrigger", 0)},
        {"gate": "out of reach", "refused": funnel.get("OutOfReach", 0)},
        {"gate": "invalid risk", "refused": funnel.get("InvalidRisk", 0)},
        {"gate": "insufficient history",
         "refused": funnel.get("InsufficientHistory", 0)},
        {"gate": "unreadable", "refused": funnel.get("Unusable", 0)},
    ])
    left, right = st.columns(2)
    with left:
        st.caption("Structural gates — must all pass now")
        st.dataframe(structural, hide_index=True, width="stretch")
    with right:
        st.caption(f"Trigger gates ({', '.join(TRIGGER_GATES)}): the close must "
                   f"still be below the trigger; the other two may already hold")
        st.dataframe(other, hide_index=True, width="stretch")


def show_breakout_watch() -> None:
    cfg = load_config()
    page_header("مراقبة الاختراق · Breakout Watch",
                "Names approaching the CONFIRMED_VOLUME_BREAKOUT trigger — the "
                "session before Confirmed Breakout reports one firing.")

    # Persistent and uncollapsed on purpose. This is the sentence that keeps a
    # list of names from reading as a list of recommendations.
    st.info(HEADER_NOTE)

    saved = available_csvs()
    controls = st.columns([2, 1, 1])

    with controls[0]:
        if saved:
            labels = {}
            for path in saved:
                run_date, session = dates_from_name(path)
                labels[path.name] = (
                    f"{path.name}  ·  run {run_date or 'unknown'}"
                    f"  ·  session {session or 'unknown'}")
            chosen = st.selectbox("Saved list",
                                  [p.name for p in saved],
                                  format_func=lambda n: labels.get(n, n))
            selected = next(p for p in saved if p.name == chosen)
        else:
            selected = None
            st.caption("No saved list yet — run a fresh scan.")

    with controls[1]:
        reach = st.selectbox(
            "Reach multiple", list(REACH_MULTIPLES),
            index=list(REACH_MULTIPLES).index(DEFAULT_REACH_ATR),
            format_func=lambda v: f"{v:g} × ATR",
            help="A presentation filter, not a measured threshold. It decides "
                 "how long the list is; nothing in this repository has "
                 "validated it.")

    with controls[2]:
        capital = st.number_input(
            "Capital (EGP)", min_value=0.0, step=1000.0,
            value=float(cfg.initial_capital),
            help=f"Sizes the share columns at the configured "
                 f"{cfg.risk_percent:g}% risk per trade. The default is the "
                 f"configured initial capital.")

    run_fresh = st.button("Run fresh scan", type="primary")

    frame, funnel, session_date, source = None, {}, None, None
    if run_fresh:
        with st.spinner("Walking the universe — this takes a few minutes."):
            frame, funnel, session_date, source = _fresh_scan(
                st.session_state.get("_breakout_watch_key", "fresh"),
                float(reach), float(capital))
        st.success(f"Saved to {source}")
    elif selected is not None:
        frame = _load_saved(selected)
        run_date, session_date = dates_from_name(selected)
        source = selected.name
        st.caption(f"Showing a saved list — run {run_date or 'unknown'}, "
                   f"data closed on {session_date or 'unknown'}. "
                   f"The reach multiple above applies to a fresh scan; a saved "
                   f"list carries whatever it was produced with.")

    if frame is None:
        empty_state("No watchlist yet",
                    "Press 'Run fresh scan' to walk the universe, or run "
                    "scripts/weekly_breakout_watchlist.py from a terminal.",
                    icon="⌕")
        return

    header = st.columns(4)
    header[0].metric("Candidates", len(frame))
    header[1].metric("Session", session_date or "unknown")
    header[2].metric("Reach", f"{reach:g} × ATR")
    header[3].metric("Capital", f"{capital:,.0f}")

    if not len(frame):
        st.info("Nothing is within reach of a trigger on this list. That is the "
                "rule being quiet, not a fault — the funnel below says what "
                "refused each name.")
        if funnel:
            _render_funnel(funnel)
        return

    # Re-derived from columns already on the row, so changing the account size
    # never means walking the universe again.
    sized = resize(frame, float(capital), cfg)

    section_header("المرشحون · Candidates",
                   "Ordered nearest-to-trigger first. That is a reading order, "
                   "not a ranking claim: nothing here has been measured to "
                   "predict which set-up goes on to fire.")
    _render_table(sized)

    st.caption(
        f"Entry on every row: {cfg.entry_mode} — the session AFTER the trigger "
        f"closes. Exit: {cfg.holding_bars} sessions or the stop; this strategy "
        f"has no target. The stop shown is today's and the base low rolls, so "
        f"it must be recomputed on the trigger day.")
    if "sizing_note" in sized.columns and len(sized):
        st.caption(str(sized["sizing_note"].iloc[0]))

    _render_triggers(sized)

    if funnel:
        _render_funnel(funnel)
    else:
        st.caption("The gate funnel is recorded on a fresh scan; a saved list "
                   "carries its candidates only.")

    st.download_button("Download this list as CSV",
                       data=frame.to_csv(index=False).encode("utf-8-sig"),
                       file_name=(source if isinstance(source, str)
                                  and source.endswith(".csv")
                                  else "breakout_watchlist.csv"),
                       mime="text/csv")
