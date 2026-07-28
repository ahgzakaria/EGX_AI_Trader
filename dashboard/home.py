"""Primary live-market dashboard; presentation never changes decisions."""

import logging
import pandas as pd
import streamlit as st

from core import scan_job_manager as job_manager
from core.data_provider import summarize_frames
from dashboard.scan_status_panel import coverage_view, scan_status_view
from decision_support.service import DecisionSupportService
from dashboard.stock_details import show_stock_details
from dashboard.ui import empty_state, page_header, section_header, status_bar


logger = logging.getLogger(__name__)

SCAN_SOURCE = "data/symbols.csv"
#: Poll interval for the progress fragment. Fast enough to feel live, slow enough that
#: the page is not re-running constantly while a scan works.
SCAN_POLL_SECONDS = 0.75

#: Idempotency keys for the terminal handoff. A finished scan must release the page
#: exactly once and publish its result exactly once, however many times the fragment
#: ticks, the page reruns, or another observer attaches.
TERMINAL_RERUN_KEY = "scan_terminal_rerun_scan_id"
TERMINAL_CONSUMED_KEY = "terminal_result_consumed"

#: Headline per terminal state. A run with gaps is never announced as a clean success.
TERMINAL_HEADLINES = {
    job_manager.COMPLETED: ("success", "Scan complete"),
    job_manager.COMPLETED_WITH_GAPS: ("warning", "Scan complete with coverage gaps"),
    job_manager.CANCELLED: ("warning", "Scan cancelled — partial diagnostics only"),
    job_manager.FAILED: ("error", "Scan failed"),
}


def _observed_metadata(results):
    """What the finished scan actually saw, for the final banner.

    Read straight from the typed provenance fields the scanner already recorded on each
    row — nothing is recomputed and no provider is consulted.
    """
    sessions = [row.get("LastCompletedSession") or row.get("CompletedSessionTimestamp")
                for row in results or ()]
    sessions = [str(value)[:10] for value in sessions if value]
    statuses = {str(row.get("LivePriceStatus") or "").upper()
                for row in results or ()} - {""}
    return {
        "latest_completed_candle": max(sessions) if sessions else "",
        # Any stale overlay downgrades the reported live state; it never touches the
        # historical source.
        "live_quote_freshness": "STALE" if "STALE" in statuses else "",
    }


def _render_terminal_summary(job):
    """Headline and counts for a finished job, above the existing results renderer."""
    snapshot = job.progress()
    tone, headline = TERMINAL_HEADLINES.get(snapshot.state, ("info", "Scan finished"))
    getattr(st, tone)(f"{headline} · scan `{snapshot.scan_id}`")
    if job.sanitized_error:
        st.error(job.sanitized_error)

    row = st.columns(5)
    row[0].metric("Completed", f"{snapshot.completed} / {snapshot.total}")
    row[1].metric("Successful", snapshot.success)
    row[2].metric("Skipped", snapshot.skipped)
    row[3].metric("Failed", snapshot.failed)
    coverage = coverage_view(snapshot)
    row[4].metric("Coverage", f"{coverage['coverage_percent']:.1f}%",
                  help="Analytical coverage — successful results / approved symbols.")
    if snapshot.status_breakdown:
        st.caption("Typed outcome breakdown")
        # Rendered as text, not a dataframe: these typed reasons are the explanation for
        # the coverage gap, so they must be readable at a glance (and assertable in the
        # DOM — Streamlit renders dataframes to a canvas, where the values are invisible).
        st.markdown(" · ".join(
            f"`{status}` **{count}**"
            for status, count in sorted(snapshot.status_breakdown.items(),
                                        key=lambda item: -item[1])))


def _format_duration(seconds):
    if seconds is None:
        return "—"
    seconds = int(max(0, seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _render_scan_status(job_progress, expected_session=None, result_metadata=None):
    """The honest three-line provider banner."""
    view = scan_status_view(job_progress, expected_session=expected_session,
                            result_metadata=result_metadata)
    coverage = coverage_view(job_progress)
    status_bar([
        ("Historical analysis source", view["historical_source"], "blue"),
        ("Latest completed candle", view["latest_completed_candle"], "gray"),
        ("Live quote overlay", view["live_overlay"],
         "green" if view["live_overlay"] == "Rubix Fresh"
         else "amber" if "Partially" in view["live_overlay"]
         or "Stale" in view["live_overlay"] else "gray"),
    ])
    return coverage


def _render_scan_job(job):
    """Render the background job, polling only while it is still active."""

    def _body():
        snapshot = job.progress()
        state, total = snapshot.state, max(1, snapshot.total)
        st.markdown(f"**{snapshot.stage}** · scan `{snapshot.scan_id}` · `{state}`")
        st.progress(min(1.0, snapshot.completed / total),
                    text=f"{snapshot.completed} / {snapshot.total}")

        row = st.columns(4)
        row[0].metric("Elapsed", _format_duration(snapshot.elapsed_seconds))
        row[1].metric("Remaining (est.)",
                      _format_duration(snapshot.estimated_remaining_seconds))
        row[2].metric("Current symbol", snapshot.current_symbol or "—")
        row[3].metric("Last symbol",
                      f"{snapshot.last_completed_symbol or '—'}"
                      + (f" · {snapshot.last_symbol_seconds:.2f}s"
                         if snapshot.last_symbol_seconds else ""))

        row = st.columns(4)
        row[0].metric("Successful", snapshot.success)
        row[1].metric("Skipped", snapshot.skipped)
        row[2].metric("Failed", snapshot.failed)
        coverage = coverage_view(snapshot)
        row[3].metric("Coverage", f"{coverage['coverage_percent']:.1f}%",
                      help="Analytical coverage — successful results / approved symbols. "
                           "Progress is a different number.")

        row = st.columns(4)
        row[0].metric("EODHD cache hits", snapshot.eodhd_cache_hits)
        row[1].metric("EODHD refreshes", snapshot.eodhd_refresh_attempts)
        row[2].metric("Rubix overlays",
                      f"{snapshot.rubix_overlay_available}/{snapshot.total}")
        row[3].metric("Circuit breaker", snapshot.circuit_breaker_state)

        if snapshot.status_breakdown:
            st.caption("Typed outcome breakdown")
            st.dataframe(
                pd.DataFrame(sorted(snapshot.status_breakdown.items(),
                                    key=lambda item: -item[1]),
                             columns=["Status", "Symbols"]),
                hide_index=True, use_container_width=True)
        if job.sanitized_error:
            st.error(job.sanitized_error)

    def _polling_body():
        """Fragment body: render, then hand off exactly once when the job finishes.

        ``run_every`` re-runs ONLY this fragment. When the worker reaches a terminal
        state the fragment would otherwise just stop polling, leaving the surrounding
        page frozen on whatever the last full script run captured — a stale banner, a
        Stop button, and no results. One app-scoped rerun releases the page so the
        normal results renderer takes over. It is guarded by scan_id so repeated
        fragment ticks, page reruns and a second observer cannot repeat the handoff.
        """
        _body()
        if not job.is_active and st.session_state.get(TERMINAL_RERUN_KEY) != job.scan_id:
            st.session_state[TERMINAL_RERUN_KEY] = job.scan_id
            st.rerun(scope="app")

    if job.is_active:
        st.fragment(_polling_body, run_every=SCAN_POLL_SECONDS)()
    else:
        # Terminal: progress stays available but must never stand in for the results.
        with st.expander(f"Scan progress · {job.progress().scan_id}", expanded=False):
            _body()


def _adopt_finished_job(job):
    """Publish a terminal job's result into session state exactly once.

    Idempotent on ``scan_id``: reruns, refreshes and a second observer all re-enter
    here, and none of them may repeat the decision-support snapshot or re-expose a
    cancelled run as a completed one. Nothing here re-runs analysis, contacts a
    provider, archives, or finalizes — the worker already did all of that.
    """
    if st.session_state.get(TERMINAL_CONSUMED_KEY) == job.scan_id:
        return
    st.session_state[TERMINAL_CONSUMED_KEY] = job.scan_id
    st.session_state.adopted_scan_id = job.scan_id
    result = job.final_result
    if result is None:
        return
    # A cancelled or failed run keeps its partial rows as diagnostics but is never
    # presented as a recorded session.
    st.session_state.results = result
    st.session_state.live_scan_completed = bool(result) and not job.cancelled
    st.session_state.archive_warning = _archive_warning(result)
    if result and not job.cancelled:
        # Phase 9 is a post-decision advisory snapshot. Failures are disclosed but can
        # never invalidate or rewrite scanner rows.
        try:
            advisory = DecisionSupportService().analyze(result)
            st.session_state.decision_support_report = advisory.get("daily_report")
            st.session_state.decision_support_error = None
        except Exception as error:
            logger.exception("Decision-support snapshot failed")
            st.session_state.decision_support_error = str(error)


def show_dashboard():
    page_header(
        "Swing / Daily Market Dashboard",
        "Multi-day frozen strategy · Daily candles · Separate from paper scalping",
        icon="📊",
        badge="SWING · DAILY",
    )
    st.info(
        "Current workspace: SWING / DAILY. Signals and levels here belong to the "
        "validated multi-day strategy. For intraday +2%/−2% paper research, open "
        "‘Scalping Dashboard · Paper’ from WORKSPACES."
    )

    live_policy_version = "SWING_HISTORY_PLUS_RUBIX_QUOTE_V5_ADAPTIVE_SELECTOR"
    if st.session_state.get("live_policy_version") != live_policy_version:
        # Invalidate only old in-memory hard-filter results once.
        st.session_state.results = None
        st.session_state.live_scan_completed = False
        st.session_state.live_policy_version = live_policy_version
    if "results" not in st.session_state:
        st.session_state.results = None

    # The provider banner reads the configured operational route and the live job — it
    # never infers a Yahoo fallback from Rubix quote health.
    job = job_manager.REGISTRY.get(job_manager.workspace_key_for("dashboard", SCAN_SOURCE))
    provider_placeholder = st.empty()
    with provider_placeholder.container():
        _render_scan_status(job.progress() if job is not None else None)

    scan_col, status_col = st.columns([3, 1])
    active = job is not None and job.is_active
    scan_completed = st.session_state.get("live_scan_completed", False)
    # A zero-result run is a recorded failed experiment, not a completed live
    # scan. Keep the evidence, but allow the operator to retry after repairing
    # the data route instead of leaving the button permanently disabled.
    if scan_completed and st.session_state.results == []:
        st.session_state.live_scan_completed = False
        scan_completed = False
    with scan_col:
        if st.button(
            "🔍 Run Market Scan",
            type="primary",
            use_container_width=True,
            disabled=active or scan_completed,
            help="One immutable market scan is recorded per application session.",
            key="run_market_scan",
        ):
            # Atomic: a double click, a rerun or a second tab attaches to the job that
            # already owns this workspace instead of starting a second scan.
            job, created = job_manager.start_scan_job(SCAN_SOURCE, "dashboard")
            if not created:
                st.info(f"A market scan is already running (scan {job.scan_id}).")
            st.rerun()
    with status_col:
        if active:
            if st.button("⏹ إيقاف الفحص · Stop Scan", use_container_width=True,
                         key=f"stop_scan_{job.scan_id}"):
                job.request_cancel()          # idempotent
                st.rerun()
        else:
            st.button(
                "● Session recorded" if scan_completed else "○ Ready to scan",
                disabled=True,
                use_container_width=True,
            )

    if job is not None:
        if not active:
            # Terminal: publish the worker's result BEFORE the progress panel, so the
            # summary and the normal market-results renderer below both see it.
            _adopt_finished_job(job)
            _render_terminal_summary(job)
        _render_scan_job(job)
        if active:
            # An active scan owns the page: results are published only once the worker
            # reaches a terminal state, and the fragment releases the page then.
            return

    archive_warning = st.session_state.get("archive_warning")
    if archive_warning:
        st.warning(archive_warning)

    if st.session_state.results is None:
        empty_state(
            "No market scan yet",
            "Run the scanner to create an immutable Phase 6/7 experiment.",
            icon="🔎",
        )
        return

    results = st.session_state.results
    if not results:
        empty_state(
            "No market data returned",
            "Review failed-symbol details and the market-data connection.",
            icon="⚠️",
        )
        return

    if st.session_state.get("decision_support_error"):
        st.warning(
            "The frozen market scan completed, but its optional decision-support "
            f"snapshot failed: {st.session_state.decision_support_error}"
        )

    df = pd.DataFrame(results)
    # The final banner is rendered from what the scan actually observed. It must NOT go
    # back through ``summarize_frames``: with no observations that helper infers a
    # fallback from Rubix quote health and announces ``local_cache:yahoo`` for the daily
    # history provider — the exact mislabel this page was fixed to stop telling.
    provider_placeholder.empty()
    with provider_placeholder.container():
        if job is not None:
            _render_scan_status(job.progress(),
                                result_metadata=_observed_metadata(results))
        else:
            # No job in this session (e.g. results restored from an older run): keep the
            # legacy summary rather than inventing a provider we did not observe.
            _render_provider_status(summarize_frames(
                [row.get("Data") for row in results], purpose="dashboard"))
    df["_AIProbabilitySort"] = pd.to_numeric(
        df["AIProbability"], errors="coerce"
    ).fillna(-1.0)
    df = df.sort_values(
        ["_AIProbabilitySort", "Confidence", "Score"], ascending=False
    ).reset_index(drop=True)

    buy = int((df["Signal"] == "BUY").sum())
    watch = int((df["Signal"] == "WATCH").sum())
    avoid = int((df["Signal"] == "AVOID").sum())
    ai_values = pd.to_numeric(df["AIProbability"], errors="coerce").dropna()
    avg_ai = round(ai_values.mean(), 1) if not ai_values.empty else None
    actionable = int(pd.Series(df.get("Actionable", False)).fillna(False).astype(bool).sum())
    non_actionable = int(
        df.get("FinalActionability", pd.Series(index=df.index, dtype=str))
        .fillna("").astype(str).str.contains("NON_ACTIONABLE").sum()
    )
    breakout_buy = int((df.get("BreakoutDecision") == "BUY").sum())
    breakout_watch = int((df.get("BreakoutDecision") == "WATCH").sum())
    breakout_avoid = int((df.get("BreakoutDecision") == "AVOID").sum())
    breakout_overlap = int(
        ((df["Signal"] == "BUY") & (df.get("BreakoutDecision") == "BUY")).sum()
    )

    run_id = results[0].get("RunID")
    latest_dates = [
        pd.Timestamp(row["Data"].index[-1])
        for row in results if row.get("Data") is not None and len(row["Data"])
    ]
    latest_date = max(latest_dates).date().isoformat() if latest_dates else "N/A"
    st.caption(
        f"Run: {run_id or 'N/A'}  •  Latest market candle: {latest_date}  •  "
        "Policy: Strategy Only + AI Advisory"
    )

    # Frozen strategy outcomes remain visually primary. Operational evidence
    # is available below in one compact expander instead of twelve cards.
    primary = st.columns(3)
    primary[0].metric("🟢 BUY", buy, help="Frozen strategy BUY decisions")
    primary[1].metric("🟡 WATCH", watch)
    primary[2].metric("🔴 AVOID", avoid)
    context = st.columns(3)
    context[0].metric("Symbols successfully analyzed", len(df))
    context[1].metric("Latest completed daily candle", latest_date)
    # Read from the rows the scan produced rather than re-summarising frames, so this
    # tile cannot disagree with the banner above it.
    live_providers = {str(row.get("LiveProvider") or "").strip().lower()
                      for row in results} - {"", "unavailable"}
    context[2].metric(
        "Live quote source",
        next(iter(sorted(live_providers)), "Unavailable").title(),
    )

    coverage = list(getattr(results, "coverage", []) or [])
    failed_coverage = [
        row for row in coverage if not row.get("accepted_into_swing_scan")
    ]
    if failed_coverage:
        status_counts = pd.Series([
            row.get("final_status", "NOT_ANALYZED") for row in failed_coverage
        ]).value_counts()
        st.warning(
            f"{len(failed_coverage)} symbols were not analyzed and are not "
            "counted as AVOID: "
            + ", ".join(f"{key}={value}" for key, value in status_counts.items())
        )
        with st.expander("Coverage failures", expanded=False):
            st.dataframe(
                pd.DataFrame(failed_coverage)[[
                    "symbol", "historical_row_count", "rejection_category",
                    "rejection_reason", "final_status",
                ]],
                use_container_width=True,
                hide_index=True,
            )

    with st.expander("Additional analysis metrics", expanded=False):
        extra = st.columns(5)
        extra[0].metric("Average Confidence", f"{df['Confidence'].mean():.1f}%")
        extra[1].metric("Average Score", f"{df['Score'].mean():.1f}")
        extra[2].metric(
            "Avg AI on evaluated BUY",
            f"{avg_ai}%" if avg_ai is not None else "N/A",
        )
        extra[3].metric("Actionable Live BUY", actionable)
        extra[4].metric("Non-actionable", non_actionable)

    section_header("Market Overview", "Signal and regime distribution")
    chart_col, regime_col = st.columns(2)
    with chart_col:
        st.bar_chart(pd.DataFrame({
            "Count": [buy, watch, avoid],
        }, index=["BUY", "WATCH", "AVOID"]), color="#2563eb")
    with regime_col:
        regimes = df["Regime"].fillna("Unknown").value_counts()
        st.bar_chart(regimes.rename("Count"), color="#0891b2")

    # Phase 10 comparison is intentionally separate: no merged signal and no
    # change to Classic ranking, filtering, or operational actionability.
    section_header(
        "Classic vs BREAKOUT_SWING",
        "Independent decision-support strategies; decisions are never merged",
    )
    classic_col, breakout_col, overlap_col = st.columns(3)
    with classic_col:
        st.markdown("**Classic Strategy (validated and frozen)**")
        st.caption(f"BUY {buy}  ·  WATCH {watch}  ·  AVOID {avoid}")
    with breakout_col:
        st.markdown("**BREAKOUT_SWING (research only)**")
        st.caption(
            f"BUY {breakout_buy}  ·  WATCH {breakout_watch}  ·  "
            f"AVOID {breakout_avoid}"
        )
    with overlap_col:
        st.markdown("**BUY overlap**")
        st.caption(f"{breakout_overlap} symbol(s) selected by both strategies")

    with st.expander("Per-symbol strategy comparison", expanded=False):
        comparison_columns = [
            "Ticker", "ClassicDecision", "ClassicRR", "BreakoutDecision",
            "BreakoutRR", "BreakoutScore", "BreakoutConfidence",
            "BreakoutEdgeScore", "HigherQualityStrategy",
        ]
        available = [column for column in comparison_columns if column in df.columns]
        st.dataframe(
            df[available],
            use_container_width=True,
            hide_index=True,
            column_config=_market_column_config(),
        )

    section_header(
        "Adaptive Strategy",
        "Walk-forward strategy selection; Classic and BREAKOUT_SWING remain unchanged",
    )
    market_regime = str(df.get("MarketRegime", pd.Series(["UNKNOWN"])).iloc[0])
    regime_confidence = pd.to_numeric(
        df.get("MarketRegimeConfidence", pd.Series([0])), errors="coerce"
    ).fillna(0).iloc[0]
    preferred_counts = df.get(
        "PreferredStrategy", pd.Series(index=df.index, dtype=str)
    ).fillna("NONE").value_counts()
    preferred_strategy = (
        str(preferred_counts.index[0]) if not preferred_counts.empty else "NONE"
    )
    selector_confidence = pd.to_numeric(
        df.get("SelectorConfidence", pd.Series(index=df.index, dtype=float)),
        errors="coerce",
    ).fillna(0).mean()
    expected_edge = pd.to_numeric(
        df.get("StrategyEdgeScore", pd.Series(index=df.index, dtype=float)),
        errors="coerce",
    ).fillna(0).mean()
    adaptive_buy_classic = int(
        (df.get("FinalRecommendation") == "BUY_CLASSIC").sum()
    )
    adaptive_buy_breakout = int(
        (df.get("FinalRecommendation") == "BUY_BREAKOUT").sum()
    )
    adaptive_cards = st.columns(5)
    adaptive_cards[0].metric("Market Regime", market_regime)
    adaptive_cards[1].metric("Preferred Strategy", preferred_strategy)
    adaptive_cards[2].metric("Selector Confidence", f"{selector_confidence:.1f}%")
    adaptive_cards[3].metric("Expected Edge", f"{expected_edge:.1f}")
    adaptive_cards[4].metric(
        "Adaptive BUY",
        adaptive_buy_classic + adaptive_buy_breakout,
        help=(
            f"BUY_CLASSIC={adaptive_buy_classic}; "
            f"BUY_BREAKOUT={adaptive_buy_breakout}"
        ),
    )
    st.caption(
        f"Regime confidence: {regime_confidence:.1f}% · "
        + str(df.get("AdaptiveMarketReasons", pd.Series(["No reason available"])).iloc[0])
    )
    with st.expander("Adaptive per-symbol decisions", expanded=False):
        adaptive_columns = [
            "Ticker", "ClassicDecision", "ClassicRR", "BreakoutDecision",
            "BreakoutRR", "MarketRegime", "ClassicSuccessProbability",
            "BreakoutSuccessProbability", "SelectorConfidence",
            "PreferredStrategy", "StrategyEdgeScore", "FinalRecommendation",
            "WhyPreferred", "WhyNotOther", "SelectorReason",
        ]
        available = [column for column in adaptive_columns if column in df.columns]
        st.dataframe(
            df[available], use_container_width=True, hide_index=True,
            column_config=_market_column_config(),
        )

    section_header("Top Strategy Opportunities", "Frozen BUY signal plus independent operational status")
    top_buy = df[df["Signal"] == "BUY"].head(10)
    if top_buy.empty:
        empty_state(
            "No strategy BUY signals today",
            "AI advisory is visible for evaluated technical candidates but cannot reject them.",
            icon="○",
        )
    else:
        st.dataframe(
            _format_ai_probability(top_buy[[
                "Rank", "Rating", "Ticker", "Regime", "AIProbability",
                "AILevel", "Confidence", "Score", "Price", "RR",
                "OperationalStatus", "FinalActionability", "DataSource",
            ]]),
            use_container_width=True,
            hide_index=True,
            column_config=_market_column_config(),
        )

    section_header("Market Scan", "Search, filter, then inspect a symbol")
    search_col, signal_col, regime_col, rating_col = st.columns([2, 1, 1, 1])
    search = search_col.text_input(
        "Search ticker", placeholder="e.g. COMI.CA", label_visibility="collapsed"
    ).strip().upper()
    signal_filter = signal_col.selectbox(
        "Signal", ["ALL", "BUY", "WATCH", "AVOID"], label_visibility="collapsed"
    )
    regime_options = ["ALL"] + sorted(df["Regime"].dropna().astype(str).unique().tolist())
    regime_filter = regime_col.selectbox(
        "Regime", regime_options, label_visibility="collapsed"
    )
    rating_options = ["ALL"] + sorted(df["Rating"].dropna().astype(str).unique().tolist())
    rating_filter = rating_col.selectbox(
        "Rating", rating_options, label_visibility="collapsed"
    )

    rr_col, score_col, result_col = st.columns([1, 1, 2])
    min_rr = rr_col.number_input("Minimum RR", min_value=0.0, value=0.0, step=0.25)
    min_score = score_col.number_input(
        "Minimum Score", min_value=0, max_value=100, value=0, step=5
    )

    filtered = df.copy()
    if search:
        filtered = filtered[filtered["Ticker"].astype(str).str.upper().str.contains(search)]
    if signal_filter != "ALL":
        filtered = filtered[filtered["Signal"] == signal_filter]
    if regime_filter != "ALL":
        filtered = filtered[filtered["Regime"] == regime_filter]
    if rating_filter != "ALL":
        filtered = filtered[filtered["Rating"] == rating_filter]
    filtered = filtered[
        (pd.to_numeric(filtered["RR"], errors="coerce").fillna(0) >= min_rr)
        & (pd.to_numeric(filtered["Score"], errors="coerce").fillna(0) >= min_score)
    ]
    result_col.caption(f"Showing {len(filtered)} of {len(df)} symbols")

    if filtered.empty:
        empty_state("No matching symbols", "Adjust the search or filter values.", icon="⌕")
        return

    display = filtered[[
        "Rank", "Rating", "Ticker", "Regime", "StrategySignal", "Stars",
        "AIProbability", "AILevel", "Confidence", "Score", "Price", "RR",
        "ClassicDecision", "ClassicRR", "BreakoutDecision", "BreakoutRR",
        "BreakoutScore", "BreakoutEdgeScore", "HigherQualityStrategy",
        "MarketRegime", "PreferredStrategy", "SelectorConfidence",
        "StrategyEdgeScore", "FinalRecommendation",
        "OperationalStatus", "FinalActionability", "DataSource",
        "DataTimestamp", "DataAgeSeconds", "OperationalReason", "Reasons",
    ]].copy()
    display["Reasons"] = display["Reasons"].astype(str).apply(
        lambda text: text if len(text) <= 90 else text[:87] + "…"
    )
    st.dataframe(
        _format_ai_probability(display),
        use_container_width=True,
        hide_index=True,
        height=min(650, 82 + len(display) * 35),
        column_config=_market_column_config(),
    )



def show_stock_details_page():
    """Dedicated Swing/Daily details page using the latest in-session scan."""

    page_header(
        "Swing / Daily Stock Details",
        "Frozen decision trace, completed daily indicators, and live quote overlay",
        icon="🔎",
        badge="SWING · DAILY",
    )
    results = st.session_state.get("results")
    if not results:
        empty_state(
            "No market scan available",
            "Run the Swing / Daily Dashboard scan first.",
            icon="○",
        )
        return
    symbols = [row.get("Ticker") for row in results if row.get("Ticker")]
    selected = st.selectbox("Select a symbol", symbols)
    stock = next((row for row in results if row.get("Ticker") == selected), None)
    if stock is not None:
        show_stock_details(stock)


def _market_column_config():
    return {
        "Rank": st.column_config.NumberColumn("#", width="small"),
        "Ticker": st.column_config.TextColumn("Ticker", width="small"),
        "Signal": st.column_config.TextColumn("Signal", width="small"),
        "StrategySignal": st.column_config.TextColumn("Strategy signal", width="small"),
        "Regime": st.column_config.TextColumn("Regime", width="small"),
        "AIProbability": st.column_config.TextColumn("AI Advisory", width="medium"),
        "Confidence": st.column_config.ProgressColumn(
            "Confidence", min_value=0, max_value=100, format="%d%%"
        ),
        "Score": st.column_config.ProgressColumn(
            "Score", min_value=0, max_value=100, format="%d"
        ),
        "RR": st.column_config.NumberColumn("R/R", format="%.2f"),
        "ClassicDecision": st.column_config.TextColumn("Classic", width="small"),
        "ClassicRR": st.column_config.NumberColumn("Classic R/R", format="%.2f"),
        "BreakoutDecision": st.column_config.TextColumn("Breakout", width="small"),
        "BreakoutRR": st.column_config.NumberColumn("Breakout R/R", format="%.2f"),
        "BreakoutScore": st.column_config.ProgressColumn(
            "Breakout score", min_value=0, max_value=100, format="%d"
        ),
        "BreakoutConfidence": st.column_config.ProgressColumn(
            "Breakout confidence", min_value=0, max_value=100, format="%d%%"
        ),
        "BreakoutEdgeScore": st.column_config.NumberColumn(
            "Edge score", format="%.2f"
        ),
        "HigherQualityStrategy": st.column_config.TextColumn(
            "Higher quality", width="medium"
        ),
        "MarketRegime": st.column_config.TextColumn("Adaptive regime", width="medium"),
        "PreferredStrategy": st.column_config.TextColumn("Preferred strategy", width="medium"),
        "SelectorConfidence": st.column_config.ProgressColumn(
            "Selector confidence", min_value=0, max_value=100, format="%.1f%%"
        ),
        "StrategyEdgeScore": st.column_config.NumberColumn(
            "Strategy edge", format="%.2f"
        ),
        "ClassicSuccessProbability": st.column_config.NumberColumn(
            "Classic success", format="%.2f%%"
        ),
        "BreakoutSuccessProbability": st.column_config.NumberColumn(
            "Breakout success", format="%.2f%%"
        ),
        "FinalRecommendation": st.column_config.TextColumn(
            "Adaptive recommendation", width="medium"
        ),
        "WhyPreferred": st.column_config.TextColumn("Why preferred", width="large"),
        "WhyNotOther": st.column_config.TextColumn("Why not other", width="large"),
        "SelectorReason": st.column_config.TextColumn("Selector reason", width="large"),
        "Price": st.column_config.NumberColumn("Price", format="%.2f"),
        "Reasons": st.column_config.TextColumn("Key reasons", width="large"),
        "OperationalStatus": st.column_config.TextColumn("Operational status", width="medium"),
        "FinalActionability": st.column_config.TextColumn("Final actionability", width="medium"),
        "DataSource": st.column_config.TextColumn("Source", width="small"),
        "DataTimestamp": st.column_config.TextColumn("Data timestamp", width="medium"),
        "DataAgeSeconds": st.column_config.NumberColumn("Age (sec)", format="%.0f"),
        "OperationalReason": st.column_config.TextColumn("Data reason", width="large"),
    }


def _format_ai_probability(frame):
    """Make intentional non-evaluation explicit without changing raw data."""
    formatted = frame.copy()
    if "AIProbability" in formatted:
        formatted["AIProbability"] = formatted["AIProbability"].apply(
            lambda value: (
                "Not evaluated" if pd.isna(value) else f"{float(value):.1f}%"
            )
        )
    return formatted


def _archive_warning(results) -> str:
    """Return a concise archival warning for the finished scan, or ``""``.

    Reads only the typed finalization fields the run recorded. A completed scan is
    never hidden or discarded because its dataset could not be published, and a
    traceback is never shown here — it stays in the log.
    """
    import json
    from pathlib import Path

    if not results:
        return ""
    run_id = str((results[0] or {}).get("RunID") or "").strip()
    if not run_id:
        return ""
    metadata_path = Path("reports") / run_id / "run_metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if metadata.get("dataset_finalization_ok", True):
        return ""
    status = metadata.get("dataset_finalization") or "UNKNOWN"
    detail = str(metadata.get("dataset_finalization_detail") or "").strip()
    return (
        f"Scan results are complete and saved. The dataset archive was not "
        f"published ({status}). Nothing was lost — the staged data is preserved "
        f"under reports/{run_id} and can be published again without rescanning."
        + (f" Detail: {detail}" if detail else "")
    )


def _render_provider_status(summary):
    """Show one routing notice and keep diagnostics collapsed by default."""

    historical = str(
        summary.get("historical_provider") or "local_cache:yahoo"
    )
    live = str(summary.get("live_quote_provider") or "unavailable")
    completed = _display_timestamp(summary.get("latest_completed_candle"))
    quote_time = _display_timestamp(summary.get("live_quote_timestamp"))
    st.info(
        f"Historical analysis source: {historical}  ·  "
        f"Live quote overlay: {live.title()}  ·  "
        f"Latest completed candle: {completed}  ·  Current quote: {quote_time}"
    )
    with st.expander("Data Status", expanded=False):
        status = st.columns(3)
        status[0].metric("Collector", str(summary.get("collector_status") or "N/A"))
        status[1].metric("Database", str(summary.get("database_status") or "N/A"))
        status[2].metric("Quote freshness", str(summary.get("freshness") or "N/A"))
        st.caption(
            f"Coverage: {int(summary.get('symbols_received') or 0)} / "
            f"{int(summary.get('symbols_requested') or 0)} · "
            f"Market: {summary.get('session_phase') or 'N/A'} · "
            f"Fallback: {'YES' if summary.get('fallback_active') else 'NO'}"
        )
        reasons = summary.get("fallback_reasons") or []
        if reasons:
            st.warning("Fallback reason: " + "; ".join(map(str, reasons)))
        _render_bridge_disclosure()
        freshness = str(summary.get("freshness") or "").upper()
        if "STALE" in freshness:
            st.warning(
                "Rubix quote overlay is stale. Historical Swing analysis remains "
                "available, but no result should be treated as a live quote."
            )
    _render_tradingview_research_panel()


def _render_tradingview_research_panel():
    """TradingView research provider status (display only, disabled by default).

    Never replaces the canonical Classic, Breakout, or Adaptive outputs -- it
    only discloses whether a compliant TradingView data route has been
    configured and, if so, what the (separately-run) reconciliation found.
    """

    try:
        from providers.tradingview_dashboard_factory import tradingview_research_disclosure

        info = tradingview_research_disclosure()
    except Exception:
        return
    with st.expander("TradingView Research (Experimental, Disabled)", expanded=False):
        cols = st.columns(3)
        cols[0].metric("Method", info["method"])
        cols[1].metric("Production enabled", info["production_enabled"])
        cols[2].metric("Current session excluded", info["current_session_excluded"])
        st.caption(
            f"Access status: {info['access_status']}  ·  "
            f"Symbol mapping: {info['symbol_mapping_reference']}"
        )
        st.caption(
            f"Latest completed TradingView candle: {info['latest_completed_tradingview_candle']}  ·  "
            f"Latest Yahoo candle: {info['latest_yahoo_candle']}  ·  "
            f"Freshness advantage: {info['freshness_advantage']}"
        )
        st.caption(
            f"Reconciliation status: {info['reconciliation_status']}  ·  "
            f"Shadow decision comparison: {info['shadow_decision_status']}"
        )
        if info["failure_or_limitation_reason"] not in (None, "n/a"):
            st.info(f"Limitation: {info['failure_or_limitation_reason']}")


def _render_bridge_disclosure():
    """Disclose the Rubix Completed-Daily Bridge state (display only)."""

    try:
        from providers.rubix_bridge_factory import bridge_disclosure

        info = bridge_disclosure()
    except Exception:
        return
    st.caption(
        f"Rubix Completed-Daily Bridge: **{info['mode_label']}** · "
        f"Historical warm-up source: {info['historical_warmup_source']} · "
        f"Latest completed candle source: {info['latest_completed_candle_source']} · "
        f"Rubix sessions appended (symbols): {info['symbols_with_rubix_sessions']} · "
        f"Current partial session in indicators: "
        f"{info['current_session_included_in_indicators']}"
    )


def _display_timestamp(value):
    if not value:
        return "Not available"
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(value)
