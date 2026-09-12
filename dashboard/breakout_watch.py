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

from dashboard.ui import (attrition_bar, empty_state, page_header,
                          section_header)
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


#: Each funnel key as something a reader can read, in the rule's own order:
#: structural gates first, then the trigger and the reach filters.
GATE_LABELS = {
    "PriceIntegrity": "بيانات السعر غير سليمة · price integrity",
    "Liquidity": "سيولة أقل من الحد · liquidity",
    "LongTermTrend": "تحت اتجاهه الطويل · long-term trend",
    "Calm": "تذبذب أعلى من الحد · calm",
    "AboveTrigger": "فوق الزناد بالفعل · already above the trigger",
    "OutOfReach": "بعيد عن الزناد · out of reach",
    "InvalidRisk": "مخاطرة غير صالحة · invalid risk",
    "InsufficientHistory": "تاريخ غير كافٍ · insufficient history",
    "Unusable": "غير قابل للقراءة · unreadable",
}


def _render_funnel(funnel: dict, candidates: int) -> None:
    """How the universe narrowed, drawn to scale.

    This was two tables of (gate, count) side by side -- the explanatory heart
    of the page as a list of numbers, where 135 and 5 are the same size on the
    screen.

    It is drawn as one bar because the counts are a *partition*: `watch.py`
    records which gate refused each symbol FIRST, so every symbol appears
    exactly once and the segments sum to the universe. It is deliberately not
    the cascade of shrinking stages the design sketches -- 230 → 209 → 74 → 28
    would claim a sequence these numbers do not carry.
    """
    refused = sum(int(funnel.get(gate, 0) or 0) for gate in GATE_LABELS)
    universe = refused + max(0, int(candidates))
    section_header(
        "قمع البوابات · Gate funnel",
        f"What refused each of {universe:,} symbols first. Each name is counted "
        f"once, by the first gate to turn it away.")
    st.markdown(
        attrition_bar(
            [(label, funnel.get(gate, 0)) for gate, label in GATE_LABELS.items()],
            survived_label=f"مرشح · approaching the trigger",
            total=universe,
        ),
        unsafe_allow_html=True,
    )
    st.caption(
        f"Structural gates ({', '.join(STRUCTURAL_GATES)}) must all pass now. "
        f"Of the trigger gates ({', '.join(TRIGGER_GATES)}) only the close "
        f"being below the trigger is required here; the other two may already "
        f"hold. A gate that refused nobody is listed at zero rather than "
        f"dropped — a week it was quiet is a fact about the week."
    )


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
        # The funnel exists only on a fresh scan -- the saved CSV carries the
        # survivors and not the counts -- so it is kept here for the Terminal,
        # which reads it and never reconstructs it from the survivors.
        st.session_state["breakout_watch_funnel"] = {
            "counts": dict(funnel), "candidates": len(frame) if frame is not None
            else None, "session": session_date}
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
            _render_funnel(funnel, 0)
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
        _render_funnel(funnel, len(frame))
    else:
        st.caption("The gate funnel is recorded on a fresh scan; a saved list "
                   "carries its candidates only.")

    st.download_button("Download this list as CSV",
                       data=frame.to_csv(index=False).encode("utf-8-sig"),
                       file_name=(source if isinstance(source, str)
                                  and source.endswith(".csv")
                                  else "breakout_watchlist.csv"),
                       mime="text/csv")
