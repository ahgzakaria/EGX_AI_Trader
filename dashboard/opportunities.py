"""Opportunities — focused scenario decision-support workspace (UI only).

This page is the working area for *scenario* decisions, not a second copy of the
Scalping Dashboard or the Expected Range Scanner. It answers five questions:

  1. What is actionable now?            → Ready view
  2. What is closest to actionable?     → Near-Ready view
  3. What exactly is missing?           → Near-Ready "Missing confirmation" + drawer
  4. Why was an opportunity lost?       → Invalidated view (durable transitions)
  5. What was recorded in paper mode?   → paper-state column + drawer Paper Evidence

Presentation only: it renders the cached scan and the durable paper state that the
engine already produced. It never changes a scenario, score, threshold, range,
TP/SL, ranking, flag, provider, database, or any signal/outcome record. Formatters
affect display strings exclusively.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard.formatting import (
    EM_DASH,
    data_quality_label,
    dq_compact,
    fmt_live_price,
    fmt_percent,
    fmt_price,
    fmt_score,
    invalidation_label,
    is_valid_price,
    paper_outcome_label,
    scenario_full_label,
    scenario_label,
    scenario_state_label,
    scenario_state_tone,
)
from dashboard.ui import (
    badge_html,
    egx_holiday_banner,
    empty_state,
    page_header,
    section_header,
    status_bar,
)

_TONE_HEX = {"green": "#34d399", "amber": "#fbbf24", "red": "#f87171",
             "gray": "#94a3b8", "blue": "#60a5fa"}


# --- page -------------------------------------------------------------------


def show_opportunities():
    """Scenario decision-support workspace (reuses the cached pre-session scan)."""
    from dashboard.expected_range_scalper import (
        _build_rows, _run_scan, _session_phase,
    )
    from scalping_expected_range.config import ExpectedRangeConfig

    cfg = ExpectedRangeConfig.load()
    page_header("Opportunities",
                "فرص المضاربة الحالية والسيناريوهات القريبة من التفعيل · "
                "Scenario monitoring · Decision support only",
                icon="🎯", badge="SCALPING V3")

    phase = _session_phase()
    egx_holiday_banner()
    _opp_status_bar(cfg, phase)

    # Shared scan gate — reuses the same cached scan as the Dashboard / Scanner so a
    # tab click never reruns the historical universe scan.
    if st.button("▶ Load / refresh scan · تحميل الفحص", type="primary", key="opp_scan_btn"):
        st.session_state["_ers_scan"] = True
    if "_ers_scan" not in st.session_state:
        empty_state("Load the scan · حمّل الفحص",
                    "Run the pre-session scan to monitor ready, near-ready, invalidated "
                    "and blocked scenarios.", icon="▶")
        return
    try:
        result = _run_scan(True)
    except Exception as error:  # never crash the workspace
        empty_state("Scan unavailable", str(error), icon="⚠")
        return

    universe = result["universe"]
    scenarios = result["scenarios"]
    rows = _build_rows(universe, scenarios)
    live = _live_extras(universe)                       # Bid / QuoteAge, presentation only

    session_date = _latest_session(cfg)
    paper_idx = _paper_index(session_date)              # durable signal/outcome per symbol
    transitions = _transitions(session_date)            # durable state transitions

    _opp_tabs(rows, live, paper_idx, transitions, phase, session_date)
    _opp_drawer(rows, universe, scenarios, live, paper_idx, transitions, phase)

    st.caption(f"Generated {result['generated_at']} · decision-support only · paper mode · "
               f"production disabled")


# --- header / status --------------------------------------------------------


def _opp_status_bar(cfg, phase):
    from dashboard.scalping import _rubix_latest_event, _rubix_path
    ptxt = {"CONTINUOUS": ("EGX OPEN", "green"), "AUCTION": ("CLOSING AUCTION", "amber"),
            "PRE_OPEN": ("PRE-OPEN", "blue"), "CLOSED": ("EGX CLOSED", "gray"),
            "HOLIDAY": ("EGX HOLIDAY", "blue")}.get(phase, (phase, "gray"))
    last_event = _rubix_latest_event()
    status_bar([
        ("", ptxt[0], ptxt[1]),
        ("Rubix", "Connected" if _rubix_path().is_file() else "Down",
         "green" if _rubix_path().is_file() else "red"),
        ("Paper", "Active" if cfg.paper_enabled else "Off", "green" if cfg.paper_enabled else "gray"),
        ("Production", "Disabled", "gray"),
        ("Last refresh", _short_ts(last_event) if last_event else "—", "gray"),
    ])


# --- tabs -------------------------------------------------------------------


def _opp_tabs(rows, live, paper_idx, transitions, phase, session_date):
    # Arabic-primary. After close the first (default) tab reframes to last-observed.
    ready_lbl = "جاهز الآن" if phase == "CONTINUOUS" else "آخر فرص مسجلة"
    tabs = st.tabs([ready_lbl, "قريب من التفعيل", "تم إلغاؤه", "محظور", "كل التقييمات"])
    with tabs[0]:
        _ready_view(rows, live, paper_idx, phase)
    with tabs[1]:
        _near_ready_view(rows, live, phase)
    with tabs[2]:
        _invalidated_view(rows, transitions, paper_idx, phase, session_date)
    with tabs[3]:
        _blocked_view(rows, phase)
    with tabs[4]:
        _all_evaluations_view(rows, live, paper_idx, phase)


# --- 1. Ready view ----------------------------------------------------------


def _ready_view(rows, live, paper_idx, phase):
    st.markdown(badge_html("Decision Support Only", "blue") + "  "
                + badge_html("Paper Mode", "green") + "  "
                + badge_html("Production Disabled", "gray"), unsafe_allow_html=True)
    if phase == "AUCTION":
        st.warning("مزاد الإغلاق — لا دخول جديد (14:15–14:25) · Closing auction — no NEW entries. "
                   "Recorded opportunities are shown for review only.")
    elif phase != "CONTINUOUS":
        st.info("السوق مغلق — آخر الفرص المرصودة (ليست قابلة للتنفيذ الآن) · Market closed — last "
                "observed opportunities, NOT currently actionable.")

    ready = rows[rows["state"] == "ready"].sort_values("Rank")
    if ready.empty:
        _compact_empty("No ready opportunities · لا توجد فرص جاهزة", rows, phase)
        return

    r = ready.merge(live, on="Symbol", how="left")
    valid = [is_valid_price(x) for x in r["Last"]]
    dq = [data_quality_label(data_status=ds, hist_stale=hs, advisory=a, phase=phase, last_valid=v)
          for ds, hs, a, v in zip(r["DataStatus"], r["HistStale"], r["Advisory"], valid)]
    disp = pd.DataFrame({
        "Symbol": r["Symbol"], "Scenario": r["BestScenario"].map(scenario_full_label),
        "Activated": [_activation_time(paper_idx, s) for s in r["Symbol"]],
        "Last": r["Last"].map(fmt_live_price), "Bid": r["Bid"].map(fmt_live_price),
        "Ask": r["Ask"].map(fmt_live_price), "Spread%": r["Spread"].map(lambda x: fmt_percent(x)),
        "Entry": r["Entry"].map(fmt_price), "Target +2%": r["Target"].map(fmt_price),
        "Stop -2%": r["Stop"].map(fmt_price),
        "Room": r["RemainingUpside"].map(lambda x: fmt_percent(x)),
        "Range Pos": r["RangePos"].fillna(0).clip(0, 100),
        "Score": r["Score"].map(fmt_score),
        "Quote age": [_quote_age(a) for a in r["QuoteAge"]],
        "Data Quality": [dq_compact(d[0]) for d in dq],
        "Paper": [paper_outcome_label(_paper_key(paper_idx, s))[0] for s in r["Symbol"]],
    })
    dq_tones = [d[1] for d in dq]
    paper_tones = [paper_outcome_label(_paper_key(paper_idx, s))[1] for s in r["Symbol"]]
    styler = (disp.style.apply(_style(dq_tones), subset=["Data Quality"], axis=0)
              .apply(_style(paper_tones), subset=["Paper"], axis=0))
    sel = st.dataframe(
        styler, use_container_width=True, hide_index=True, on_select="rerun",
        selection_mode="single-row", key="opp_ready",
        column_config={
            "Symbol": st.column_config.TextColumn(width="small", pinned=True),
            "Scenario": st.column_config.TextColumn(width="medium", pinned=True),
            "Range Pos": st.column_config.ProgressColumn(min_value=0, max_value=100,
                                                         format="%d%%", width="small"),
            "Data Quality": st.column_config.TextColumn(
                width="small", help="Compact label; full text + original quote timestamp are "
                                    "in the stock drawer's Data Quality section."),
            "Paper": st.column_config.TextColumn(
                width="small", help="Recorded paper signal / matured outcome only — never live "
                                    "execution. Full evidence is in the drawer.")})
    _select_from(sel, disp, "opp_ready")


# --- 2. Near-ready view -----------------------------------------------------


def _near_ready_view(rows, live, phase):
    section_header("قريب من التفعيل · Near Ready", "closest to confirmation — by proximity to trigger")
    wait = rows[rows["state"] == "waiting"].merge(live, on="Symbol", how="left").copy()
    if wait.empty:
        st.caption("No symbols near confirmation · لا شيء قريب من التفعيل.")
        return
    wait["_valid"] = wait["Last"].map(is_valid_price)
    valid = wait[wait["_valid"]].copy()
    invalid = wait[~wait["_valid"]]

    # Distance to trigger — only from a VALID last price. Never from a zero/missing
    # price, and a historical Trigger alone is not treated as near-ready evidence.
    valid["_last"] = pd.to_numeric(valid["Last"], errors="coerce")
    valid["_entry"] = pd.to_numeric(valid["Entry"], errors="coerce")
    valid = valid[valid["_entry"].notna() & valid["_last"].gt(0)].copy()
    valid["_dist"] = (valid["_entry"] - valid["_last"]) / valid["_last"] * 100.0
    if not valid.empty:
        valid = valid.sort_values("_dist", key=lambda s: s.abs()).head(15)

    if valid.empty:
        st.caption("No near-ready symbols with a valid live price.")
    else:
        dq = [data_quality_label(data_status=ds, hist_stale=hs, advisory=a, phase=phase, last_valid=True)
              for ds, hs, a in zip(valid["DataStatus"], valid["HistStale"], valid["Advisory"])]
        disp = pd.DataFrame({
            "Symbol": valid["Symbol"], "Expected scenario": valid["BestScenario"].map(scenario_full_label),
            "Price": valid["Last"].map(fmt_live_price), "Trigger": valid["Entry"].map(fmt_price),
            "Dist to trigger": valid["_dist"].map(lambda x: fmt_percent(x)),
            "Missing confirmation": [_missing_confirmation(a, r) for a, r in
                                     zip(valid["Advisory"], valid["Reason"])],
            "Room": valid["RemainingUpside"].map(lambda x: fmt_percent(x)),
            "Spread%": valid["Spread"].map(lambda x: fmt_percent(x)),
            "Quote age": [_quote_age(a) for a in valid["QuoteAge"]],
            "Score": valid["Score"].map(fmt_score), "Data Quality": [dq_compact(d[0]) for d in dq],
        })
        dq_tones = [d[1] for d in dq]
        sel = st.dataframe(
            disp.style.apply(_style(dq_tones), subset=["Data Quality"], axis=0),
            use_container_width=True, hide_index=True, on_select="rerun",
            selection_mode="single-row", key="opp_near",
            column_config={
                "Symbol": st.column_config.TextColumn(width="small", pinned=True),
                "Expected scenario": st.column_config.TextColumn(width="medium"),
                "Missing confirmation": st.column_config.TextColumn(width="medium"),
                "Data Quality": st.column_config.TextColumn(width="small")})
        _select_from(sel, disp, "opp_near")
        st.caption("Ranked by distance to trigger from the current valid price — decision support only.")

    if not invalid.empty:
        # Missing-price symbols are separated; distance is never computed from them.
        ref = pd.DataFrame({
            "Symbol": invalid["Symbol"],
            "Expected scenario": invalid["BestScenario"].map(scenario_full_label),
            "Price": [EM_DASH] * len(invalid), "Trigger (ref)": invalid["Entry"].map(fmt_price),
            "Data Quality": ["Last Price Unavailable"] * len(invalid)})
        st.caption(f"Data Unavailable ({len(invalid)}) — no valid last price available; "
                   f"shown for historical trigger reference only, excluded from near-ready ranking:")
        st.dataframe(ref, use_container_width=True, hide_index=True)


# --- 3. Invalidated view ----------------------------------------------------


def _invalidated_view(rows, transitions, paper_idx, phase, session_date):
    from dashboard.formatting import INVALIDATION_STATES
    section_header("تم إلغاؤه · Invalidated", f"scenarios that became invalid · session {session_date or '—'}")
    st.caption("Durable state transitions — the original signal is never deleted or rewritten.")
    if transitions is None or transitions.empty:
        st.caption("No recorded transitions for the latest session.")
        return
    # A symbol/scenario was watching or ready, then transitioned to an invalidating
    # state. Show the LAST such invalidation per (symbol, scenario, cycle).
    inv = transitions[transitions["to_state"].isin(INVALIDATION_STATES)
                      | (transitions["note"] == "NO_NEW_ENTRY_AFTER_1415")].copy()
    inv = inv[inv["from_state"].isin(["READY", "WAIT", "WATCH"]) | inv["to_state"].isin(INVALIDATION_STATES)]
    if inv.empty:
        st.caption("No previously-watching/ready scenario was invalidated in this session.")
        return
    inv = inv.sort_values("id").drop_duplicates(
        subset=["symbol", "scenario", "activation_cycle"], keep="last")
    last_price = {r["Symbol"]: r["Last"] for _, r in rows.iterrows()}
    range_pos = {r["Symbol"]: r["RangePos"] for _, r in rows.iterrows()}
    disp = pd.DataFrame({
        "Symbol": inv["symbol"], "Scenario": inv["scenario"].map(scenario_full_label),
        "Previous state": inv["from_state"].map(scenario_state_label),
        "Invalidated at": inv["at_cairo"].map(_short_ts),
        "Reason": [invalidation_label(t, n) for t, n in zip(inv["to_state"], inv["note"])],
        "Last valid": inv["symbol"].map(lambda s: fmt_live_price(last_price.get(s))),
        "Range Pos": inv["symbol"].map(lambda s: fmt_percent(range_pos.get(s))),
        "Cycle": inv["activation_cycle"],
        "Paper UUID": [_paper_uuid(paper_idx, s) for s in inv["symbol"]],
    })
    st.dataframe(disp, use_container_width=True, hide_index=True,
                 column_config={"Symbol": st.column_config.TextColumn(width="small", pinned=True),
                                "Scenario": st.column_config.TextColumn(width="medium"),
                                "Paper UUID": st.column_config.TextColumn(width="small")})


# --- 4. Blocked view --------------------------------------------------------


def _blocked_view(rows, phase):
    title = {"CONTINUOUS": "محظور · Active Blockers",
             "AUCTION": "محظور — مزاد الإغلاق · Blockers (closing auction)"
             }.get(phase, "محظور — تصنيف آخر فحص · Blockers (last scan classification)")
    section_header(title, "unique-symbol reasons — each symbol counted once")
    groups = _blocker_groups(rows, phase)
    total = len(rows)
    active = [(lbl, syms, tone) for key, lbl, tone, syms in groups if syms]
    if not active:
        st.caption("No blockers — ready opportunities present.")
        return
    if phase != "CONTINUOUS":
        st.caption("After close, quote age alone is NOT an active fault — it is a last-session snapshot.")
    for lbl, syms, tone in active:
        n = len(syms)
        st.markdown(badge_html(f"{lbl}: {n} ({n / total:.0%})", tone), unsafe_allow_html=True)
        with st.expander(f"{lbl} — {n} symbol(s)"):
            st.dataframe(pd.DataFrame({"Symbol": sorted(syms)}),
                         use_container_width=True, hide_index=True, height=min(320, 46 + 30 * n))


# --- 5. All evaluations view ------------------------------------------------


def _all_evaluations_view(rows, live, paper_idx, phase):
    section_header("كل التقييمات · All Evaluations", "searchable · filterable — click a row for the drawer")
    r = rows.merge(live, on="Symbol", how="left")
    f = _eval_filters(r, paper_idx)
    if f.empty:
        empty_state("No matches · لا نتائج", "No evaluations match the active filters.", icon="○")
        return
    valid = [is_valid_price(x) for x in f["Last"]]
    dq = [data_quality_label(data_status=ds, hist_stale=hs, advisory=a, phase=phase, last_valid=v)
          for ds, hs, a, v in zip(f["DataStatus"], f["HistStale"], f["Advisory"], valid)]
    disp = pd.DataFrame({
        "Symbol": f["Symbol"], "Scenario": f["BestScenario"].map(scenario_label),
        "State": f["Advisory"].map(scenario_state_label), "Rank": f["Rank"],
        "Score": f["Score"].map(fmt_score), "Last": f["Last"].map(fmt_live_price),
        "Trigger": f["Entry"].map(fmt_price), "Target": f["Target"].map(fmt_price),
        "Stop": f["Stop"].map(fmt_price), "Range Pos": f["RangePos"].fillna(0).clip(0, 100),
        "Spread%": f["Spread"].map(lambda x: fmt_percent(x)),
        "Data Quality": [dq_compact(d[0]) for d in dq],
        "Cycle": [_paper_cycle(paper_idx, s) for s in f["Symbol"]],
    })
    state_tones = [scenario_state_tone(a) for a in f["Advisory"]]
    dq_tones = [d[1] for d in dq]
    styler = (disp.style.apply(_style(state_tones), subset=["State"], axis=0)
              .apply(_style(dq_tones), subset=["Data Quality"], axis=0))
    sel = st.dataframe(
        styler, use_container_width=True, hide_index=True, on_select="rerun",
        selection_mode="single-row", key="opp_all",
        height=min(640, 46 + 34 * len(disp)),
        column_config={
            "Symbol": st.column_config.TextColumn(width="small", pinned=True),
            "State": st.column_config.TextColumn(width="small", pinned=True),
            "Range Pos": st.column_config.ProgressColumn(min_value=0, max_value=100,
                                                         format="%d%%", width="small"),
            "Data Quality": st.column_config.TextColumn(width="small")})
    _select_from(sel, disp, "opp_all")


def _eval_filters(r, paper_idx):
    top = st.columns([2.2, 1.2, 1.2, 1, 1, 1])
    q = top[0].text_input("Search", key="opp_q", placeholder="Search symbol… · ابحث",
                          label_visibility="collapsed")
    scenarios = ["Any"] + sorted({scenario_label(x) for x in r["BestScenario"].dropna().unique()})
    scen = top[1].selectbox("Scenario", scenarios, key="opp_scen")
    states = ["Any", "ready", "waiting", "no_trade", "rejected"]
    state = top[2].selectbox("State", states, key="opp_state")
    min_score = top[3].number_input("Min Score", 0, 100, 0, key="opp_ms")
    max_spread = top[4].number_input("Max Spread%", 0.0, 10.0, 10.0, step=0.1, key="opp_msp")
    show = top[5].selectbox("Show", ["Top 50", "Top 20", "All"], key="opp_show")
    b = st.columns([1, 1, 1, 3])
    dq_choice = b[0].selectbox("Data", ["Any", "Fresh only", "Hide history-lag"], key="opp_dq")
    valid_only = b[1].checkbox("Valid price only", key="opp_valid")
    paper_only = b[2].checkbox("Paper recorded", key="opp_paper")

    f = r
    active = []
    if q:
        f = f[f["Symbol"].str.contains(q.strip(), case=False, na=False)]; active.append(f"~{q}")
    if scen != "Any":
        f = f[f["BestScenario"].map(scenario_label) == scen]; active.append(scen)
    if state != "Any":
        f = f[f["state"] == state]; active.append(state)
    if min_score:
        f = f[f["Score"] >= min_score]; active.append(f"score≥{min_score}")
    if max_spread < 10.0:
        f = f[pd.to_numeric(f["Spread"], errors="coerce").fillna(99) <= max_spread]
        active.append(f"spread≤{max_spread}%")
    if dq_choice == "Fresh only":
        f = f[f["DataStatus"] == "OK"]; active.append("fresh")
    elif dq_choice == "Hide history-lag":
        f = f[~f["HistStale"].fillna(False)]; active.append("hide-lag")
    if valid_only:
        f = f[f["Last"].map(is_valid_price)]; active.append("valid-price")
    if paper_only:
        f = f[f["Symbol"].map(lambda s: _paper_key(paper_idx, s) != "NONE")]; active.append("paper")
    f = f.sort_values("Rank")
    limit = {"Top 20": 20, "Top 50": 50, "All": len(f)}[show]
    if active:
        st.caption("Filters · " + " · ".join(active) + f" — showing {min(limit, len(f))}/{len(f)}")
    return f.head(limit).reset_index(drop=True)


# --- drawer (reuses the approved side drawer + Opportunity Summary + timeline) --


def _opp_drawer(rows, universe, scenarios, live, paper_idx, transitions, phase):
    symbol = st.session_state.get("_opp_drawer_symbol")
    if not symbol:
        return
    from dashboard.expected_range_scalper import _render_drawer

    @st.dialog(f"🎯 {symbol}", width="large")
    def _drawer():
        _opportunity_summary(symbol, rows, live, paper_idx, phase)
        _transition_timeline(symbol, transitions, paper_idx)
        st.divider()
        _render_drawer(symbol, rows, universe, scenarios)   # existing approved sections
        if st.button("Close · إغلاق", key="opp_drawer_close"):
            st.session_state.pop("_opp_drawer_symbol", None)
            st.rerun()
    _drawer()


def _opportunity_summary(symbol, rows, live, paper_idx, phase):
    section_header("ملخص الفرصة · Opportunity Summary", "current scenario decision context")
    rrow = rows[rows["Symbol"] == symbol]
    if rrow.empty:
        st.info("No scenario row for this symbol.")
        return
    r = rrow.iloc[0]
    lv = live[live["Symbol"] == symbol]
    qage = lv.iloc[0]["QuoteAge"] if not lv.empty else None
    valid = is_valid_price(r["Last"])
    dq_label, dq_tone = data_quality_label(data_status=r["DataStatus"], hist_stale=bool(r["HistStale"]),
                                           advisory=r["Advisory"], phase=phase, last_valid=valid)
    state_lbl = scenario_state_label(r["Advisory"])
    st.markdown(
        badge_html(f"State: {state_lbl}", scenario_state_tone(r["Advisory"]))
        + "  " + badge_html(dq_label, dq_tone)
        + "  " + badge_html(paper_outcome_label(_paper_key(paper_idx, symbol))[0],
                            paper_outcome_label(_paper_key(paper_idx, symbol))[1]),
        unsafe_allow_html=True)
    c = st.columns(4)
    c[0].metric("Scenario", scenario_full_label(r["BestScenario"]))
    c[1].metric("Entry", fmt_price(r["Entry"]))
    c[2].metric("Target +2%", fmt_price(r["Target"]))
    c[3].metric("Stop −2%", fmt_price(r["Stop"]))
    c2 = st.columns(4)
    c2[0].metric("Remaining room", fmt_percent(r["RemainingUpside"]))
    c2[1].metric("Range Pos", fmt_percent(r["RangePos"]))
    c2[2].metric("Quote age", _quote_age(qage))
    c2[3].metric("Activated", _activation_time(paper_idx, symbol))
    st.caption("**Missing confirmation:** " + _missing_confirmation(r["Advisory"], r.get("Reason")))


def _transition_timeline(symbol, transitions, paper_idx):
    section_header("مسار الحالات · State Transition Timeline", "only real transitions — unchanged evals suppressed")
    if transitions is None or transitions.empty:
        st.caption("No recorded transitions for this session.")
        return
    t = transitions[transitions["symbol"] == symbol].sort_values("id")
    # Unchanged evaluations are never recorded as transitions (the engine suppresses
    # them), and we additionally drop any accidental no-op from/to identical states.
    t = t[t["from_state"] != t["to_state"]]
    if t.empty:
        st.caption("No state changes recorded for this symbol — it stayed in one state.")
        return
    disp = pd.DataFrame({
        "Time (Cairo)": t["at_cairo"].map(_short_ts),
        "From": t["from_state"].map(scenario_state_label), "To": t["to_state"].map(scenario_state_label),
        "Reason": [invalidation_label(to, n) if str(to) not in ("READY",) else "Confirmed"
                   for to, n in zip(t["to_state"], t["note"])],
        "Cycle": t["activation_cycle"], "Note": t["note"].replace("", EM_DASH),
    })
    st.dataframe(disp, use_container_width=True, hide_index=True,
                 height=min(280, 46 + 30 * len(disp)))


# --- durable-state loaders (cached; no unnecessary DB scans) -----------------


@st.cache_data(ttl=120)
def _paper_index(session_date):
    """Symbol -> durable paper info {key, label, uuid, cycle, outcome} for the latest
    session. Loads the immutable signals + matured outcomes (records only)."""
    if not session_date:
        return {}
    sig = _safe_csv("reports/expected_range_paper_signals.csv")
    out = _safe_csv("reports/expected_range_paper_outcomes.csv")
    if sig.empty or "Symbol" not in sig:
        return {}
    sig = sig[sig.get("SessionDate").astype(str) == str(session_date)] if "SessionDate" in sig else sig
    idx = {}
    for _, s in sig.iterrows():
        sym = str(s["Symbol"])
        uuid = str(s.get("SignalUUID", ""))
        cycle = s.get("ActivationCycle")
        key, outcome = "RECORDED", None
        if not out.empty and "SignalUUID" in out:
            m = out[out["SignalUUID"] == uuid]
            if not m.empty and "FirstHit" in m and pd.notna(m.iloc[0]["FirstHit"]):
                outcome = str(m.iloc[0]["FirstHit"])
                key = outcome            # TARGET / STOP / NEITHER
            else:
                key = "PENDING"
        idx[sym] = {"key": key, "uuid": uuid, "cycle": cycle, "outcome": outcome,
                    "activated": str(s.get("DecisionTimestampCairo", ""))}
    return idx


@st.cache_data(ttl=120)
def _transitions(session_date):
    if not session_date:
        return pd.DataFrame()
    try:
        from scalping_expected_range.paper_state import PaperStateStore
        tr = PaperStateStore().transitions(session_date)
    except Exception:
        return pd.DataFrame()
    if not tr:
        return pd.DataFrame()
    df = pd.DataFrame(tr)
    for col in ("from_state", "to_state", "note", "symbol", "scenario"):
        if col not in df:
            df[col] = ""
        df[col] = df[col].fillna("")
    return df


def _latest_session(cfg):
    try:
        root = Path(cfg.paper_output_root)
        if root.is_dir():
            dates = sorted([p.name for p in root.iterdir() if p.is_dir() and p.name[:4].isdigit()])
            if dates:
                return dates[-1]
    except Exception:
        pass
    daily = _safe_csv("reports/expected_range_daily_paper_summary.csv")
    if not daily.empty and "SessionDate" in daily:
        return str(daily.iloc[-1]["SessionDate"])
    return None


def _live_extras(universe):
    """Symbol -> Bid / QuoteAge from the universe live overlay (presentation only)."""
    u = universe
    return pd.DataFrame({
        "Symbol": u["Symbol"],
        "Bid": pd.to_numeric(u.get("live_bid"), errors="coerce") if "live_bid" in u else None,
        "QuoteAge": pd.to_numeric(u.get("live_quote_age_seconds"), errors="coerce")
        if "live_quote_age_seconds" in u else None,
    })


# --- blocker grouping (unique-symbol; mirrors the dashboard classifier) -------


def _blocker_groups(rows, phase):
    """[(key, label, tone, [symbols])] — each symbol counted once, session-aware.

    Mirrors dashboard `_dash_classify` semantics so counts reconcile, but also
    carries the affected-symbol list for the expandable view. Outside continuous
    trading a stale LIVE quote is a last-session snapshot, not an active fault.
    """
    continuous = phase == "CONTINUOUS"
    buckets = {k: [] for k in ("rejected_low_liquidity", "no_history", "history_lag",
                               "wide_spread", "live_quote_stale", "last_price_unavailable",
                               "range_consumed", "no_room_other", "last_session_quotes")}
    for _, r in rows.iterrows():
        sym = r["Symbol"]
        state = r.get("state")
        if state in ("ready", "waiting"):
            continue
        if state == "rejected":
            buckets["rejected_low_liquidity"].append(sym); continue
        adv = r.get("Advisory")
        if r.get("DataStatus") == "MISSING":
            buckets["no_history"].append(sym)
        elif bool(r.get("HistStale")):
            buckets["history_lag"].append(sym)
        elif adv == "SPREAD_TOO_WIDE":
            buckets["wide_spread"].append(sym)
        elif not is_valid_price(r.get("Last")) and continuous:
            buckets["last_price_unavailable"].append(sym)
        elif adv == "DATA_STALE":
            buckets["live_quote_stale" if continuous else "last_session_quotes"].append(sym)
        elif adv == "RANGE_CONSUMED":
            buckets["range_consumed"].append(sym)
        else:
            buckets["no_room_other"].append(sym)
    labels = [
        ("rejected_low_liquidity", "Low Liquidity", "red"),
        ("no_history", "No History", "red"),
        ("history_lag", "History Lag", "amber"),
        ("wide_spread", "Wide Spread", "red"),
        ("live_quote_stale", "Live Quote Stale (active)", "red"),
        ("last_price_unavailable", "Last Price Unavailable", "red"),
        ("range_consumed", "Range Consumed", "amber"),
        ("no_room_other", "Target Room Insufficient", "amber"),
        ("last_session_quotes", "Outside Continuous Session", "gray"),
    ]
    return [(k, lbl, tone, buckets[k]) for k, lbl, tone in labels]


# --- small presentation helpers ---------------------------------------------


def _style(tones):
    return lambda _c: [f"color:{_TONE_HEX.get(t, '#94a3b8')};font-weight:700" for t in tones]


def _select_from(sel, disp, key):
    if sel and sel.selection and sel.selection.get("rows"):
        idx = sel.selection["rows"][0]
        st.session_state["_opp_drawer_symbol"] = str(disp.iloc[idx]["Symbol"])


def _compact_empty(title, rows, phase):
    from dashboard.scalping import _BLOCKER_LABELS, _dash_classify
    b = _dash_classify(rows, phase)
    top = sorted(((lbl, b[k]) for k, lbl, _ in _BLOCKER_LABELS if b.get(k, 0) > 0),
                 key=lambda x: -x[1])[:3]
    reason_txt = " · ".join(f"{lbl} ({v})" for lbl, v in top) or "no data"
    subtitle = ("no scenario currently satisfies its rules"
                if phase == "CONTINUOUS" else "none in the last completed scan")
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:1rem;background:var(--surface);'
        f'border:1px solid var(--border);border-left:3px solid #94a3b8;border-radius:11px;'
        f'padding:.7rem .95rem"><div style="font-size:1.2rem">○</div>'
        f'<div><div style="font-weight:750">{title}</div>'
        f'<div style="color:var(--muted);font-size:.8rem">{subtitle} — top reasons: {reason_txt}. '
        f'See Near Ready and Blocked.</div></div></div>', unsafe_allow_html=True)


def _missing_confirmation(advisory, reason):
    """Human 'what is missing for confirmation' for a waiting/near-ready scenario."""
    a = str(advisory)
    table = {
        "WAIT_LOWER_RANGE_BOUNCE": "Price to reach the lower-range trigger",
        "WATCH_HIGH_VOLUME_VOLATILITY": "A confirmed setup on the volatility watch",
        "LOWER_RANGE_NOT_REACHED": "Price to reach the lower-range trigger",
        "FALLING_WITHOUT_CONFIRMATION": "A reclaim/confirmation after the dip",
        "RANGE_CONSUMED": "Range already consumed — no room",
        "DATA_STALE": "A fresh live quote (currently stale)",
        "SPREAD_TOO_WIDE": "Spread to narrow below the gate",
    }
    if a in table:
        return table[a]
    return str(reason) if reason else "Live confirmation pending"


def _activation_time(paper_idx, symbol):
    info = paper_idx.get(str(symbol)) if paper_idx else None
    if info and info.get("activated"):
        return _short_ts(info["activated"])
    return EM_DASH


def _paper_key(paper_idx, symbol):
    info = paper_idx.get(str(symbol)) if paper_idx else None
    return info["key"] if info else "NONE"


def _paper_uuid(paper_idx, symbol):
    info = paper_idx.get(str(symbol)) if paper_idx else None
    u = info.get("uuid") if info else None
    return (u[:8] + "…") if u else EM_DASH


def _paper_cycle(paper_idx, symbol):
    info = paper_idx.get(str(symbol)) if paper_idx else None
    c = info.get("cycle") if info else None
    try:
        return int(c) if c is not None and str(c) != "nan" else EM_DASH
    except (TypeError, ValueError):
        return EM_DASH


def _quote_age(seconds):
    if seconds is None or (isinstance(seconds, float) and pd.isna(seconds)):
        return EM_DASH
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return EM_DASH
    if s < 90:
        return f"{s:.0f}s"
    return f"{s / 60:.0f}m"


def _short_ts(value):
    if not value:
        return EM_DASH
    try:
        ts = pd.Timestamp(value)
        if ts.tzinfo is not None:
            ts = ts.tz_convert("Africa/Cairo")
        return ts.strftime("%d %b %H:%M").lstrip("0")
    except Exception:
        return str(value)[:16]


def _safe_csv(path):
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()
