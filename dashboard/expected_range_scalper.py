"""Expected Range Scalper — professional trading-terminal workspace (UI only).

Presentation and layout only: this page renders what `ExpectedRangeScanner` already
computes. It never changes a scenario, score, threshold, range, TP/SL, flag, or any
numeric value — formatters affect display strings exclusively.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.formatting import (
    EM_DASH,
    NAME_COLUMN,
    company_names,
    with_company_name_column,
    data_quality_label,
    fmt_frequency,
    fmt_percent,
    fmt_price,
    fmt_range,
    fmt_turnover,
    fmt_volume,
    range_zone,
    scenario_label,
    scenario_state_label,
    scenario_state_tone,
    status_label,
    status_tone,
)
from dashboard.ui import (
    badge_html,
    egx_holiday_banner,
    empty_state,
    metric_card,
    page_header,
    range_position_bar,
    section_header,
    status_bar,
)
from scalping_expected_range.config import ExpectedRangeConfig
from scalping_expected_range.paper_evidence import scenario_evidence
from scalping_expected_range.scanner import ExpectedRangeScanner

READY_STATES = {"READY", "BOUNCE_READY"}
WAIT_STATES = {"BOUNCE_WAIT", "WAIT_LOWER_RANGE_BOUNCE", "WATCH_HIGH_VOLUME_VOLATILITY",
               "LOWER_RANGE_NOT_REACHED", "FALLING_WITHOUT_CONFIRMATION"}


def _session_phase():
    """Finer EGX Cairo phase for live-state semantics (auction- and holiday-aware)."""
    from datetime import time
    from core.egx_calendar import is_official_holiday
    from core.egx_session import cairo_now, is_regular_trading_day
    now = cairo_now()
    if is_official_holiday(now.date()):
        return "HOLIDAY"
    if not is_regular_trading_day(now.date()):
        return "CLOSED"
    t = now.timetz().replace(tzinfo=None)
    if t < time(10, 0):
        return "PRE_OPEN"
    if t < time(14, 15):
        return "CONTINUOUS"
    if t < time(14, 25):
        return "AUCTION"
    return "CLOSED"


def _ready_label(phase):
    """(arabic, english, sub, actionable-tone) for the 'ready' metric by phase.

    Old signals are never presented as currently actionable outside continuous trading.
    """
    if phase == "CONTINUOUS":
        return ("جاهز الآن", "Ready Now", "live scenario rules satisfied", "green")
    if phase == "AUCTION":
        return ("مزاد — لا دخول جديد", "Auction — No New Entries", "closing auction 14:15–14:25", "gray")
    return ("جاهز عند آخر فحص", "Ready at Last Scan", "last observed — not currently actionable", "blue")


@st.cache_data(show_spinner="Scanning EGX universe (liquidity-first)…", ttl=300)
def _run_scan(with_live: bool):
    result = ExpectedRangeScanner(config=ExpectedRangeConfig.load()).scan(with_live=with_live)
    return {"universe": result["universe"], "scenarios": result["scenarios"],
            "views": result["views"], "summary": result["summary"],
            "generated_at": result["generated_at"]}


@st.cache_data(ttl=120)
def _load_evidence():
    ev = scenario_evidence(cfg=ExpectedRangeConfig.load())
    return ev["per_scenario"], ev["overall"]


# --- page -------------------------------------------------------------------


def show_expected_range_scalper():
    cfg = ExpectedRangeConfig.load()
    page_header("Expected Range Scalper",
                "المرشحون حسب السيولة + النطاق المتوقع + سيناريوهات الدخول الحية · Decision-support only",
                icon="📐", badge="SCALPING V3")

    egx_holiday_banner()
    _session_status_bar(cfg)

    with_live = st.toggle("Include live Rubix quote snapshot · لقطة رابيكس الحية", value=True)
    run = st.button("▶ Run pre-session scan · تشغيل الفحص", type="primary")
    if run:
        st.session_state["_ers_scan"] = True
    if "_ers_scan" not in st.session_state:
        empty_state("Ready · جاهز", "Run the pre-session scan to rank liquidity-first candidates.",
                    icon="▶")
        return
    try:
        result = _run_scan(with_live)
    except Exception as error:  # never crash the workspace
        empty_state("Scan unavailable", str(error), icon="⚠")
        return

    universe = result["universe"]
    scenarios = result["scenarios"]
    summary = result["summary"]
    rows = _build_rows(universe, scenarios)

    _primary_cards(rows, summary)
    _stale_alert(universe, rows)
    _main_tabs(rows, scenarios, universe, cfg)

    with st.expander("Paper forward evidence · أدلة التتبع الورقي", expanded=False):
        _paper_status(cfg)
    st.caption(f"Generated {result['generated_at']} · fixed TP +{cfg.fixed_take_profit_percent:.0f}% / "
               f"SL −{cfg.fixed_stop_loss_percent:.0f}% · decision-support only")

    # Stock drawer (opens on row select or explicit choice).
    _maybe_open_drawer(rows, universe, scenarios)


# --- session status bar (Phase 4A) ------------------------------------------


def _session_status_bar(cfg):
    from core.egx_session import egx_session_phase
    phase = egx_session_phase()
    phase_map = {"OPEN": ("EGX OPEN", "green"), "PRE_OPEN": ("PRE-OPEN", "blue"),
                 "POST_CLOSE": ("EGX CLOSED", "gray"), "WEEKEND": ("WEEKEND", "gray"),
                 "HOLIDAY": ("EGX HOLIDAY", "blue")}
    ptxt, ptone = phase_map.get(phase, (phase, "gray"))
    latest = _safe_latest_session()
    status_bar([
        ("", ptxt, ptone),
        ("History", latest or "?", "blue" if latest else "amber"),
        ("Rubix", "Connected" if _rubix_ok() else "Unavailable", "green" if _rubix_ok() else "red"),
        ("Bridge", "Rubix-derived", "green"),
        ("Paper", "Active" if cfg.paper_enabled else "Off", "green" if cfg.paper_enabled else "gray"),
        ("Production", "Disabled", "gray"),
    ])


def _safe_latest_session():
    try:
        from core.daily_bridge.normalized_cache import NormalizedDailyCache
        return NormalizedDailyCache(cfg_path()).latest_final_session("RUBIX_DERIVED")
    except Exception:
        return None


def cfg_path():
    return ExpectedRangeConfig.load().normalized_daily_cache_path


def _rubix_ok():
    try:
        from pathlib import Path
        from config.settings_manager import settings
        return Path(settings.get("market_data").get("rubix_db_path",
                    "data/rubix_live_market.db")).is_file()
    except Exception:
        return False


# --- primary cards (Phase 4B) -----------------------------------------------


def _primary_cards(rows, summary):
    total = len(rows)                                     # unique symbols scanned
    tradable = int(rows["Tradable"].sum())               # passed the liquidity gate
    rejected = total - tradable                           # NOT tradable (low liquidity)
    ready = int((rows["state"] == "ready").sum())        # tradable + live ready
    waiting = int((rows["state"] == "waiting").sum())    # tradable + live waiting
    no_trade = int((rows["state"] == "no_trade").sum())  # tradable + no room / stale live

    phase = _session_phase()
    r_ar, r_en, r_sub, r_tone = _ready_label(phase)
    c = st.columns(4)
    with c[0]:
        metric_card("مرشحون قابلون للتداول", tradable, "Tradable Candidates",
                    f"of {total} symbols · passed liquidity gate", "blue", tag="symbol · historical")
    with c[1]:
        metric_card(r_ar, ready, r_en, r_sub, r_tone,
                    tag="symbol · live" if phase == "CONTINUOUS" else "symbol · last scan")
    with c[2]:
        metric_card("انتظار", waiting, "Watch / Waiting", "pending live confirmation",
                    "amber", tag="symbol · live" if phase == "CONTINUOUS" else "symbol · last scan")
    with c[3]:
        metric_card("مرفوض", rejected, "Rejected", "low liquidity — not a candidate",
                    "red", tag="symbol · historical")

    # Item 5: the long reconciliation moves into a compact expander.
    with st.expander("How counts are calculated · كيف تُحسب الأعداد"):
        st.markdown(
            f"- **Tradable ({tradable}) + Rejected ({rejected}) = {tradable + rejected} unique symbols** "
            f"— a clean split of the {total} scanned universe (symbol-level, historical liquidity gate).\n"
            f"- **Ready / Waiting are live sub-states of the {tradable} tradable symbols**, so they do "
            f"*not* add to the split. Of the tradable set: Ready **{ready}** · Waiting **{waiting}** · "
            f"No-room/stale-live **{no_trade}**.\n"
            f"- Historical selection context: high-volume **{summary.get('high_volume_candidates', 0)}**, "
            f"expected-2% **{summary.get('expected_2pct_candidates', 0)}**.\n"
            + ("- Outside continuous trading, the Ready count reflects the **last scan**, not currently "
               "actionable signals." if phase != "CONTINUOUS" else ""))


# --- stale alert (Phase 4I) -------------------------------------------------


def _short_date(d):
    """A compact human date like '22 Jul' (exact ISO stays in the details expander)."""
    try:
        return pd.to_datetime(d).strftime("%d %b").lstrip("0")
    except Exception:
        return str(d)


def _max_date(frame, column):
    """Max parseable date in a column, or None. Column-safe (no Series truthiness)."""
    if column not in frame.columns:
        return None
    dates = []
    for x in frame[column].dropna():
        try:
            dates.append(pd.to_datetime(x).date())
        except (ValueError, TypeError):
            continue
    return max(dates) if dates else None


def _stale_alert(universe, rows):
    """Compact, phase-aware disclosure. After close, a stale live quote is expected
    (last-session snapshot), NOT a warning; global cache latest vs expected dates are
    disclosed separately and the dataset is never called stale when it is up to date."""
    if "prov_data_status" not in universe.columns:
        return
    phase = _session_phase()
    hist_lag = universe[universe["prov_data_status"].isin(["DATA_STALE", "DATA_INSUFFICIENT"])]
    missing = universe[universe["prov_data_status"] == "MISSING"]
    live_stale = int((rows["Advisory"] == "DATA_STALE").sum())

    global_latest = _max_date(universe, "prov_latest_completed_session")
    expected = _max_date(universe, "prov_expected_latest_session")
    global_behind = bool(global_latest and expected and global_latest < expected)

    chips = []
    # Market state — after close, a snapshot, not a live-stale warning.
    if phase == "CONTINUOUS":
        if live_stale:
            chips.append(badge_html(f"⚠ Live quote stale · {live_stale} symbols", "red"))
    else:
        chips.append(badge_html("MARKET CLOSED — LAST SESSION SNAPSHOT", "gray"))

    # Clear, separate history disclosure (never call the whole dataset stale when the
    # global latest date is at/after the expected completed date).
    if global_latest:
        chips.append(badge_html(f"Global history through {_short_date(global_latest)}",
                                "amber" if global_behind else "green"))
    if global_behind:
        chips.append(badge_html(f"Global history behind expected {_short_date(expected)}", "amber"))
    elif not hist_lag.empty:
        chips.append(badge_html(f"{len(hist_lag)} symbols lagging", "amber"))
    if not missing.empty:
        chips.append(badge_html(f"{len(missing)} no history", "red"))

    if not chips:
        return
    st.markdown("  ".join(chips), unsafe_allow_html=True)
    if not hist_lag.empty:
        with st.expander(f"{len(hist_lag)} symbols behind expected history — last range preserved, never AVOID"):
            st.dataframe(
                with_company_name_column(hist_lag, "Symbol")[[
                    "Symbol", NAME_COLUMN, "prov_latest_completed_session",
                    "prov_data_status", "prov_data_age_sessions",
                ]].rename(columns={
                    "prov_latest_completed_session": "Latest",
                    "prov_data_status": "Status",
                    "prov_data_age_sessions": "Sessions behind"}),
                use_container_width=True, hide_index=True)


# --- main tabs (Phase 4C) ---------------------------------------------------


def _main_tabs(rows, scenarios, universe, cfg):
    # Arabic-primary tab labels (self-explanatory — no permanent helper line).
    tabs = st.tabs(["جاهز الآن", "أفضل المرشحين", "انتظار", "مرفوض", "كل الأسهم"])
    with tabs[1]:
        _candidate_table(rows[rows["Tradable"]].sort_values("Rank"), "best")
    with tabs[0]:
        _ready_now(rows[rows["state"] == "ready"], scenarios)
    with tabs[2]:
        _candidate_table(rows[rows["state"] == "waiting"].sort_values("Rank"), "waiting")
    with tabs[3]:
        _candidate_table(rows[rows["state"] == "rejected"].sort_values("Rank"), "rejected")
    with tabs[4]:
        _candidate_table(rows.sort_values("Rank"), "all")


def _filter_bar(frame, key):
    """Compact filter row (primary filters visible) + quick filters. Returns filtered."""
    top = st.columns([2.4, 1, 1, 1.1, 1.1, 1])
    q = top[0].text_input("Search", key=f"f_q_{key}", placeholder="Search symbol… · ابحث عن سهم",
                          label_visibility="collapsed")
    min_score = top[1].number_input("Min Score", 0, 100, 0, key=f"f_sc_{key}")
    min_adr = top[2].number_input("Min ADR%", 0.0, 20.0, 0.0, step=0.5, key=f"f_adr_{key}")
    max_spread = top[3].number_input("Max Spread%", 0.0, 10.0, 10.0, step=0.1, key=f"f_sp_{key}")
    data_q = top[4].selectbox("Data", ["Any", "Fresh only", "Hide stale"], key=f"f_dq_{key}")
    show = top[5].selectbox("Show", ["Top 20", "Top 50", "All"], key=f"f_show_{key}")
    with st.popover("⚙ More filters · فلاتر إضافية"):
        min_turn = st.number_input("Min Turnover (EGP)", 0, key=f"f_turn_{key}")
        min_vol = st.number_input("Min Avg Volume", 0, key=f"f_vol_{key}")
        if st.button("Reset filters · إعادة الضبط", key=f"f_rst_{key}"):
            for k in list(st.session_state):
                if k.endswith(f"_{key}"):
                    del st.session_state[k]
            st.rerun()

    f = frame
    active = []
    if q:
        f = f[f["Symbol"].str.contains(q.strip(), case=False, na=False)]; active.append(f"~{q}")
    if min_score:
        f = f[f["Score"] >= min_score]; active.append(f"score≥{min_score}")
    if min_adr:
        f = f[f["ADR"].fillna(0) >= min_adr]; active.append(f"ADR≥{min_adr}%")
    if max_spread < 10.0:
        f = f[f["Spread"].fillna(99) <= max_spread]; active.append(f"spread≤{max_spread}%")
    if data_q == "Fresh only":
        f = f[f["DataStatus"] == "OK"]; active.append("fresh")
    elif data_q == "Hide stale":
        f = f[~f["HistStale"]]; active.append("hide-stale")
    if st.session_state.get(f"f_turn_{key}"):
        f = f[f["AvgTurnover"].fillna(0) >= st.session_state[f"f_turn_{key}"]]; active.append("turnover")
    if st.session_state.get(f"f_vol_{key}"):
        f = f[f["AvgVolume"].fillna(0) >= st.session_state[f"f_vol_{key}"]]; active.append("volume")
    limit = {"Top 20": 20, "Top 50": 50, "All": len(f)}[show]
    if active:
        st.caption("Filters · " + " · ".join(active) + f" — showing {min(limit, len(f))}/{len(f)}")
    return f.head(limit)


_TONE_HEX = {"green": "#34d399", "amber": "#fbbf24", "red": "#f87171",
             "gray": "#94a3b8", "blue": "#60a5fa"}


def _candidate_table(frame, key):
    if frame.empty:
        empty_state("No symbols · لا توجد أسهم", "No rows match this view.", icon="○")
        return
    view = _filter_bar(frame, key).reset_index(drop=True)
    if view.empty:
        empty_state("No matches · لا نتائج", "No symbols match the active filters.", icon="○")
        return
    # A small ⚠ prefix only for GENUINELY history-stale symbols (not market-wide live stale).
    symbols = [f"⚠ {s}" if hs else s for s, hs in zip(view["Symbol"], view["HistStale"])]
    # Separate the three concepts: Scenario (what) · State (disposition) · Data Quality.
    dq = [data_quality_label(data_status=ds, hist_stale=hs, advisory=a)
          for ds, hs, a in zip(view["DataStatus"], view["HistStale"], view["Advisory"])]
    disp = pd.DataFrame({
        "Symbol": symbols, NAME_COLUMN: company_names(view["Symbol"]),
        "Rank": view["Rank"], "Score": view["Score"].round(1),
        "Avg Vol": view["AvgVolume"].map(fmt_volume),
        "Turnover": view["AvgTurnover"].map(fmt_turnover),
        "ADR%": view["ADR"].map(lambda x: fmt_percent(x, 1)),
        "2%": view["Freq2pct"].map(fmt_frequency),
        "Range": [fmt_range(l, h) for l, h in zip(view["ExpLow"], view["ExpHigh"])],
        "Live": view["Last"].map(fmt_price),
        "Range Pos": view["RangePos"].fillna(0).clip(0, 100),
        "Scenario": view["BestScenario"].map(scenario_label),
        "Entry": view["Entry"].map(fmt_price), "Target": view["Target"].map(fmt_price),
        "Stop": view["Stop"].map(fmt_price),
        "State": view["Advisory"].map(scenario_state_label),
        "Data Quality": [d[0] for d in dq],
    })
    state_tones = [scenario_state_tone(a) for a in view["Advisory"]]
    dq_tones = [d[1] for d in dq]
    scores = list(view["Score"])

    def _style(tones):
        return lambda _c: [f"color:{_TONE_HEX.get(t, '#94a3b8')};font-weight:700" for t in tones]

    def _style_score(_col):
        return [f"color:{'#34d399' if v >= 80 else ('#fbbf24' if v >= 60 else '#94a3b8')};font-weight:750"
                for v in scores]

    styler = (disp.style
              .apply(_style(state_tones), subset=["State"], axis=0)
              .apply(_style(dq_tones), subset=["Data Quality"], axis=0)
              .apply(_style_score, subset=["Score"], axis=0))
    sel = st.dataframe(
        styler, use_container_width=True, hide_index=True,
        height=min(600, 46 + 35 * len(disp)),
        on_select="rerun", selection_mode="single-row", key=f"tbl_{key}",
        column_config={
            "Symbol": st.column_config.TextColumn(width="small", pinned=True),
            "Rank": st.column_config.NumberColumn(width="small"),
            "Score": st.column_config.NumberColumn(format="%.0f", width="small"),
            "ADR%": st.column_config.TextColumn(width="small"),
            "2%": st.column_config.TextColumn(width="small"),
            "Live": st.column_config.TextColumn(width="small"),
            "Range Pos": st.column_config.ProgressColumn(
                "Range Pos", min_value=0, max_value=100, format="%d%%", width="small"),
            "Scenario": st.column_config.TextColumn(width="medium"),
            "State": st.column_config.TextColumn(width="small", pinned=True),
            "Data Quality": st.column_config.TextColumn(width="small"),
        })
    if sel and sel.selection and sel.selection.get("rows"):
        idx = sel.selection["rows"][0]
        st.session_state["_ers_drawer_symbol"] = str(disp.iloc[idx]["Symbol"]).replace("⚠ ", "")


# --- Ready Now view (Phase 6) -----------------------------------------------


def _ready_now(ready_rows, scenarios):
    phase = _session_phase()
    st.markdown(badge_html("Decision Support Only", "blue") + "  "
                + badge_html("Paper Mode", "green") + "  "
                + badge_html("Production Disabled", "gray"), unsafe_allow_html=True)
    # Item 2: never present old signals as currently actionable outside continuous trading.
    if phase == "AUCTION":
        st.warning("مزاد الإغلاق — لا دخول جديد بعد 14:15 · Closing auction — no NEW entries "
                   "(14:15–14:25). Existing paper scenarios are evaluated per their exit rules only.")
    elif phase != "CONTINUOUS":
        st.info("السوق مغلق — آخر الفرص المرصودة (ليست قابلة للتنفيذ الآن) · Market closed — last "
                "observed opportunities from the most recent scan, NOT currently actionable.")
    if ready_rows.empty:
        msg = ("No scenario currently satisfies its rules — waiting for confirmation."
               if phase == "CONTINUOUS" else
               "No ready opportunities were observed in the last scan.")
        empty_state("No ready opportunities · لا توجد فرص جاهزة", msg, icon="○")
        return
    cards = st.columns(2)
    for i, (_, r) in enumerate(ready_rows.sort_values("Rank").iterrows()):
        with cards[i % 2]:
            _opp_card(r)


def _opp_card(r):
    tone = status_tone(r["Advisory"])
    st.markdown(
        f'<div class="egx-oppcard">'
        f'<div style="display:flex;justify-content:space-between;align-items:center">'
        f'<div style="font-weight:800;font-size:1.05rem">{r["Symbol"]}</div>'
        f'{badge_html(status_label(r["Advisory"]), tone)}</div>'
        f'<div style="color:var(--muted);font-size:.8rem;margin:.15rem 0 .4rem">'
        f'{scenario_label(r["BestScenario"])} · Score {r["Score"]:.0f}</div>'
        f'<div style="display:flex;gap:1.1rem;font-size:.9rem">'
        f'<span>Entry <b>{fmt_price(r["Entry"])}</b></span>'
        f'<span style="color:var(--green)">Target <b>{fmt_price(r["Target"])}</b></span>'
        f'<span style="color:var(--red)">Stop <b>{fmt_price(r["Stop"])}</b></span></div>'
        f'<div style="display:flex;gap:1.1rem;font-size:.78rem;color:var(--muted);margin-top:.3rem">'
        f'<span>Ask {fmt_price(r["Ask"])}</span><span>Spread {fmt_percent(r["Spread"])}</span>'
        f'<span>Room {fmt_percent(r["RemainingUpside"])}</span></div></div>',
        unsafe_allow_html=True)
    if st.button("Details · تفاصيل", key=f"rn_{r['Symbol']}"):
        st.session_state["_ers_drawer_symbol"] = r["Symbol"]
        st.rerun()


# --- stock drawer (Phase 5) -------------------------------------------------


def _maybe_open_drawer(rows, universe, scenarios):
    symbol = st.session_state.get("_ers_drawer_symbol")
    if not symbol:
        return

    @st.dialog(f"📊 {symbol}", width="large")
    def _drawer():
        _render_drawer(symbol, rows, universe, scenarios)
        if st.button("Close · إغلاق"):
            st.session_state.pop("_ers_drawer_symbol", None)
            st.rerun()
    _drawer()


def _render_drawer(symbol, rows, universe, scenarios):
    urow = universe[universe["Symbol"] == symbol]
    if urow.empty:
        st.info("No data for this symbol.")
        return
    u = urow.iloc[0]
    rrow = rows[rows["Symbol"] == symbol]
    r = rrow.iloc[0] if not rrow.empty else None

    head = st.columns([1, 1, 1])
    head[0].metric("Last", fmt_price(u.get("live_last")))
    head[1].metric("Score", f"{u.get('EXPECTED_RANGE_SCALPING_SCORE', 0):.0f}")
    head[2].markdown("**Status**")
    if r is not None:
        head[2].markdown(badge_html(status_label(r["Advisory"]), status_tone(r["Advisory"]),
                                    title=str(r["Advisory"])), unsafe_allow_html=True)

    if r is not None and r["state"] == "ready":
        st.markdown("##### Quick Decision · القرار السريع")
        q = st.columns(4)
        q[0].metric("Entry · دخول", fmt_price(r["Entry"]))
        q[1].metric("Target · هدف", fmt_price(r["Target"]))
        q[2].metric("Stop · وقف", fmt_price(r["Stop"]))
        q[3].metric("Room", fmt_percent(r["RemainingUpside"]))
        st.caption(str(r.get("Reason") or ""))

    t = st.tabs(["Liquidity · السيولة", "Volatility · التذبذب", "Expected Range · النطاق",
                 "All Scenarios · السيناريوهات", "Data · البيانات"])
    with t[0]:
        _kv_table([("Avg Volume · متوسط الفوليوم", fmt_volume(u.get("liq_avg_volume_20"))),
                   ("Median Volume", fmt_volume(u.get("liq_median_volume_20"))),
                   ("Avg Turnover · قيمة التداول", fmt_turnover(u.get("liq_avg_turnover_egp_20"))),
                   ("Volume Consistency", fmt_frequency(u.get("liq_volume_consistency"))),
                   ("Volume Trend", u.get("liq_volume_trend")),
                   ("Liquidity Status", status_label(u.get("liq_status")))])
    with t[1]:
        _kv_table([("ADR % · التذبذب اليومي", fmt_percent(u.get("vol_adr_percent_20"))),
                   ("ATR % (14)", fmt_percent(u.get("vol_atr_percent_14"))),
                   ("2% Frequency", fmt_frequency(u.get("vol_target_2pct_frequency"))),
                   ("2% Upside Freq", fmt_frequency(u.get("vol_upside_2pct_frequency"))),
                   ("Classification", u.get("vol_classification"))])
    with t[2]:
        _kv_table([("Core (p50)", fmt_range(u.get("er_base_expected_low"), u.get("er_base_expected_high"))),
                   ("Expansion (p75)", fmt_range(u.get("er_high_volatility_expected_low"),
                                                 u.get("er_high_volatility_expected_high"))),
                   ("Extreme (max)", fmt_range(u.get("er_extreme_expected_low"),
                                               u.get("er_extreme_expected_high")))])
        if r is not None and pd.notna(r.get("RangePos")):
            zone, _ = range_zone(r["RangePos"])
            range_position_bar(r["RangePos"], fmt_price(u.get("er_base_expected_low")),
                               fmt_price(u.get("er_base_expected_high")))
            st.caption(f"Position {fmt_percent(r['RangePos'])} · {zone} · remaining "
                       f"{fmt_percent(r.get('RemainingUpside'))}")
    with t[3]:
        sc = scenarios[scenarios["Symbol"] == symbol] if not scenarios.empty else pd.DataFrame()
        if sc.empty:
            st.info("No live scenarios for this symbol.")
        else:
            for _, s in sc.iterrows():
                st.markdown(
                    badge_html(status_label(s["Status"]), status_tone(s["Status"]), title=str(s["Status"]))
                    + f"  **{scenario_label(s['Scenario'])}** — "
                    + f"entry {fmt_price(s.get('EntryTrigger'))}, target {fmt_price(s.get('TakeProfit'))}, "
                    + f"stop {fmt_price(s.get('StopLoss'))}", unsafe_allow_html=True)
                if s.get("Reason"):
                    st.caption(str(s["Reason"]))
    with t[4]:
        _kv_table([("Historical Date · تاريخ البيانات", u.get("prov_latest_completed_session")),
                   ("Data Status · حالة البيانات", status_label(u.get("prov_data_status"))),
                   ("Freshness", u.get("prov_freshness_status")),
                   ("Provider", u.get("prov_historical_provider")),
                   ("Rubix overlay sessions", u.get("prov_rubix_overlay_sessions"))])
        if u.get("prov_fallback_reason"):
            st.caption(str(u.get("prov_fallback_reason")))


def _kv_table(pairs):
    st.dataframe(pd.DataFrame([{"Metric": k, "Value": dash(v)} for k, v in pairs]),
                 use_container_width=True, hide_index=True)


def dash(v):
    if v is None or (isinstance(v, float) and pd.isna(v)) or v == "":
        return EM_DASH
    return v


# --- paper evidence (compact) -----------------------------------------------


def _paper_status(cfg):
    try:
        per_scenario, overall = _load_evidence()
    except Exception:
        st.caption("Paper evidence unavailable.")
        return
    c = st.columns(5)
    c[0].metric("Complete Sessions", overall["complete_forward_sessions"])
    c[1].metric("Pilot / Partial", f"{overall['pilot_sessions']} / {overall['partial_sessions']}")
    c[2].metric("Total READY", overall["total_signals_all_sessions"])
    c[3].metric("Matured Exec", overall["matured_executable_signals"])
    c[4].metric("Pending", overall["pending_outcomes"])
    st.caption(f"Evidence status: **{overall['evidence_status']}** · production not recommended by this tool.")
    if not per_scenario.empty:
        cols = ["Scenario", "SignalCount", "TargetFirst", "StopFirst", "NeitherHit",
                "ExpectancyAfterCosts%", "ProfitFactor"]
        st.dataframe(per_scenario[[c for c in cols if c in per_scenario.columns]],
                     use_container_width=True, hide_index=True)


# --- row builder (pure, presentation prep) ----------------------------------


def _build_rows(universe, scenarios):
    """One display row per symbol: raw values + a best-scenario summary + state."""
    best = _best_per_symbol(scenarios)
    u = universe.copy()
    df = pd.DataFrame({
        "Symbol": u["Symbol"], "Rank": u.get("Rank"),
        "Score": pd.to_numeric(u.get("EXPECTED_RANGE_SCALPING_SCORE"), errors="coerce").fillna(0),
        "AvgVolume": pd.to_numeric(u.get("liq_avg_volume_20"), errors="coerce"),
        "AvgTurnover": pd.to_numeric(u.get("liq_avg_turnover_egp_20"), errors="coerce"),
        "ADR": pd.to_numeric(u.get("vol_adr_percent_20"), errors="coerce"),
        "Freq2pct": pd.to_numeric(u.get("vol_target_2pct_frequency"), errors="coerce"),
        "ExpLow": pd.to_numeric(u.get("er_base_expected_low"), errors="coerce"),
        "ExpHigh": pd.to_numeric(u.get("er_base_expected_high"), errors="coerce"),
        "Last": pd.to_numeric(u.get("live_last"), errors="coerce"),
        "Spread": pd.to_numeric(u.get("live_spread_percent"), errors="coerce"),
        "Tradable": u.get("TradableCandidate").fillna(False) if "TradableCandidate" in u else False,
        "DataStatus": u.get("prov_data_status"),
        # genuine PER-SYMBOL history staleness (distinct from market-wide stale live quotes)
        "HistStale": (u.get("prov_data_status") == "DATA_STALE") if "prov_data_status" in u else False,
    })
    df = df.merge(best, on="Symbol", how="left")
    df["state"] = df.apply(_row_state, axis=1)
    return df


def _best_per_symbol(scenarios):
    cols = ["Symbol", "Scenario", "Advisory", "EntryTrigger", "TakeProfit", "StopLoss",
            "RangePosition%", "RemainingUpside%", "Ask", "Spread%", "Reason", "Status"]
    if scenarios is None or scenarios.empty:
        return pd.DataFrame(columns=["Symbol", "BestScenario", "Advisory", "Entry", "Target",
                                     "Stop", "RangePos", "RemainingUpside", "Ask", "Reason"])
    s = scenarios[[c for c in cols if c in scenarios.columns]].copy()
    s["_rank"] = s["Status"].map(lambda x: 0 if x in READY_STATES else (1 if x in WAIT_STATES else 2))
    s = s.sort_values(["Symbol", "_rank"]).drop_duplicates("Symbol", keep="first")
    # Spread stays on the universe/live row (df) to avoid a merge-name clash.
    return s.rename(columns={"Scenario": "BestScenario", "EntryTrigger": "Entry",
                             "TakeProfit": "Target", "StopLoss": "Stop",
                             "RangePosition%": "RangePos", "RemainingUpside%": "RemainingUpside"})[
        ["Symbol", "BestScenario", "Advisory", "Entry", "Target", "Stop", "RangePos",
         "RemainingUpside", "Ask", "Reason"]]


_READY_ADVISORY = {"READY_LOWER_RANGE_BOUNCE", "READY_DIP_RECLAIM", "READY_CONTINUATION",
                   "READY_BREAKOUT_RETEST", "READY_GAP_RECOVERY"}
_WAIT_ADVISORY = {"WAIT_LOWER_RANGE_BOUNCE", "WATCH_HIGH_VOLUME_VOLATILITY"}


def _row_state(r):
    """A clean 4-way partition of the 265 symbols (each symbol in exactly one).

    'rejected' == not tradable (failed the liquidity gate) — so Tradable + Rejected
    equals the universe. 'ready' / 'waiting' / 'no_trade' are the LIVE dispositions
    WITHIN the tradable set, so they never inflate the partition.
    """
    if not bool(r.get("Tradable")):
        return "rejected"                     # low-liquidity: not a candidate at all
    adv = r.get("Advisory")
    if adv in _READY_ADVISORY:
        return "ready"
    if adv in _WAIT_ADVISORY:
        return "waiting"
    return "no_trade"                         # tradable but no room / stale live / consumed
