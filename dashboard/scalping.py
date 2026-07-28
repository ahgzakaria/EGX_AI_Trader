"""Streamlit pages for the isolated paper-only scalping module."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from config.settings_manager import settings
from core.egx_session import REGULAR_OPEN, cairo_now, egx_session_phase
from dashboard.ui import (
    badge_html,
    egx_holiday_banner,
    empty_state,
    metric_card,
    page_header,
    section_header,
    status_bar,
)
from scalping.backtest_engine import ScalpingBacktest
from scalping.config import ScalpingConfig
from scalping.data_sources import (
    RubixScalpingDataSource,
    ScalpingDataSourceError,
    historical_archive_files,
    load_csv_frames,
)
from scalping.database import ScalpingDatabase
from scalping.models import Opportunity, SetupType
from scalping.paper_portfolio import ScalpingPaperPortfolio
from scalping.scanner import ScalpingScanner, intraday_evidence_description
from scalping_expected_range.frozen_watchlist import (
    CONFIG_VERSION_MISMATCH,
    GENERATION_FAILED,
    INSUFFICIENT_DAILY_HISTORY,
    NO_ELIGIBLE_SYMBOLS,
    PROVENANCE_REJECTED,
    SOURCE_FINGERPRINT_CHANGED,
    SOURCE_UNAVAILABLE,
    WATCHLIST_NOT_GENERATED,
    WATCHLIST_READY,
    FrozenHistoricalWatchlistService,
)
from scalping_expected_range.live_readiness import (
    CLOSING_AUCTION_NO_NEW_ENTRY,
    ENTRY_READY_RESEARCH_ONLY,
    LIVE_DATA_STALE,
    LIVE_DATA_UNAVAILABLE,
    MOVE_EXTENDED_DO_NOT_CHASE,
    SESSION_CLOSED,
    WATCHLIST_NOT_READY as LIVE_WATCHLIST_NOT_READY,
    LiveEntryReadinessEngine,
    LiveReadinessConfig,
    RubixLiveBatchReader,
)


def _context():
    config = ScalpingConfig.from_mapping(settings.get("scalping"))
    return config, ScalpingDatabase(config.database_path)


def _historical_watchlist_service():
    return FrozenHistoricalWatchlistService()


def _historical_watchlist_frame(record, *, displayed_only):
    members = record.displayed if displayed_only else record.members
    rows = []
    for member in members:
        rows.append(
            {
                "rank": member["historical_rank"],
                "symbol": member["symbol"],
                "historical_score": member["historical_score"],
                "movement_potential": member["movement_potential_score"],
                "range_stability": member["range_stability_score"],
                "zone_consistency": member["combined_zone_consistency_score"],
                "zone_confidence": member["zone_confidence_label"],
                "liquidity_score": member["liquidity_score"],
                "median_daily_range": member["median_daily_range"],
                "normal_range_band": (
                    f"{member['normal_range_lower']:.2f}%–"
                    f"{member['normal_range_upper']:.2f}%"
                ),
                "range_hit_2pct_frequency": member["range_hit_2pct_frequency"],
                "typical_lower_excursion": member["median_lower_excursion"],
                "typical_upper_excursion": member["median_upper_excursion"],
                "primary_60_state": member["primary_readiness_status"],
                "recent_30_state": member["recent_confirmation_status"],
                "historical_explanation": member["historical_explanation"],
            }
        )
    return pd.DataFrame(rows)


def _historical_watchlist_panel(service=None):
    """Render READY history only; this function never rebuilds on presentation."""

    service = service or _historical_watchlist_service()
    target, proposed_cutoff = service.target_and_cutoff()
    section_header(
        "HISTORICAL SCALPING WATCHLIST",
        "قائمة السكالبنج التاريخية الثابتة · completed EODHD Daily history only",
    )
    controls = st.columns((1, 3))
    load_clicked = controls[0].button(
        "Load frozen watchlist",
        key="load_frozen_historical_watchlist",
        width="stretch",
    )
    controls[1].caption(
        f"Target session {target.isoformat()} · proposed historical cutoff "
        f"{proposed_cutoff.isoformat()} · loading is read-only"
    )
    result = service.get_for_session(target)
    if load_clicked:
        result = service.get_for_session(target)

    with st.expander("Research Rebuild · إعادة بناء بحثية صريحة"):
        st.warning(
            "Research-only. This creates a new immutable identity only when "
            "the cutoff, source fingerprint, selector version, universe, or "
            "Top-N changes. It never overwrites a READY watchlist."
        )
        st.write(
            f"Proposed target: **{target.isoformat()}**  \n"
            f"Maximum historical cutoff: **{proposed_cutoff.isoformat()}**"
        )
        confirmed = st.checkbox(
            "I confirm an authorized research rebuild",
            key=f"historical_rebuild_confirm_{target.isoformat()}",
        )
        if st.button(
            "Run Research Rebuild",
            disabled=not confirmed,
            key=f"historical_rebuild_{target.isoformat()}",
        ):
            with st.spinner(
                "Loading EODHD Daily history and publishing an immutable watchlist..."
            ):
                result = service.rebuild_for_research(
                    target,
                    authorized=True,
                )

    if result.status != WATCHLIST_READY or result.record is None:
        messages = {
            WATCHLIST_NOT_GENERATED: (
                "No frozen historical watchlist has been generated for this "
                "session. Presentation does not start generation."
            ),
            INSUFFICIENT_DAILY_HISTORY: (
                "The historical source does not contain enough completed daily "
                "history to publish a watchlist."
            ),
            SOURCE_UNAVAILABLE: "EODHD Daily history is unavailable.",
            PROVENANCE_REJECTED: (
                "Historical source provenance was rejected. Yahoo, unknown and "
                "mixed-provider histories are not accepted."
            ),
            CONFIG_VERSION_MISMATCH: (
                "The stored selector version is incompatible with the current "
                "approved configuration."
            ),
            SOURCE_FINGERPRINT_CHANGED: (
                "The source fingerprint changed; an explicit research rebuild "
                "is required."
            ),
            GENERATION_FAILED: (
                "Historical watchlist generation failed closed. No partial "
                "READY list is available."
            ),
            NO_ELIGIBLE_SYMBOLS: (
                "No symbol passed the hard historical data and safety gates."
            ),
        }
        st.info(messages.get(result.status, result.detail or result.status))
        if result.detail:
            st.caption(result.detail)
        st.caption(
            "No fallback to legacy ERS, Range Scanner, Yahoo, Rubix movers, "
            "or current-session percentage change is permitted."
        )
        return result

    record = result.record
    header = record.header
    st.session_state["_historical_scalping_watchlist_id"] = header["watchlist_id"]
    cards = st.columns(4)
    cards[0].metric("Target session", header["target_session_date"])
    cards[1].metric("Historical cutoff", header["historical_data_cutoff"])
    cards[2].metric("Eligible universe", header["eligible_count"])
    cards[3].metric("Displayed candidates", header["displayed_count"])
    st.caption(
        f"ID {header['watchlist_id']} · {header['provider']} · "
        f"{header['metric_version']} / {header['config_version']} · "
        f"60-session primary / 30-session confirmation · Top {header['top_n']} · "
        f"generated {header['generated_at']} · "
        f"{header['source_fingerprint_status']} · FROZEN / IMMUTABLE"
    )
    st.success(
        "This list is based only on completed EODHD Daily history and is "
        "frozen for the session."
    )
    st.caption(
        "Daily candles may include official closing-auction effects. "
        "Intraday historical enrichment is currently unavailable/not ready."
    )

    top = _historical_watchlist_frame(record, displayed_only=True)
    st.dataframe(top, use_container_width=True, hide_index=True)

    with st.expander(
        f"Complete hard-eligible universe ({header['eligible_count']})"
    ):
        sort_columns = {
            "Historical rank": "rank",
            "Historical score": "historical_score",
            "Movement potential": "movement_potential",
            "Range Stability": "range_stability",
            "Zone Consistency": "zone_consistency",
            "Liquidity": "liquidity_score",
            "Median daily range": "median_daily_range",
            "2% hit frequency": "range_hit_2pct_frequency",
        }
        sort_controls = st.columns((2, 1))
        sort_label = sort_controls[0].selectbox(
            "Sort historical universe",
            tuple(sort_columns),
            key=f"historical_sort_{header['watchlist_id']}",
        )
        descending = sort_controls[1].toggle(
            "Descending",
            value=sort_columns[sort_label] != "rank",
            key=f"historical_sort_desc_{header['watchlist_id']}",
        )
        complete = _historical_watchlist_frame(
            record, displayed_only=False
        ).sort_values(
            sort_columns[sort_label],
            ascending=not descending,
            kind="mergesort",
        )
        st.dataframe(complete, use_container_width=True, hide_index=True)

    previous = service.repository.ready_before(header["target_session_date"])
    if previous is not None:
        comparison = service.compare_watchlists(
            previous.header["watchlist_id"], header["watchlist_id"]
        )
        with st.expander("Stored watchlist comparison · مقارنة القوائم"):
            comparison_cards = st.columns(4)
            comparison_cards[0].metric(
                "Previous target", comparison.previous_target_session
            )
            comparison_cards[1].metric(
                "Candidate overlap", len(comparison.candidate_overlap)
            )
            comparison_cards[2].metric(
                "Additions / removals",
                f"{len(comparison.additions)} / {len(comparison.removals)}",
            )
            comparison_cards[3].metric(
                "Top-N turnover", f"{comparison.top_n_turnover:.1%}"
            )
            st.caption(
                f"Cutoff {comparison.previous_data_cutoff} → "
                f"{comparison.current_data_cutoff} · eligible count change "
                f"{comparison.eligible_count_change:+d} · metric version changed "
                f"{comparison.metric_version_changed} · config version changed "
                f"{comparison.config_version_changed}"
            )
            if comparison.additions:
                st.write("Additions:", ", ".join(comparison.additions))
            if comparison.removals:
                st.write("Removals:", ", ".join(comparison.removals))
            if comparison.rank_changes:
                st.dataframe(
                    pd.DataFrame(comparison.rank_changes),
                    use_container_width=True,
                    hide_index=True,
                )
            st.caption(
                "Additions, removals and rank changes are historical list "
                "comparisons, not trade signals."
            )
    return result


def _live_readiness_engine():
    market_data = settings.get("market_data") or {}
    db_path = (
        os.getenv("RUBIX_DB_PATH")
        or market_data.get("rubix_db_path")
        or "data/rubix_live_market.db"
    )
    config = LiveReadinessConfig()
    return LiveEntryReadinessEngine(
        RubixLiveBatchReader(
            db_path,
            busy_timeout_ms=config.read_busy_timeout_ms,
        ),
        config=config,
    )


def _live_readiness_frame(batch):
    rows = []
    for item in batch.results:
        rows.append(
            {
                "Historical Rank (Frozen)": item.historical_rank,
                "Symbol": item.symbol,
                "Live Readiness": item.readiness_score,
                "Live State": item.live_state,
                "Current Price": item.current_price,
                "Change From Open %": item.change_from_open_percent,
                "Continuous Range %": item.continuous_range_percent,
                "Historical Range Consumption": (
                    item.historical_range_consumption
                ),
                "Indicative Remaining Movement": (
                    item.indicative_remaining_movement
                ),
                "Opening Range": item.opening_range_state,
                "VWAP State": item.vwap_state,
                "Distance From VWAP %": item.distance_from_vwap_percent,
                "Spread %": item.current_spread_percent,
                "Quote Age Seconds": item.quote_age_seconds,
                "Cumulative Volume": item.cumulative_volume,
                "Cumulative Traded Value": item.cumulative_traded_value,
                "Trade Count": item.trade_count,
                "Entry Zone": (
                    f"{item.entry_zone_low:.4f}–{item.entry_zone_high:.4f}"
                    if item.entry_zone_low is not None
                    and item.entry_zone_high is not None
                    else None
                ),
                "Target": item.target_price,
                "Stop": item.stop_price,
                "Invalidation": item.invalidation_condition,
                "No-Chase Warning": item.no_chase_reason,
                "Data Quality": item.data_quality_status,
                "Evaluated At": item.evaluated_at,
            }
        )
    return pd.DataFrame(rows)


@st.fragment(run_every=LiveReadinessConfig().refresh_seconds)
def _live_entry_monitor_fragment(record, engine=None, evaluated_at=None):
    """Refresh only mutable Rubix readiness; never rebuild frozen history."""

    engine = engine or _live_readiness_engine()
    batch = engine.evaluate(record, evaluated_at=evaluated_at)
    if batch.status == LIVE_WATCHLIST_NOT_READY:
        st.info(
            "WATCHLIST_NOT_READY — generate the immutable historical "
            "watchlist through the explicit Research Rebuild control above."
        )
        return batch

    states = {}
    for item in batch.results:
        states[item.live_state] = states.get(item.live_state, 0) + 1
    cards = st.columns(4)
    cards[0].metric(
        "Entry Ready · Research Only",
        states.get(ENTRY_READY_RESEARCH_ONLY, 0),
    )
    cards[1].metric(
        "Extended · Do Not Chase",
        states.get(MOVE_EXTENDED_DO_NOT_CHASE, 0),
    )
    cards[2].metric(
        "Stale / Unavailable",
        states.get(LIVE_DATA_STALE, 0)
        + states.get(LIVE_DATA_UNAVAILABLE, 0),
    )
    cards[3].metric(
        "Auction / Closed",
        states.get(CLOSING_AUCTION_NO_NEW_ENTRY, 0)
        + states.get(SESSION_CLOSED, 0),
    )
    st.caption(
        f"Watchlist {batch.watchlist_id} · Rubix cutoff "
        f"{batch.rubix_data_cutoff or 'unavailable'} · "
        f"{batch.connection_count} read-only connection · "
        f"{batch.query_count} queries · query {batch.query_latency_ms:.3f} ms · "
        f"evaluation {batch.evaluation_latency_ms:.3f} ms"
    )
    if batch.detail:
        st.caption(batch.detail)

    frame = _live_readiness_frame(batch)
    if frame.empty:
        st.info("No displayed frozen candidate is available for monitoring.")
        return batch
    sort_columns = {
        "Readiness": "Live Readiness",
        "Live state": "Live State",
        "Quote freshness": "Quote Age Seconds",
        "Historical rank": "Historical Rank (Frozen)",
    }
    controls = st.columns((2, 1))
    sort_label = controls[0].selectbox(
        "Sort live monitor",
        tuple(sort_columns),
        key=f"live_readiness_sort_{batch.watchlist_id}",
    )
    descending = controls[1].toggle(
        "Descending",
        value=sort_label == "Readiness",
        key=f"live_readiness_desc_{batch.watchlist_id}",
    )
    ordered = frame.sort_values(
        sort_columns[sort_label],
        ascending=not descending,
        kind="mergesort",
        na_position="last",
    )
    st.dataframe(ordered, use_container_width=True, hide_index=True)

    with st.expander("Deterministic assessment details · تفاصيل التقييم"):
        selected = st.selectbox(
            "Frozen candidate",
            [item.symbol for item in batch.results],
            key=f"live_readiness_detail_{batch.watchlist_id}",
        )
        item = next(
            result for result in batch.results if result.symbol == selected
        )
        st.markdown(
            "\n".join(
                [
                    "Historical selection:",
                    f"- frozen rank {item.historical_rank}",
                    f"- historical score {item.historical_score:.2f}",
                    (
                        f"- median full-session range "
                        f"{item.median_daily_range:.2f}%"
                    ),
                    (
                        f"- Range Stability {item.range_stability:.2f}; "
                        f"Zone Consistency {item.zone_consistency:.2f}"
                    ),
                    "",
                    "Live assessment:",
                    *[f"- {reason}" for reason in item.explanations],
                    f"- state: {item.live_state}",
                ]
            )
        )
        st.caption(
            "Deterministic rules only. No AI prose, broker route, order, "
            "paper trade, portfolio mutation or short-selling signal."
        )
    return batch


def _live_entry_monitor_panel(historical_result, engine=None, evaluated_at=None):
    section_header(
        "LIVE ENTRY MONITOR",
        "مراقبة جاهزية الدخول اللحظية · frozen candidates only",
    )
    st.caption(
        "Historical universe: 225 validated symbols · Endpoint coverage: "
        "241 / 265 · Watchlist ranking population: 225 validated symbols · "
        "Remaining endpoint-unavailable/inactive/non-equity: 24"
    )
    st.info(
        "Historical Rank: FROZEN · Live Readiness: CURRENT SESSION · "
        "Production: DISABLED"
    )
    if (
        historical_result is None
        or historical_result.status != WATCHLIST_READY
        or historical_result.record is None
    ):
        st.warning(
            "WATCHLIST_NOT_READY — the live monitor will not scan the full "
            "market, legacy ERS, Range Scanner, current movers or a temporary "
            "candidate list. Use the explicit historical preparation workflow "
            "above."
        )
        return None
    return _live_entry_monitor_fragment(
        historical_result.record,
        engine=engine,
        evaluated_at=evaluated_at,
    )


def _today_summary(database, config):
    today = cairo_now().date().isoformat()
    realized = database.row(
        "SELECT COALESCE(SUM(realized_pnl),0) value FROM exits WHERE substr(exited_at,1,10)=?",
        (today,),
    )["value"]
    active = database.open_positions()
    unrealized = sum(
        ((row.get("current_bid") or row["entry_fill"]) - row["entry_fill"]) * row["quantity"]
        for row in active
    )
    risk = database.row("SELECT * FROM daily_risk_state WHERE session_date=?", (today,)) or {}
    signals = database.row(
        """SELECT COUNT(*) total,
                  COALESCE(SUM(actionable),0) actionable,
                  COALESCE(SUM(CASE WHEN actionable=0 THEN 1 ELSE 0 END),0) blocked
           FROM signals WHERE session_date=?""", (today,),
    )
    exits = database.row(
        """SELECT COUNT(*) total,
                  COALESCE(SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END),0) wins,
                  COALESCE(SUM(CASE WHEN realized_pnl<0 THEN 1 ELSE 0 END),0) losses
           FROM exits WHERE substr(exited_at,1,10)=?""", (today,),
    )
    loss_limit = config.initial_capital * config.max_daily_loss_percent / 100.0
    return {
        "realized": float(realized or 0), "unrealized": float(unrealized),
        "active": len(active), "trades": int(exits["total"] or 0),
        "wins": int(exits["wins"] or 0), "losses": int(exits["losses"] or 0),
        "actionable": int(signals["actionable"] or 0),
        "blocked": int(signals["blocked"] or 0),
        "daily_loss_remaining": max(0.0, loss_limit + float(realized or 0)),
        "risk": risk,
    }


def show_scalping_dashboard():
    """Operational control-tower for the active Expected Range Scalper system.

    Presentation only — reuses the cached pre-session scan, the shared components and
    the reusable stock drawer. Never changes a strategy, score, threshold, scenario,
    range, TP/SL, flag, provider, or database.
    """
    from dashboard.expected_range_scalper import (
        _build_rows, _maybe_open_drawer, _run_scan, _session_phase,
    )
    from scalping_expected_range.config import ExpectedRangeConfig

    cfg = ExpectedRangeConfig.load()
    page_header("Scalping Dashboard",
                "لوحة متابعة المضاربة اللحظية والاختبار الورقي · Live monitoring · Decision support only",
                icon="⚡", badge="SCALPING V3")

    egx_holiday_banner()
    historical_result = _historical_watchlist_panel()
    _live_entry_monitor_panel(historical_result)
    _dash_status_bar(cfg, _session_phase())
    _dash_health_panel(cfg)
    _dash_paper_panel(cfg)

    if st.button("▶ Load / refresh scan · تحميل الفحص", type="primary"):
        st.session_state["_ers_scan"] = True
    if "_ers_scan" not in st.session_state:
        empty_state("Load the scan · حمّل الفحص",
                    "Run the pre-session scan to load live opportunities, watchlist, "
                    "top candidates and the blockers summary.", icon="▶")
        return
    try:
        result = _run_scan(True)
    except Exception as error:
        empty_state("Scan unavailable", str(error), icon="⚠")
        return

    rows = _build_rows(result["universe"], result["scenarios"])
    phase = _session_phase()
    _dash_primary_cards(rows, phase)
    _dash_secondary_metrics(rows, result["summary"], cfg)
    _dash_ready_panel(rows, phase)
    _dash_watchlist_panel(rows, phase)
    _dash_top_candidates(rows, phase)
    _dash_blockers(rows, phase)
    _maybe_open_drawer(rows, result["universe"], result["scenarios"])
    st.caption(f"Generated {result['generated_at']} · decision-support only · production disabled")


# --- Scalping Dashboard components (operational overview, presentation only) ---

def _rubix_path():
    return Path(settings.get("market_data").get("rubix_db_path", "data/rubix_live_market.db"))


def _rubix_latest_event():
    p = _rubix_path()
    if not p.is_file():
        return None
    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{p.resolve().as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            row = conn.execute("SELECT MAX(received_at) FROM quotes").fetchone()
        finally:
            conn.close()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def _bridge_latest_final():
    try:
        from scalping_expected_range.config import ExpectedRangeConfig
        from core.daily_bridge.normalized_cache import NormalizedDailyCache
        return NormalizedDailyCache(ExpectedRangeConfig.load().normalized_daily_cache_path
                                    ).latest_final_session("RUBIX_DERIVED")
    except Exception:
        return None


def _dash_status_bar(cfg, phase):
    ptxt = {"CONTINUOUS": ("EGX OPEN", "green"), "AUCTION": ("AUCTION", "amber"),
            "PRE_OPEN": ("PRE-OPEN", "blue"), "CLOSED": ("EGX CLOSED", "gray"),
            "HOLIDAY": ("EGX HOLIDAY", "blue")}.get(phase, (phase, "gray"))
    last_event = _rubix_latest_event()
    if phase == "CONTINUOUS" and last_event:
        vp = ("Progressing", "green")
    elif phase == "HOLIDAY":
        vp = ("Idle (holiday)", "gray")
    else:
        vp = ("Idle (closed)", "gray")
    status_bar([
        ("", ptxt[0], ptxt[1]),
        ("Rubix", "Connected" if _rubix_path().is_file() else "Down",
         "green" if _rubix_path().is_file() else "red"),
        ("Value", vp[0], vp[1]),
        ("Bridge", _bridge_latest_final() or "—", "green" if _bridge_latest_final() else "amber"),
        ("Paper", "Active" if cfg.paper_enabled else "Off", "green" if cfg.paper_enabled else "gray"),
        ("Production", "Disabled", "gray"),
        ("Last event", _timestamp(last_event) if last_event else "—", "gray"),
    ])


def _current_history_health():
    """CURRENT when the latest completed session is available locally (Rubix overlay/
    Daily Bridge), regardless of any old pilot snapshot's status."""
    try:
        import pandas as _pd
        from core.egx_session import expected_latest_completed_session
        bridge = _bridge_latest_final()
        expected = expected_latest_completed_session()
        if bridge and expected and _pd.to_datetime(bridge).date() >= expected:
            return ("CURRENT", "green", bridge, expected)
        return ("BEHIND", "amber", bridge, expected)
    except Exception:
        return ("UNKNOWN", "gray", None, None)


def _dash_health_panel(cfg):
    section_header("Data & System Health · صحة البيانات والنظام", "operational status")
    from scalping_expected_range.orchestration import read_manifest
    from scalping_expected_range.paper_state import PaperStateStore
    root = Path(cfg.paper_output_root)
    last_session = None
    if root.is_dir():
        dates = sorted([p.name for p in root.iterdir() if p.is_dir() and p.name[:4].isdigit()])
        last_session = dates[-1] if dates else None
    manifest = read_manifest(root / last_session / "pre_session_manifest.json") if last_session else None
    try:
        counters = PaperStateStore().counters(last_session) if last_session else {}
    except Exception:
        counters = {}
    daily = _read_csv("reports/expected_range_daily_paper_summary.csv")
    snap_class = None
    if last_session and not daily.empty and "Classification" in daily:
        match = daily[daily["SessionDate"].astype(str) == str(last_session)]
        snap_class = match.iloc[-1]["Classification"] if not match.empty else daily.iloc[-1]["Classification"]

    hist_state, hist_tone, bridge, expected = _current_history_health()
    # CURRENT history health is derived live (bridge/overlay) — NOT the old snapshot status.
    items = [
        ("Rubix connection", "OK" if _rubix_path().is_file() else "Down",
         "green" if _rubix_path().is_file() else "red"),
        ("Last event", _timestamp(_rubix_latest_event()) or "—", "gray"),
        ("Current history health", hist_state, hist_tone),
        ("Daily Bridge (latest FINAL)", _bridge_latest_final() or "—",
         "green" if _bridge_latest_final() else "amber"),
        ("Live monitor (last session)", f"{counters.get('evaluations_processed', 0)} evals"
         if counters else "inactive", "green" if counters.get("evaluations_processed") else "gray"),
        ("Scheduled tasks", "5 registered", "gray"),
    ]
    cols = st.columns(len(items))
    for col, (label, value, tone) in zip(cols, items):
        with col:
            st.markdown(badge_html(value, tone, title=label), unsafe_allow_html=True)
            st.caption(label)

    # Compact historical snapshot line; full diagnostics live in System Details.
    from datetime import datetime as _dt
    snap_status = (manifest or {}).get("snapshot_status", "—")
    snap_date = (manifest or {}).get("session_date", last_session or "—")
    snap_time = (manifest or {}).get("snapshot_created_at", "")
    try:
        short_date = _dt.fromisoformat(str(snap_date)).strftime("%d %b").lstrip("0")
    except Exception:
        short_date = str(snap_date)
    st.caption(f"Latest snapshot: **{short_date} · {snap_class or '—'}** · Historical status: "
               f"{snap_status} *(not current — history health is {hist_state})*")
    with st.expander("System Details · تفاصيل النظام"):
        st.markdown(
            f"- **Current history health:** {hist_state} "
            f"(Daily Bridge latest FINAL {bridge or '—'} vs expected completed {expected or '—'}).\n"
            f"- **Latest pre-session snapshot:** session {snap_date} · status {snap_status} · "
            f"class {snap_class or '—'} · created {snap_time[:19] if snap_time else '—'} (historical).\n"
            f"- **Live monitor (last session):** {counters.get('evaluations_processed', 0)} evaluations, "
            f"{counters.get('transitions_recorded', 0)} transitions.\n"
            f"- **Scheduled tasks:** Pre-session (09:45), Live Monitor (09:55), Outcome Finalizer (14:40), "
            f"Rubix Daily Finalizer (14:35), Session Validator — Sun–Thu.\n"
            "- **Feed-health signal:** a frozen Mubasher `market_timestamp` is *not* a fault while "
            "Last/Bid/Ask/Volume keep progressing — market-wide value-progression is the health signal, "
            "not the exchange timestamp. See the System Health page for collector/process diagnostics.")


def _dash_paper_panel(cfg):
    from scalping_expected_range.paper_evidence import scenario_evidence
    try:
        ev = scenario_evidence(cfg=cfg)
    except Exception:
        return
    o = ev["overall"]
    section_header("Paper Forward Test · الاختبار الورقي", "records only · production not recommended")
    c = st.columns(6)
    c[0].metric("Complete Sessions", o["complete_forward_sessions"])
    c[1].metric("Pilot / Partial", f"{o['pilot_sessions']} / {o['partial_sessions']}")
    c[2].metric("READY Signals", o["total_signals_all_sessions"])
    c[3].metric("Matured Exec", o["matured_executable_signals"])
    c[4].metric("Pending", o["pending_outcomes"])
    per = ev["per_scenario"]
    tf = int(per["TargetFirst"].sum()) if not per.empty else 0
    sf = int(per["StopFirst"].sum()) if not per.empty else 0
    c[5].metric("Target / Stop first", f"{tf} / {sf}")
    st.markdown(badge_html(f"Evidence: {o['evidence_status']}", "amber")
                + "  " + badge_html("Production not approved", "gray"), unsafe_allow_html=True)


def _dash_primary_cards(rows, phase):
    total = len(rows)
    tradable = int(rows["Tradable"].sum())
    rejected = total - tradable
    ready = int((rows["state"] == "ready").sum())
    waiting = int((rows["state"] == "waiting").sum())
    from dashboard.expected_range_scalper import _ready_label
    r_ar, r_en, r_sub, r_tone = _ready_label(phase)
    c = st.columns(4)
    with c[0]:
        metric_card(r_ar, ready, r_en, r_sub, r_tone,
                    tag="symbols · live" if phase == "CONTINUOUS" else "symbols · last scan")
    with c[1]:
        metric_card("انتظار", waiting, "Watch / Waiting", "closest to confirmation", "amber",
                    tag="symbols · live" if phase == "CONTINUOUS" else "symbols · last scan")
    with c[2]:
        metric_card("قابل للتداول", tradable, "Tradable Universe",
                    f"of {total} · passed liquidity gate", "blue", tag="symbols · historical")
    with c[3]:
        metric_card("محظور/مرفوض", rejected, "Blocked / Rejected", "low liquidity — not a candidate",
                    "red", tag="symbols · historical")


def _dash_secondary_metrics(rows, summary, cfg):
    from scalping_expected_range.paper_evidence import scenario_evidence
    spread_median = pd.to_numeric(rows.get("Spread"), errors="coerce").dropna().median()
    daily = _read_csv("reports/expected_range_daily_paper_summary.csv")
    signals_today = int(daily.iloc[-1]["ReadySignals"]) if (not daily.empty and "ReadySignals" in daily) else 0
    dq_fail = int(daily.iloc[-1]["DataQualityFailures"]) if (not daily.empty and "DataQualityFailures" in daily) else 0
    try:
        ev = scenario_evidence(cfg=cfg)["overall"]
        pending = ev["pending_outcomes"]; complete = ev["complete_forward_sessions"]
    except Exception:
        pending = complete = 0
    s = st.columns(4)
    s[0].caption(f"High-volume candidates: **{summary.get('high_volume_candidates', 0)}**")
    s[1].caption(f"Expected-2% candidates: **{summary.get('expected_2pct_candidates', 0)}**")
    s[2].caption(f"Median spread: **{('%.2f%%' % spread_median) if pd.notna(spread_median) else '—'}**")
    s[3].caption(f"Range consumed: **{summary.get('range_consumed', 0)}**")
    s2 = st.columns(4)
    s2[0].caption(f"Paper signals today: **{signals_today}**")
    s2[1].caption(f"Pending outcomes: **{pending}**")
    s2[2].caption(f"Data-quality failures: **{dq_fail}**")
    s2[3].caption(f"Complete forward sessions: **{complete}**")


def _dash_ready_panel(rows, phase):
    from dashboard.formatting import fmt_live_price, fmt_percent, fmt_price, fmt_score, scenario_label
    heading = ("جاهز الآن · Ready Opportunities" if phase == "CONTINUOUS"
               else "آخر فرص مسجلة · Last Observed Opportunities")
    section_header(heading, "scenario-specific · decision support only")
    st.markdown(badge_html("Decision Support Only", "blue") + "  " + badge_html("Paper Mode", "green")
                + "  " + badge_html("Production Disabled", "gray"), unsafe_allow_html=True)
    ready = rows[rows["state"] == "ready"].sort_values("Rank")
    if ready.empty:
        # Compact empty state (~90px), session-aware, top reasons — no spinner.
        b = _dash_classify(rows, phase)
        top = sorted(((lbl, b[k]) for k, lbl, _ in _BLOCKER_LABELS if b[k] > 0),
                     key=lambda x: -x[1])[:3]
        reason_txt = " · ".join(f"{lbl} ({v})" for lbl, v in top) or "no data"
        subtitle = ("no scenario currently satisfies its rules"
                    if phase == "CONTINUOUS" else "none in the last completed scan")
        label = "No ready reasons" if phase == "CONTINUOUS" else "Last-scan classification"
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:1rem;background:var(--surface);'
            f'border:1px solid var(--border);border-left:3px solid #94a3b8;border-radius:11px;'
            f'padding:.7rem .95rem"><div style="font-size:1.2rem">○</div>'
            f'<div><div style="font-weight:750">No ready opportunities · لا توجد فرص جاهزة</div>'
            f'<div style="color:var(--muted);font-size:.8rem">{subtitle} — {label}: {reason_txt}. '
            f'See the Watchlist below.</div></div></div>', unsafe_allow_html=True)
        return
    cols = ["Symbol", "Scenario", "Last", "Ask", "Entry", "Target", "Stop", "Spread%",
            "Score", "Range Pos", "Data Quality"]
    disp = pd.DataFrame({
        "Symbol": ready["Symbol"], "Scenario": ready["BestScenario"].map(scenario_label),
        "Last": ready["Last"].map(fmt_live_price), "Ask": ready["Ask"].map(fmt_live_price),
        "Entry": ready["Entry"].map(fmt_price), "Target": ready["Target"].map(fmt_price),
        "Stop": ready["Stop"].map(fmt_price), "Spread%": ready["Spread"].map(lambda x: fmt_percent(x)),
        "Score": ready["Score"].map(fmt_score), "Range Pos": ready["RangePos"].fillna(0).clip(0, 100),
    })
    st.dataframe(disp, use_container_width=True, hide_index=True,
                 column_config={"Range Pos": st.column_config.ProgressColumn(
                     min_value=0, max_value=100, format="%d%%", width="small")})


def _dash_watchlist_panel(rows, phase):
    from dashboard.formatting import (
        EM_DASH, fmt_live_price, fmt_percent, fmt_price, fmt_score, is_valid_price, scenario_label,
    )
    section_header("قائمة المراقبة · Watchlist (closest to ready)", "waiting for confirmation")
    wait = rows[rows["state"] == "waiting"].copy()
    if wait.empty:
        st.caption("No symbols waiting · لا شيء في الانتظار — nothing is close to a ready state.")
        return
    # Defensive price validity: a symbol with no valid live price is NOT ranked
    # closest-to-ready and never contributes a fake 0.00; move it to Data Unavailable.
    wait["_valid"] = wait["Last"].map(is_valid_price)
    valid = wait[wait["_valid"]].copy()
    invalid = wait[~wait["_valid"]]
    valid["_room"] = pd.to_numeric(valid.get("RemainingUpside"), errors="coerce").fillna(999).abs()
    valid = valid.sort_values(["_room", "Rank"]).head(15)
    disp = pd.DataFrame({
        "Symbol": valid["Symbol"], "Expected scenario": valid["BestScenario"].map(scenario_label),
        "Last": valid["Last"].map(fmt_live_price), "Trigger": valid["Entry"].map(fmt_price),
        "Room %": valid["RemainingUpside"].map(lambda x: fmt_percent(x)),
        "Spread%": valid["Spread"].map(lambda x: fmt_percent(x)), "Score": valid["Score"].map(fmt_score),
    })
    if not disp.empty:
        st.dataframe(disp, use_container_width=True, hide_index=True)
    if not invalid.empty:
        # Reference-only view: no valid last price exists, so the price is an em dash
        # and only the historical Trigger is shown — never implying an observed price.
        ref = pd.DataFrame({
            "Symbol": invalid["Symbol"], "Expected scenario": invalid["BestScenario"].map(scenario_label),
            "Last": [EM_DASH] * len(invalid), "Trigger (ref)": invalid["Entry"].map(fmt_price),
            "Data Quality": ["Last Price Unavailable"] * len(invalid)})
        st.caption(f"Data Unavailable ({len(invalid)}) — no valid last price available; "
                   f"shown for historical trigger reference only, excluded from closest-to-ready ranking:")
        st.dataframe(ref, use_container_width=True, hide_index=True)


_TONE_HEX = {"green": "#34d399", "amber": "#fbbf24", "red": "#f87171",
             "gray": "#94a3b8", "blue": "#60a5fa"}


def _dash_top_candidates(rows, phase):
    from dashboard.formatting import (
        data_quality_label, dq_compact, fmt_live_price, fmt_percent, fmt_range, fmt_score,
        fmt_turnover, fmt_volume, is_valid_price, scenario_label, scenario_state_label,
        scenario_state_tone,
    )
    section_header("أفضل المرشحين · Top Candidates", "overview — click a row for the stock drawer")
    # Ready and Waiting symbols are shown in their own sections above; exclude them here
    # but KEEP the real global rank (never renumber).
    top = (rows[rows["Tradable"] & ~rows["state"].isin(["ready", "waiting"])]
           .sort_values("Rank").head(20).reset_index(drop=True))
    if top.empty:
        empty_state("No tradable candidates", "None passed the liquidity gate.", icon="○")
        return
    st.caption("Ranks are global; Ready and Waiting symbols may be shown in the sections above.")
    valid = [is_valid_price(x) for x in top["Last"]]
    dq = [data_quality_label(data_status=ds, hist_stale=hs, advisory=a, phase=phase, last_valid=v)
          for ds, hs, a, v in zip(top["DataStatus"], top["HistStale"], top["Advisory"], valid)]
    disp = pd.DataFrame({
        "Symbol": top["Symbol"], "Rank": top["Rank"], "Score": top["Score"].map(fmt_score),
        "Avg Vol": top["AvgVolume"].map(fmt_volume), "Turnover": top["AvgTurnover"].map(fmt_turnover),
        "ADR%": top["ADR"].map(lambda x: fmt_percent(x, 1)),
        "Range": [fmt_range(l, h) for l, h in zip(top["ExpLow"], top["ExpHigh"])],
        "Live": top["Last"].map(fmt_live_price), "Range Pos": top["RangePos"].fillna(0).clip(0, 100),
        "Scenario": top["BestScenario"].map(scenario_label),
        "State": top["Advisory"].map(scenario_state_label),
        "Data Quality": [dq_compact(d[0]) for d in dq],
    })
    state_tones = [scenario_state_tone(a) for a in top["Advisory"]]
    dq_tones = [d[1] for d in dq]

    def _style(tones):
        return lambda _c: [f"color:{_TONE_HEX.get(t, '#94a3b8')};font-weight:700" for t in tones]

    styler = (disp.style.apply(_style(state_tones), subset=["State"], axis=0)
              .apply(_style(dq_tones), subset=["Data Quality"], axis=0))
    sel = st.dataframe(
        styler, use_container_width=True, hide_index=True, on_select="rerun",
        selection_mode="single-row", key="dash_top",
        column_config={
            "Symbol": st.column_config.TextColumn(width="small", pinned=True),
            "Range Pos": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d%%",
                                                         width="small"),
            "State": st.column_config.TextColumn(width="small", pinned=True),
            "Data Quality": st.column_config.TextColumn(
                width="small",
                help="Compact label; 'Last Snapshot' = last-session snapshot. Full label and "
                     "original quote timestamp are in the stock drawer's Data Quality section.")})
    if sel and sel.selection and sel.selection.get("rows"):
        st.session_state["_ers_drawer_symbol"] = str(disp.iloc[sel.selection["rows"][0]]["Symbol"])


def _dash_classify(rows, phase):
    """Unique-symbol partition of the universe (each symbol in exactly one bucket).

    Session-aware: outside continuous trading a stale LIVE quote is NOT an active
    failure — it is the last-session snapshot, counted separately and excluded from
    active data blockers. Genuine history issues (no history / history lag) stay
    classified in every phase.
    """
    continuous = phase == "CONTINUOUS"
    b = {k: 0 for k in ("ready", "waiting", "rejected_low_liquidity", "no_history",
                        "history_lag", "wide_spread", "live_quote_stale",
                        "last_session_quotes", "range_consumed", "no_room_other")}
    for _, r in rows.iterrows():
        state = r.get("state")
        if state == "ready":
            b["ready"] += 1
        elif state == "waiting":
            b["waiting"] += 1
        elif state == "rejected":
            b["rejected_low_liquidity"] += 1               # not tradable (low liquidity)
        else:                                              # tradable, not ready/waiting
            adv = r.get("Advisory")
            if r.get("DataStatus") == "MISSING":
                b["no_history"] += 1
            elif bool(r.get("HistStale")):
                b["history_lag"] += 1
            elif adv == "SPREAD_TOO_WIDE":
                b["wide_spread"] += 1
            elif adv == "DATA_STALE":                      # stale LIVE quote
                b["live_quote_stale" if continuous else "last_session_quotes"] += 1
            elif adv == "RANGE_CONSUMED":
                b["range_consumed"] += 1
            else:
                b["no_room_other"] += 1
    return b


# Presentation labels for the classify buckets that count as active blockers /
# last-scan classifications (NOT ready/waiting/last_session_quotes).
_BLOCKER_LABELS = [
    ("rejected_low_liquidity", "Low liquidity", "red"),
    ("no_history", "No history", "red"),
    ("history_lag", "History lag", "amber"),
    ("wide_spread", "Wide spread", "red"),
    ("live_quote_stale", "Live quote stale", "red"),
    ("range_consumed", "Range consumed / no room", "amber"),
    ("no_room_other", "No target room", "amber"),
]


def _dash_blockers(rows, phase):
    title = {"CONTINUOUS": "لماذا لا توجد فرص الآن؟ · Why no opportunities now?",
             "AUCTION": "لا دخول جديد — مزاد الإغلاق · No new entries — closing auction",
             }.get(phase, "تصنيف آخر فحص · Last Scan Classification")
    caption = ("active unique-symbol blockers" if phase == "CONTINUOUS"
               else "last-scan classification — not currently actionable")
    section_header(title, caption)
    b = _dash_classify(rows, phase)
    total = len(rows)
    active = [(lbl, b[k], tone) for k, lbl, tone in _BLOCKER_LABELS if b[k] > 0]
    if not active:
        st.caption("No blockers — ready opportunities present.")
    else:
        chips = "  ".join(badge_html(f"{lbl}: {v} ({v / total:.0%})", tone) for lbl, v, tone in active)
        st.markdown(chips, unsafe_allow_html=True)

    # Count reconciliation — market-closed quote age kept separate from active failures.
    parts = [f"Ready **{b['ready']}**", f"Waiting **{b['waiting']}**",
             f"Rejected/low-liquidity **{b['rejected_low_liquidity']}**"]
    active_data = b["no_history"] + b["history_lag"] + b["wide_spread"] + b["live_quote_stale"]
    parts.append(f"Active data blockers **{active_data}**")
    if b["last_session_quotes"]:
        parts.append(f"Last-session quotes **{b['last_session_quotes']}**")
    st.caption("Unique-symbol accounting: " + " · ".join(parts)
               + f" · total **{total}**")


def _read_csv(path):
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def show_live_opportunities():
    config, database = _context()
    page_header("Live Opportunities", "Rubix-only technical setups with explicit actionability", icon="🎯", badge="SCALPING")
    if not config.enabled:
        st.info("Scalping is disabled by default. Enable it explicitly in Scalping Settings for paper testing.")
    if st.button("Scan Rubix Intraday", type="primary", disabled=not config.enabled):
        with st.spinner("Reading Rubix SQLite and evaluating the three isolated setups..."):
            st.session_state.scalping_scan = ScalpingScanner(config=config, database=database).scan()
    result = st.session_state.get("scalping_scan")
    if not result:
        empty_state("No scalping scan yet", "Enable PAPER_ONLY and run the isolated intraday scanner.", icon="🔎")
        return
    quality = result.get("data_quality") or {}
    if quality.get("invalid_bars_removed"):
        st.info(
            f"Data quality: rejected {quality['invalid_bars_removed']:,} impossible "
            f"zero/invalid candle(s) across {quality.get('affected_symbols', 0)} "
            "symbol(s) before technical evaluation. No price was filled or inferred."
        )
    # Older in-memory scan results stored all no-data outcomes under failures.
    # Partition them at render time so an app rerun immediately shows accurate
    # semantics without requiring the user to discard session state.
    skipped = list(result.get("skipped") or [])
    unexpected_failures = []
    for item in result.get("failures") or []:
        description = intraday_evidence_description(item.get("reason"))
        if description:
            normalized = dict(item)
            normalized["explanation"] = description
            skipped.append(normalized)
        else:
            unexpected_failures.append(item)
    coverage = result.get("coverage") or {}
    requested = coverage.get("requested_symbols")
    evaluated = coverage.get("evaluated_symbols")
    if requested is not None and evaluated is not None:
        st.caption(
            f"Intraday coverage: {evaluated:,} of {requested:,} symbol(s) evaluated · "
            f"{len(skipped):,} without sufficient current-session evidence · "
            f"{len(unexpected_failures):,} unexpected error(s)."
        )
    rows = pd.DataFrame(result["opportunities"])
    if rows.empty:
        empty_state("No qualified setups", "No conservative setup currently meets the technical gate.", icon="○")
    else:
        display = rows.rename(columns={
            "timestamp": "time", "ask": "entry_ask", "freshness": "data_freshness",
        })
        display["target_2pct"] = display["entry_ask"] * 1.02
        display["stop_2pct"] = display["entry_ask"] * 0.98
        columns = [
            "ticker", "setup", "time", "entry_ask", "target_2pct", "stop_2pct",
            "spread_percent", "volume", "score", "reasons", "data_freshness",
            "actionable", "blocked_reason",
        ]
        st.dataframe(display[columns], use_container_width=True, hide_index=True)
        actionable = rows[rows["actionable"].fillna(False).astype(bool)]
        if not actionable.empty:
            st.caption("Paper execution only — entry is modelled at Ask plus configured slippage.")
            labels = [
                f"{row.ticker} · {row.setup} · {row.timestamp}"
                for row in actionable.itertuples()
            ]
            selected = st.selectbox("Qualified paper opportunity", labels)
            if st.button("Open Paper Trade", type="primary"):
                source = actionable.iloc[labels.index(selected)].to_dict()
                item = Opportunity(
                    ticker=source["ticker"], setup=SetupType(source["setup"]),
                    timestamp=pd.Timestamp(source["timestamp"]).to_pydatetime(),
                    signal_price=float(source["signal_price"]), ask=float(source["ask"]),
                    bid=float(source["bid"]), volume=float(source["volume"]),
                    spread_percent=float(source["spread_percent"]),
                    score=float(source["score"]), reasons=tuple(source["reasons"]),
                    freshness=str(source["freshness"]), actionable=True,
                    blocked_reason=None, ai_probability=source.get("ai_probability"),
                    opportunity_id=str(source["opportunity_id"]),
                )
                state = database.load_risk_state(
                    cairo_now().date().isoformat(), config.initial_capital
                )
                trade, reason = ScalpingPaperPortfolio(config, database).execute(
                    item, source["signal_id"], state, now=cairo_now()
                )
                if trade:
                    st.success(
                        f"Paper fill recorded at {trade.entry.actual_entry_fill:.3f}; "
                        f"target {trade.entry.target_price:.3f}; stop {trade.entry.stop_price:.3f}."
                    )
                else:
                    st.error(f"Paper entry rejected: {reason}")
    if skipped:
        with st.expander(f"Symbols without sufficient intraday evidence ({len(skipped)})"):
            st.caption(
                "These symbols were safely skipped. No old candle or synthetic price was used "
                "for a scalping decision."
            )
            st.dataframe(pd.DataFrame(skipped), use_container_width=True, hide_index=True)
    if unexpected_failures:
        with st.expander(f"Unexpected scanner errors ({len(unexpected_failures)})"):
            st.dataframe(pd.DataFrame(unexpected_failures), use_container_width=True, hide_index=True)


def show_active_scalping_trades():
    config, database = _context()
    page_header("Active Scalping Trades", "Open paper positions and fixed fill-relative levels", icon="📍", badge="PAPER_ONLY")
    if st.button("Refresh Paper Positions"):
        result = ScalpingScanner(config=config, database=database).update_open_positions()
        if result["failures"]:
            st.warning(f"Position refresh completed with {len(result['failures'])} data failure(s).")
        else:
            st.success(f"Position refresh complete; {result['closed']} closed.")
    rows = database.open_positions()
    if not rows:
        empty_state("No active trades", "Qualified paper fills will appear here.", icon="○")
        return
    for row in rows:
        current = float(row.get("current_bid") or row["entry_fill"])
        quantity = int(row["quantity"])
        gross = (current - row["entry_fill"]) * quantity
        cols = st.columns(4)
        cols[0].metric(row["ticker"], row["setup"])
        cols[1].metric("Entry / Current Bid", f"{row['entry_fill']:.3f} / {current:.3f}")
        cols[2].metric("Target / Stop", f"{row['target_price']:.3f} / {row['stop_price']:.3f}")
        cols[3].metric("Gross P&L", f"{gross:,.2f}")
        st.caption(
            f"Holding: {_holding_minutes(row['opened_at']):.0f} min · "
            f"Distance TP: {(row['target_price']/current-1)*100:.2f}% · "
            f"Distance SL: {(row['stop_price']/current-1)*100:.2f}%"
        )


def show_scalping_paper_portfolio():
    config, database = _context()
    page_header("Scalping Paper Portfolio", "Dedicated virtual capital and risk state", icon="🧾", badge="PAPER_ONLY")
    summary = _today_summary(database, config)
    cols = st.columns(4)
    cols[0].metric("Virtual capital", f"{config.initial_capital + summary['realized']:,.2f}")
    cols[1].metric("Realized", f"{summary['realized']:,.2f}")
    cols[2].metric("Unrealized", f"{summary['unrealized']:,.2f}")
    cols[3].metric("Open positions", summary["active"])
    closed = database.rows(
        """SELECT p.ticker,p.setup,p.opened_at,e.* FROM exits e
           JOIN open_positions p ON p.position_id=e.position_id ORDER BY e.exited_at DESC LIMIT 200"""
    )
    section_header("Closed paper trades", "Append-only fills and exits")
    if closed:
        st.dataframe(pd.DataFrame(closed), use_container_width=True, hide_index=True)
    else:
        st.info("No scalping paper trades have closed yet.")


def show_scalping_history():
    config, database = _context()
    page_header("Scalping History", "Persistent signals, rejections, fills, exits and alerts", icon="🗂️", badge="SCALPING")
    tabs = st.tabs(["Signals", "Rejected", "Fills", "Exits", "Alerts"])
    queries = [
        "SELECT * FROM signals ORDER BY observed_at DESC LIMIT 500",
        "SELECT * FROM rejected_opportunities ORDER BY observed_at DESC LIMIT 500",
        "SELECT * FROM fills ORDER BY filled_at DESC LIMIT 500",
        "SELECT * FROM exits ORDER BY exited_at DESC LIMIT 500",
        "SELECT * FROM alerts ORDER BY created_at DESC LIMIT 500",
    ]
    for tab, query in zip(tabs, queries):
        with tab:
            rows = database.rows(query)
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            else:
                st.info("No records yet.")


def show_scalping_settings():
    config, database = _context()
    page_header("Scalping Settings", "Independent settings; the daily engine is untouched", icon="🛠️", badge="PAPER_ONLY")
    with st.form("scalping_settings"):
        enabled = st.toggle("Enable isolated paper scalping", value=config.enabled)
        st.text_input("Mode", value="PAPER_ONLY", disabled=True)
        c1, c2 = st.columns(2)
        tp = c1.number_input("Take Profit % (production default)", value=float(config.take_profit_percent), min_value=0.1, step=0.1)
        sl = c2.number_input("Stop Loss % (production default)", value=float(config.stop_loss_percent), min_value=0.1, step=0.1)
        c3, c4, c5 = st.columns(3)
        risk = c3.number_input("Risk per trade %", value=float(config.risk_per_trade_percent), min_value=0.1, step=0.1)
        max_loss = c4.number_input("Maximum daily loss %", value=float(config.max_daily_loss_percent), min_value=0.1, step=0.1)
        max_positions = c5.number_input("Maximum open positions", value=int(config.max_open_positions), min_value=1, step=1)
        c6, c7 = st.columns(2)
        cutoff = c6.text_input("Stop new entries", value=config.entry_cutoff)
        forced = c7.text_input("Force close all positions", value=config.forced_exit_time)
        submitted = st.form_submit_button("Save Scalping Settings", type="primary")
    if submitted:
        values = config.as_dict()
        values.update({
            "enabled": bool(enabled), "mode": "PAPER_ONLY",
            "take_profit_percent": float(tp), "stop_loss_percent": float(sl),
            "risk_per_trade_percent": float(risk),
            "max_daily_loss_percent": float(max_loss),
            "max_open_positions": int(max_positions),
            "entry_cutoff": cutoff, "forced_exit_time": forced,
            "require_rubix_fresh": True, "allow_yahoo_actionable": False,
            "close_at_session_end": True,
        })
        try:
            candidate = ScalpingConfig.from_mapping(values)
            candidate.entry_cutoff_time
            candidate.forced_exit_clock
            settings.set("scalping", values)
            st.success("Scalping settings saved independently. Mode remains PAPER_ONLY.")
        except Exception as error:
            st.error(f"Settings were not saved: {error}")
    st.caption(f"Database: {database.path} · Integrity: {database.integrity_check()['status']}")


def _timestamp(value):
    return pd.Timestamp(value).tz_convert("Africa/Cairo").strftime("%Y-%m-%d %H:%M:%S") if value else "N/A"


def _holding_minutes(value):
    opened = datetime.fromisoformat(value)
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - opened.astimezone(timezone.utc)).total_seconds() / 60)


# This page changes only the evidence source; ScalpingBacktest remains untouched.
def show_scalping_backtest():
    config, _database = _context()
    page_header(
        "Scalping Backtest",
        "Rubix SQLite by default · CSV and archive remain optional",
        icon="⏱️",
        badge="RESEARCH · PAPER ONLY",
    )
    st.warning(
        "A valid scalping backtest requires Rubix ticks or 1-minute bars. "
        "The collector database is read-only and daily OHLCV is still rejected."
    )
    st.caption(
        "The unchanged engine models Ask entries, Bid exits, fees, slippage, "
        "session close, missed capacity, and conservative same-bar ambiguity."
    )

    section_header("Data Source", "Rubix database is recommended and never modified")
    source = st.radio(
        "Source",
        ("Rubix Live Database (Recommended)", "CSV Files", "Historical Archive"),
        horizontal=True,
        label_visibility="collapsed",
        key="scalping_backtest_source",
    )
    loader = None
    ready = False
    load_error = None
    source_metadata = {"source": source}

    if source == "Rubix Live Database (Recommended)":
        market = settings.get("market_data")
        database_path = st.text_input(
            "Database",
            value=str(market.get("rubix_db_path", "data/rubix_live_market.db")),
            help="SQLite mode=ro + PRAGMA query_only. No adapter table can be changed.",
        )
        try:
            repository = RubixScalpingDataSource(database_path)
            status = repository.status()
            cards = st.columns(4)
            cards[0].metric("Database", status.database_status)
            cards[1].metric("Stored Sessions", status.session_count)
            cards[2].metric("Stored Symbols", status.symbol_count)
            cards[3].metric("Minute Bars", f"{status.minute_bar_count:,}")
            st.caption(f"Latest stored candle: {status.latest_minute or 'N/A'} · {status.database_path}")
            dates = repository.session_dates()
            if not dates:
                load_error = "The Rubix database contains no stored one-minute sessions yet."
            else:
                selectors = st.columns((1, 1, 2))
                selected_date = selectors[0].selectbox(
                    "Session Date", dates, format_func=lambda value: value.isoformat()
                )
                selectors[1].text_input("Market", value="EGX", disabled=True)
                available_symbols = repository.symbols(selected_date)
                preferred = ("COMI.CA", "SWDY.CA", "EAST.CA", "FWRY.CA", "TMGH.CA")
                defaults = [item for item in preferred if item in available_symbols]
                if not defaults:
                    defaults = list(available_symbols[: min(10, len(available_symbols))])
                selected_symbols = selectors[2].multiselect(
                    "Symbols",
                    available_symbols,
                    default=defaults,
                    format_func=lambda value: value.rsplit(".", 1)[0],
                    key=f"scalping_db_symbols_{selected_date.isoformat()}",
                )
                ready = bool(selected_symbols)
                source_metadata.update({
                    "database": status.database_path,
                    "database_status": status.database_status,
                    "session_date": selected_date.isoformat(),
                    "symbols": list(selected_symbols),
                })
                if ready:
                    loader = lambda: repository.load_session(selected_date, selected_symbols)
        except ScalpingDataSourceError as error:
            load_error = str(error)
    elif source == "CSV Files":
        uploads = st.file_uploader(
            "Rubix intraday CSV files",
            type=["csv"],
            accept_multiple_files=True,
            help="Each file must contain Timestamp/Date, OHLCV, and preferably Bid/Ask and size columns.",
        )
        ready = bool(uploads)
        source_metadata["files"] = [item.name for item in uploads or ()]
        if ready:
            loader = lambda: load_csv_frames(uploads)
    else:
        archive_root = st.text_input("Historical archive folder", value="data/scalping_archive")
        archived = historical_archive_files(archive_root)
        selected_files = st.multiselect(
            "Archived intraday sessions",
            archived,
            format_func=lambda value: Path(value).name,
        )
        if not archived:
            st.info(
                "No archived CSV files found. Add intraday CSVs to "
                "data/scalping_archive or choose another folder."
            )
        ready = bool(selected_files)
        source_metadata.update({
            "archive_folder": archive_root,
            "files": [str(item) for item in selected_files],
        })
        if ready:
            loader = lambda: load_csv_frames(selected_files)

    if load_error:
        st.error(load_error)

    section_header("Frozen Strategy", "Display only — no parameter is changed on this page")
    strategy = st.columns(5)
    strategy[0].number_input("Take Profit %", value=float(config.take_profit_percent), disabled=True)
    strategy[1].number_input("Stop Loss %", value=float(config.stop_loss_percent), disabled=True)
    strategy[2].time_input("Market Start", value=REGULAR_OPEN, disabled=True)
    strategy[3].time_input("Last Entry", value=config.entry_cutoff_time, disabled=True)
    strategy[4].time_input("Force Exit", value=config.forced_exit_clock, disabled=True)
    st.caption(
        "Rubix candles_1m stores OHLCV, not historical Bid/Ask. When Bid/Ask is "
        "absent, the existing conservative spread assumption is used exactly as before."
    )

    if st.button(
        "Run Scalping Backtest",
        type="primary",
        disabled=not ready or loader is None,
        width="stretch",
    ):
        try:
            with st.spinner("Reading intraday evidence and running the unchanged scalping engine..."):
                loaded = loader()
                errors = []
                if isinstance(loaded, tuple):
                    loaded, errors = loaded
                if errors:
                    st.error("; ".join(errors))
                if not loaded:
                    raise ScalpingDataSourceError("No usable intraday frames were loaded.")
                research_config = ScalpingConfig.from_mapping({**config.as_dict(), "enabled": True})
                result = ScalpingBacktest(research_config).run(loaded)
                result["source_metadata"] = source_metadata
                st.session_state.scalping_backtest = result
                st.success(f"Backtest completed from {source} using {len(loaded)} symbol(s).")
        except Exception as error:
            st.error(f"Scalping backtest could not run: {error}")

    result = st.session_state.get("scalping_backtest")
    if result:
        metadata = result.get("source_metadata") or {}
        source_caption = f"Displayed result source: {metadata.get('source', 'Legacy CSV run')}"
        if metadata.get("session_date"):
            source_caption += f" · Session: {metadata['session_date']}"
        st.caption(source_caption)
        if result["limitations"]:
            st.warning("Coverage limitations: " + " | ".join(result["limitations"]))
        st.dataframe(pd.DataFrame([result["metrics"]]), use_container_width=True, hide_index=True)
        tabs = st.tabs(["Trades", "By Setup", "By Time", "By Symbol"])
        tables = (
            pd.DataFrame(result["trades"]), result["by_setup"],
            result["by_time_of_day"], result["by_symbol"],
        )
        for tab, table in zip(tabs, tables):
            with tab:
                if table is not None and not table.empty:
                    st.dataframe(table, use_container_width=True, hide_index=True)
                else:
                    st.info("No qualifying records for this view.")
