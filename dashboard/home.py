"""Primary live-market dashboard; presentation never changes decisions."""

import logging
import pandas as pd
import streamlit as st

from core import scan_job_manager as job_manager
from core.daily_data_guard import INSUFFICIENT_CURRENT_COVERAGE
from core.data_provider import summarize_frames
from core.symbols import SYMBOL_SOURCE
from dashboard.formatting import (
    NAME_COLUMN,
    company_name,
    display_ticker,
    symbol_option_label,
    with_company_name_column,
)
from dashboard.scan_status_panel import coverage_view, scan_status_view
from decision_support.service import DecisionSupportService
from dashboard.stock_details import show_stock_details
from dashboard.ui import (COLOURS, EMA20_MARKER_HELP, context_strip,
                          ema20_marker_html, ema20_marker_text, empty_state,
                          notice, opportunity_card, opportunity_grid,
                          page_header, section_header, status_bar)

#: The compact table's marker column. Shown beside every signal; decides nothing.
EMA20_COLUMN = "بُعد EMA20"


def _ema20_distance(row):
    """The signal close's distance from EMA20 in percent, or None.

    Read from the features the scanner attached to the row (the last bar of
    the indicator frame), falling back to that frame itself. Never computed
    here: this is the same `EMA20_DIST` the research measured and the forward
    tester stores, so the marker, the study and the live check agree.
    """
    for source in (row.get("AIFeatures"), row.get("Data")):
        try:
            if source is None:
                continue
            if isinstance(source, pd.DataFrame):
                if "EMA20_DIST" not in source.columns or source.empty:
                    continue
                value = source["EMA20_DIST"].iloc[-1]
            else:
                value = source.get("EMA20_DIST")
            value = float(value)
        except (TypeError, ValueError, AttributeError, IndexError):
            continue
        if value == value:
            return value
    return None


logger = logging.getLogger(__name__)

#: Membership comes from the authoritative EODHD universe, never a local list.
SCAN_SOURCE = SYMBOL_SOURCE
#: Poll interval for the progress fragment. Fast enough to feel live, slow enough that
#: the page is not re-running constantly while a scan works.
SCAN_POLL_SECONDS = 0.75

#: Idempotency keys for the terminal handoff. A finished scan must release the page
#: exactly once and publish its result exactly once, however many times the fragment
#: ticks, the page reruns, or another observer attaches.
TERMINAL_RERUN_KEY = "scan_terminal_rerun_scan_id"
TERMINAL_CONSUMED_KEY = "terminal_result_consumed"

#: The workspace this page owns, resolved ONCE and kept. Recomputing it on every
#: render is what lost a running scan: the old key resolved a relative universe
#: path against the process working directory and degraded to "unknown" on any
#: configuration read failure, so one render could look up a different key than
#: the one the running job was registered under. The page then showed no
#: progress, re-enabled the button, and a second click started a duplicate
#: concurrent scan — observed as RUN_20260803_223656 and RUN_20260803_223829.
WORKSPACE_KEY = "daily_scan_workspace_key"
WORKSPACE_FINGERPRINT_KEY = "daily_scan_workspace_fingerprint"
ACTIVE_JOB_KEY = "daily_scan_active_job_id"
LAST_ADOPTED_JOB_KEY = "daily_scan_last_adopted_job_id"
LAST_RESULT_KEY = "daily_scan_last_result"
#: Bounded diagnostic: how many polls saw a terminal job with no result yet.
PENDING_RESULT_POLLS_KEY = "daily_scan_pending_result_polls"
#: Beyond this many consecutive polls, terminal-without-result stops being a race
#: and is surfaced to the operator instead of spun on silently.
MAX_PENDING_RESULT_POLLS = 40

#: Headline per terminal state. A run with gaps is never announced as a clean success.
TERMINAL_HEADLINES = {
    job_manager.COMPLETED: ("success", "Scan complete"),
    job_manager.COMPLETED_WITH_GAPS: ("warning", "Scan complete with coverage gaps"),
    job_manager.CANCELLED: ("warning", "Scan cancelled — partial diagnostics only"),
    job_manager.FAILED: ("error", "Scan failed"),
}


#: Below this share of rows agreeing on one session date, a single headline date
#: would misdescribe the table underneath it.
DOMINANT_SESSION_SHARE = 0.98


def observed_sessions(results):
    """The session date each displayed row actually carries.

    Read straight from the typed provenance fields the scanner already recorded
    on each row — nothing is recomputed and no provider is consulted.
    """
    sessions = [row.get("LastCompletedSession") or row.get("CompletedSessionTimestamp")
                for row in results or ()]
    return [str(value)[:10] for value in sessions if value]


def session_coverage(results):
    """How the displayed rows are distributed across session dates.

    The banner used to headline ``max(sessions)``, which on 2026-08-04 announced
    "Latest completed candle 2026-08-03" while 184 of 194 rows carried
    2026-07-30 prices — the newest single row describing the whole table. The
    verified truth was that EODHD had published 2026-08-03 for only 6 EGX
    symbols; every row's own date and OHLCV agreed, so nothing was relabelled.
    What was wrong was the aggregate.
    """
    sessions = observed_sessions(results)
    if not sessions:
        return {"dominant": "", "newest": "", "oldest": "", "total": 0,
                "dominant_rows": 0, "distribution": {}, "mixed": False}
    counts = {}
    for value in sessions:
        counts[value] = counts.get(value, 0) + 1
    dominant, dominant_rows = max(counts.items(), key=lambda item: (item[1], item[0]))
    return {
        "dominant": dominant,
        "newest": max(counts),
        "oldest": min(counts),
        "total": len(sessions),
        "dominant_rows": dominant_rows,
        "distribution": dict(sorted(counts.items())),
        "mixed": (dominant_rows / len(sessions)) < DOMINANT_SESSION_SHARE,
    }


def _observed_metadata(results):
    """What the finished scan actually saw, for the final banner."""
    coverage = session_coverage(results)
    statuses = {str(row.get("LivePriceStatus") or "").upper()
                for row in results or ()} - {""}
    return {
        # The date the displayed prices actually carry, not the newest outlier.
        "latest_completed_candle": coverage["dominant"],
        "session_coverage": coverage,
        # No row carries a live quote since the feed was retired, so this can
        # only be "" now. It stays because a scan archived before that date
        # still has rows that say STALE, and reading one back must report what
        # it recorded rather than today's silence.
        "live_quote_freshness": "STALE" if "STALE" in statuses else "",
    }


def price_source_reading(results):
    """The strip's price-source cell: the completed close, and whose record it is.

    ``(value, tone, sub)``. There is no live source to name. The Rubix feed was
    retired on 2026-09-10, and the question this cell used to answer -- is the
    live quote fresh or stale? -- stopped having a subject that evening. It went
    from green "Rubix" to amber "Rubix · قديم", and both were wrong for the same
    reason: every price on the page is a completed session's close.

    So it says that, and names the history record the rows actually came from
    (``DataSource``, e.g. EODHD + Mubasher). No row carrying a source is "—",
    never a guess.
    """
    from core.live_feed import PRICE_SOURCE_AR, history_source_label

    source = history_source_label(row.get("DataSource") for row in results or ())
    return PRICE_SOURCE_AR, "gray", ("" if source == "—" else source)


def _workspace_fingerprint(workspace):
    """What must change before the stored key may be replaced.

    Only genuine workspace configuration — never widget state, never a path
    spelling, never a clock value.
    """
    return (workspace.purpose, workspace.source_identity, workspace.provider_mode)


def resolve_page_workspace():
    """The workspace this page owns, resolved once and reused across reruns.

    Returns ``(workspace, error)``. The key is recomputed only when the actual
    configuration changes; otherwise the stored key is returned unchanged so a
    rerun can never look up a different workspace than the running job's.
    """
    try:
        workspace = job_manager.resolve_workspace("dashboard", SCAN_SOURCE)
    except job_manager.WorkspaceConfigurationError as error:
        # Explicit and visible. Never a placeholder key: a guessed workspace is
        # how one page loses another's job.
        return None, str(error)

    fingerprint = _workspace_fingerprint(workspace)
    if st.session_state.get(WORKSPACE_FINGERPRINT_KEY) != fingerprint:
        st.session_state[WORKSPACE_FINGERPRINT_KEY] = fingerprint
        st.session_state[WORKSPACE_KEY] = workspace.key
    return workspace, None


def discover_job(workspace_key):
    """Reattach to whatever the registry still owns for this workspace.

    The registry — not session state — is the source of truth. A browser
    refresh, a script rerun or a navigation away and back all land here, and all
    of them must find the same job rather than offer to start another.
    """
    job = job_manager.REGISTRY.get(workspace_key)
    if job is None:
        return None
    st.session_state[ACTIVE_JOB_KEY] = job.scan_id if job.is_active else None
    return job


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


ARCHIVE_FAILURE_HEADLINE = "SCAN COMPLETED BUT ARCHIVE PUBLICATION FAILED"

COMPANY_LOOKUP_WARNING = (
    "Company names are unavailable for this table; symbols are shown as-is."
)


def _with_company_names(frame, symbol_column):
    """Enrich for display, but never let a name lookup lose the evidence.

    The exclusion table is the record of which symbols were withheld and why.
    A universe-mapping failure must degrade to bare symbols with a warning,
    not remove rows and not take the page down.

    Programming errors are deliberately NOT caught: a wrong argument name or a
    missing attribute must keep failing loudly, because that is exactly the
    defect this function was added for.
    """

    try:
        return with_company_name_column(frame, symbol_column)
    except (TypeError, AttributeError):
        raise
    except Exception:                       # noqa: BLE001 - data/mapping only
        logger.warning("company-name enrichment unavailable", exc_info=True)
        st.warning(COMPANY_LOOKUP_WARNING)
        return frame


def _render_failed_archive_provenance(job):
    """Show what a refused publication actually saw. Returns True if rendered.

    A scan that analysed 241 symbols and then failed its archive invariant is
    not "no market scan yet". Falling back to the empty state discarded the
    one screen that could explain the refusal, and left the date banner
    claiming there was no dated candle while the audit was full of them.

    Nothing here adopts the archive: it was correctly not published, and the
    counts below are evidence, not decisions.
    """

    if job is None or not getattr(job, "sanitized_error", ""):
        return False
    error = str(job.sanitized_error)
    if "ArchiveInvariantError" not in error:
        return False

    st.error(f"### {ARCHIVE_FAILURE_HEADLINE}")
    st.caption(
        "The scan ran to completion. Its archive failed cross-file validation "
        "and was therefore not published — no partial archive was adopted."
    )
    st.markdown("**Invariant failure**")
    st.code(error, language=None)

    snapshot = job.progress()
    row = st.columns(4)
    row[0].metric("Universe completed", f"{snapshot.completed} / {snapshot.total}")
    row[1].metric("Successful", snapshot.success)
    row[2].metric("Skipped", snapshot.skipped)
    row[3].metric("Failed", snapshot.failed)

    expected = _expected_completed_session_label()
    _, provenance = (error.split(" | ", 1) + [""])[:2]
    status_bar([
        ("Expected completed session", expected or "unknown", "blue"),
        ("Archive status", "FAILED VALIDATION", "red"),
        ("Decisions published", "none", "amber"),
    ])
    if provenance:
        st.caption(f"Observed provenance — {provenance}")
    if snapshot.status_breakdown:
        st.caption("Typed outcome breakdown")
        st.markdown(" · ".join(
            f"`{status}` **{count}**"
            for status, count in sorted(snapshot.status_breakdown.items(),
                                        key=lambda item: -item[1])))
    return True


def _expected_completed_session_label():
    """The authoritative completed EGX session, for the failure banner."""

    try:
        from core.egx_calendar import effective_holidays
        from core.egx_session import authoritative_completed_session

        value = authoritative_completed_session(holidays=effective_holidays())
        return value.isoformat() if value else ""
    except Exception:
        return ""


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
    # The third line used to report the Rubix overlay -- Fresh, Stale, Partially
    # Available -- five days after that feed was retired. There is no live
    # overlay to grade, so the line says so instead of grading a dead one.
    from core.live_feed import LIVE_QUOTES_STATUS_EN

    status_bar([
        ("Historical analysis source", view["historical_source"], "blue"),
        ("Latest completed candle", view["latest_completed_candle"], "gray"),
        ("Live quotes", LIVE_QUOTES_STATUS_EN, "gray"),
    ])
    return coverage


def _render_scan_job(job):
    """Render the background job, polling only while it is still active."""

    def _body():
        snapshot = job.progress()
        state, total = snapshot.state, max(1, snapshot.total)
        st.markdown(f"**{snapshot.stage}** · scan `{snapshot.scan_id}` · `{state}`")
        # Job identity and start time, so an operator can tell at a glance that a
        # rerun reattached to the SAME scan rather than starting another.
        st.caption(
            f"job `{snapshot.scan_id}` · started {job.started_at or job.created_at or '—'}"
            f" · {snapshot.completed}/{snapshot.total} processed"
            f" · {snapshot.success} successful · {snapshot.failed} failed"
        )
        st.progress(min(1.0, snapshot.completed / total),
                    text=f"{snapshot.completed} / {snapshot.total}")

        row = st.columns(4)
        row[0].metric("Elapsed", _format_duration(snapshot.elapsed_seconds))
        row[1].metric("Remaining (est.)",
                      _format_duration(snapshot.estimated_remaining_seconds))
        from dashboard.formatting import display_ticker

        row[2].metric("Current symbol", display_ticker(snapshot.current_symbol) or "—")
        row[3].metric("Last symbol",
                      f"{display_ticker(snapshot.last_completed_symbol) or '—'}"
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
        # This counted Rubix overlays read from a database nothing has written
        # since 2026-09-10 -- a count of five-day-old quotes shown beside the
        # scan's real progress as if it were part of it.
        from core.live_feed import LIVE_QUOTES_STATUS_EN, RETIREMENT_NOTE

        row[2].metric("Live quotes", LIVE_QUOTES_STATUS_EN.split(" · ", 1)[0],
                      help=RETIREMENT_NOTE)
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
        if job.is_active or job.result_pending:
            # Still working, or terminal but the result has not been published
            # yet. Releasing the page now would hand the results renderer an
            # empty job; keep polling instead.
            return
        if st.session_state.get(TERMINAL_RERUN_KEY) != job.scan_id:
            st.session_state[TERMINAL_RERUN_KEY] = job.scan_id
            st.rerun(scope="app")

    # Mounted whenever the job still owes the page something: while it runs, and
    # through the window where it is terminal but has not published its result.
    # A page that stopped polling at the first terminal reading is exactly how a
    # finished scan stayed invisible until the operator clicked again.
    if job.is_active or job.result_pending:
        st.fragment(_polling_body, run_every=SCAN_POLL_SECONDS)()
    else:
        # Terminal: progress stays available but must never stand in for the results.
        with st.expander(f"Scan progress · {job.progress().scan_id}", expanded=False):
            _body()


def _adopt_finished_job(job):
    """Publish a terminal job's result into session state exactly once.

    Ordering is the fix. The previous version marked the job consumed and *then*
    read ``final_result``; a single observer arriving between the worker's
    ``final_result = result`` and its terminal ``publish`` therefore burned the
    idempotency key while the result was still None, and the finished scan could
    never be adopted afterwards. The rows were on disk and invisible in the app.

    Now nothing is claimed until there is something real to claim:

      1. the job must be terminal,
      2. ``final_result`` must actually be present,
      3. the claim is atomic in the registry (one observer wins),
      4. only then is the result stored.

    A terminal job with no result yet is left completely untouched, so the next
    poll retries. Nothing here re-runs analysis, contacts a provider, archives or
    finalizes — the worker already did all of that.
    """
    if job.is_active:
        return False

    if job.result_pending:
        # The worker is terminal but has not published its result yet. Count the
        # wait so an abnormal stall becomes visible rather than silent, and never
        # consume the job — consuming here is precisely what lost a finished scan.
        polls = int(st.session_state.get(PENDING_RESULT_POLLS_KEY, 0)) + 1
        st.session_state[PENDING_RESULT_POLLS_KEY] = polls
        if polls > MAX_PENDING_RESULT_POLLS:
            st.session_state["scan_pending_result_warning"] = (
                f"scan `{job.scan_id}` reported {job.progress().state} but published "
                f"no result after {polls} checks"
            )
        return False

    st.session_state[PENDING_RESULT_POLLS_KEY] = 0
    result = job.final_result

    # The claim is the registry's, not the page's: two browser sessions observing
    # the same finished job must not both run the decision-support snapshot.
    if not job.claim_result(f"dashboard:{job.scan_id}"):
        if st.session_state.get(LAST_ADOPTED_JOB_KEY) == job.scan_id:
            return False
        # Another observer claimed it. This session still renders the same rows;
        # it simply does not repeat the one-time downstream work.
        st.session_state[TERMINAL_CONSUMED_KEY] = job.scan_id
        st.session_state[LAST_ADOPTED_JOB_KEY] = job.scan_id
        if result is not None:
            st.session_state.results = result
            st.session_state[LAST_RESULT_KEY] = result
        st.session_state.live_scan_completed = bool(result) and not job.cancelled
        return False

    st.session_state[TERMINAL_CONSUMED_KEY] = job.scan_id
    st.session_state[LAST_ADOPTED_JOB_KEY] = job.scan_id
    st.session_state.adopted_scan_id = job.scan_id
    if result is None:
        # A FAILED run produced nothing. It is settled honestly — the operator
        # may retry — but no empty result is published as if it were a scan.
        st.session_state.live_scan_completed = False
        return True
    # A cancelled run keeps its partial rows as diagnostics but is never
    # presented as a recorded session.
    st.session_state.results = result
    st.session_state[LAST_RESULT_KEY] = result
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
    return True


SWING_PRIMARY_COLUMNS = (
    "السهم",
    NAME_COLUMN,
    "القرار",
    "حالة السوق",
    "السعر",
    # A reward/risk ratio without the levels it was derived from is not
    # actionable: the reader can see 2.0 and still not know where to enter,
    # where the idea is wrong, or where to take profit. The scan already
    # computes all four; the table simply was not showing them.
    "نطاق الشراء",
    "وقف الخسارة",
    "الهدف 1",
    "الهدف 2",
    "العائد إلى المخاطرة",
    "الثقة",
    # Beside the decision, never part of it: which side of EMA20 the signal
    # closed on. Measured better at or below in both eras; not a rule.
    EMA20_COLUMN,
    "الحالة التشغيلية",
)


def _entry_band(low, high):
    """``12.40 – 12.75``, or an em dash when the scan produced no band.

    Never falls back to a single price: an entry the strategy expressed as a
    range must not be shown as a point.
    """

    try:
        low_value, high_value = float(low), float(high)
    except (TypeError, ValueError):
        return "—"
    if low_value != low_value or high_value != high_value:      # NaN
        return "—"
    if low_value <= 0 or high_value <= 0:
        return "—"
    if abs(high_value - low_value) < 1e-9:
        return f"{low_value:,.2f}"
    return f"{low_value:,.2f} – {high_value:,.2f}"


def _level(value, digits=3):
    """A price level, or ``—`` when the scan computed none.

    Not ``0.000``. The engine leaves every level at zero for a name it refused:
    98 of the 100 AVOID rows in a 2026-09-10 scan carried a stop loss of 0, a
    target of 0 and a reward/risk of 0, and the table printed all of them as
    prices. A stop of zero is not a stop -- it is the absence of one, drawn as
    the most dangerous number on the row.

    These columns become text, so the compact table no longer sorts on them
    numerically. The full numeric frame is one disclosure away in Advanced
    Research, and a column of false zeroes sorts to the top of exactly the
    ranking a reader would use it for.
    """

    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number != number or number <= 0:                          # NaN or absent
        return "—"
    return f"{number:,.{digits}f}"


#: Columns the engine leaves at zero for a name it refused, rather than at a
#: value it measured. Rendered through ``_level``.
_ABSENT_AS_ZERO = {
    "وقف الخسارة": 3, "الهدف 1": 3, "الهدف 2": 3,
    "العائد إلى المخاطرة": 2, "الثقة": 0,
}


#: Decision -> the tone its card and badge wear.
_SIGNAL_TONE = {"BUY": "green", "WATCH": "blue", "AVOID": "red"}


def _render_opportunities(frame):
    """The day's actionable names as cards, three to a row.

    This was the same twelve-column table as the one below it, showing its
    first ten rows -- so the page's headline section and its reference table
    were the same object at two lengths. A candidate worth acting on is read
    one at a time: what it is, what was decided, the four figures the trade is
    judged on, and where today's close sits between the stop and the second
    target.
    """
    cards = []
    for _, row in frame.iterrows():
        signal = str(row.get("Signal", ""))
        cards.append(opportunity_card(
            ticker=display_ticker(row.get("Ticker", "")),
            name=company_name(row.get("Ticker", "")),
            sector=str(row.get("Sector") or "").strip(),
            regime=str(row.get("Regime") or "").strip(),
            signal=signal,
            tone=_SIGNAL_TONE.get(signal, "gray"),
            figures=(
                ("السعر", _level(row.get("Price"), 2), None),
                ("الوقف", _level(row.get("StopLoss"), 2), "stop"),
                ("هدف 1", _level(row.get("Target1"), 2), None),
                # "العائد/المخاطرة" does not fit a quarter-width cell and
                # truncated mid-word. R:R is what the ratio is called on a
                # trading screen in any language.
                ("R:R", _level(row.get("RR"), 2), "rr"),
            ),
            stop=row.get("StopLoss"), buy_low=row.get("BuyLow"),
            buy_high=row.get("BuyHigh"), price=row.get("Price"),
            target2=row.get("Target2"),
            marker_html=ema20_marker_html(_ema20_distance(row)),
        ))
    st.markdown(opportunity_grid(cards), unsafe_allow_html=True)


def _swing_primary_frame(frame):
    """Compact trader view without mutating scan results.

    Ticker and company name are separate columns so search, filtering and
    export keep working on the raw ticker.
    """

    source = frame.copy()
    mapping = {
        "Ticker": "السهم",
        "Signal": "القرار",
        "Regime": "حالة السوق",
        "Price": "السعر",
        "StopLoss": "وقف الخسارة",
        "Target1": "الهدف 1",
        "Target2": "الهدف 2",
        "RR": "العائد إلى المخاطرة",
        "Confidence": "الثقة",
        "OperationalStatus": "الحالة التشغيلية",
    }
    available = [column for column in mapping if column in source.columns]
    view = source[available].rename(columns=mapping)
    if "السهم" in view.columns:
        view[NAME_COLUMN] = [company_name(value) for value in view["السهم"]]
    # The scan expresses the entry as a band rather than one price; collapsing
    # it to a single number would invent a precision the strategy never had.
    if {"BuyLow", "BuyHigh"} <= set(source.columns):
        view["نطاق الشراء"] = [
            _entry_band(low, high)
            for low, high in zip(source["BuyLow"], source["BuyHigh"])
        ]
    # An absent level is an em dash, never a zero. `نطاق الشراء` above already
    # worked this way; the five columns beside it did not, so one cell on the
    # row said "no band" while the next four quoted prices of 0.000.
    for column, digits in _ABSENT_AS_ZERO.items():
        if column in view.columns:
            view[column] = [_level(value, digits) for value in view[column]]
    # Read row by row from the source, never from `view`: the features the
    # scanner attached are not among the renamed columns. An unmeasured
    # distance is an em dash, never a side.
    view[EMA20_COLUMN] = [ema20_marker_text(_ema20_distance(row))
                          for _, row in source.iterrows()]
    return view.reindex(columns=SWING_PRIMARY_COLUMNS)


def _render_swing_advanced_research(
    df,
    *,
    buy,
    watch,
    avoid,
    failed_coverage,
):
    """Keep comparisons, charts, and developer detail behind one disclosure."""

    with st.expander("البحث المتقدم (Advanced Research)", expanded=False):
        st.caption(
            "تفاصيل بحثية للمراجعة؛ لا تغيّر القرارات أو ترتيب النتائج."
        )
        # Display-only enrichment: the raw Ticker column is left untouched.
        df = df.copy()
        if "Ticker" in df.columns:
            df[NAME_COLUMN] = [company_name(value) for value in df["Ticker"]]
        if failed_coverage:
            st.subheader("تفاصيل فجوات التغطية")
            st.dataframe(
                with_company_name_column(
                    pd.DataFrame(failed_coverage)[[
                        "symbol", "historical_row_count", "rejection_category",
                        "rejection_reason", "final_status",
                    ]], "symbol"),
                use_container_width=True,
                hide_index=True,
            )

        ai_values = pd.to_numeric(
            df["AIProbability"], errors="coerce"
        ).dropna()
        avg_ai = round(ai_values.mean(), 1) if not ai_values.empty else None
        actionable = int(
            pd.Series(df.get("Actionable", False))
            .fillna(False).astype(bool).sum()
        )
        non_actionable = int(
            df.get(
                "FinalActionability",
                pd.Series(index=df.index, dtype=str),
            )
            .fillna("").astype(str).str.contains("NON_ACTIONABLE").sum()
        )
        extra = st.columns(5)
        extra[0].metric("متوسط الثقة", f"{df['Confidence'].mean():.1f}%")
        extra[1].metric("متوسط الدرجة", f"{df['Score'].mean():.1f}")
        extra[2].metric(
            "متوسط تقييم AI",
            f"{avg_ai}%" if avg_ai is not None else "N/A",
        )
        extra[3].metric("فرص BUY اللحظية", actionable)
        extra[4].metric("غير قابل للتنفيذ", non_actionable)

        section_header("توزيع الإشارات وحالة السوق", "Full signal/regime charts")
        chart_col, regime_col = st.columns(2)
        with chart_col:
            st.bar_chart(
                pd.DataFrame(
                    {"Count": [buy, watch, avoid]},
                    index=["BUY", "WATCH", "AVOID"],
                ),
                color=COLOURS["accent"],
            )
        with regime_col:
            regimes = df["Regime"].fillna("Unknown").value_counts()
            st.bar_chart(regimes.rename("Count"), color=COLOURS["blue"])

        breakout_buy = int((df.get("BreakoutDecision") == "BUY").sum())
        breakout_watch = int((df.get("BreakoutDecision") == "WATCH").sum())
        breakout_avoid = int((df.get("BreakoutDecision") == "AVOID").sum())
        breakout_overlap = int(
            ((df["Signal"] == "BUY") & (df.get("BreakoutDecision") == "BUY"))
            .sum()
        )
        section_header(
            "مقارنة Classic و BREAKOUT_SWING",
            "Classic versus Breakout comparison",
        )
        classic_col, breakout_col, overlap_col = st.columns(3)
        classic_col.metric("Classic BUY / WATCH / AVOID", f"{buy} / {watch} / {avoid}")
        breakout_col.metric(
            "Breakout BUY / WATCH / AVOID",
            f"{breakout_buy} / {breakout_watch} / {breakout_avoid}",
        )
        overlap_col.metric("اتفاق BUY", breakout_overlap)
        comparison_columns = [
            "Ticker", NAME_COLUMN, "ClassicDecision", "ClassicRR", "BreakoutDecision",
            "BreakoutRR", "BreakoutScore", "BreakoutConfidence",
            "BreakoutEdgeScore", "HigherQualityStrategy",
        ]
        st.dataframe(
            df[[column for column in comparison_columns if column in df.columns]],
            use_container_width=True,
            hide_index=True,
            column_config=_market_column_config(),
        )
        show_scoring_basis()

        market_regime = str(
            df.get("MarketRegime", pd.Series(["UNKNOWN"])).iloc[0]
        )
        preferred_counts = df.get(
            "PreferredStrategy",
            pd.Series(index=df.index, dtype=str),
        ).fillna("NONE").value_counts()
        preferred_strategy = (
            str(preferred_counts.index[0])
            if not preferred_counts.empty
            else "NONE"
        )
        selector_confidence = pd.to_numeric(
            df.get(
                "SelectorConfidence",
                pd.Series(index=df.index, dtype=float),
            ),
            errors="coerce",
        ).fillna(0).mean()
        section_header(
            "الاختيار التكيفي",
            "Adaptive per-symbol decisions",
        )
        adaptive_cards = st.columns(3)
        adaptive_cards[0].metric("حالة السوق", market_regime)
        adaptive_cards[1].metric("الاستراتيجية المفضلة", preferred_strategy)
        adaptive_cards[2].metric(
            "ثقة الاختيار",
            f"{selector_confidence:.1f}%",
        )
        adaptive_columns = [
            "Ticker", NAME_COLUMN, "ClassicDecision", "ClassicRR", "BreakoutDecision",
            "BreakoutRR", "MarketRegime", "ClassicSuccessProbability",
            "BreakoutSuccessProbability", "SelectorConfidence",
            "PreferredStrategy", "StrategyEdgeScore", "FinalRecommendation",
            "WhyPreferred", "WhyNotOther", "SelectorReason",
        ]
        st.dataframe(
            df[[column for column in adaptive_columns if column in df.columns]],
            use_container_width=True,
            hide_index=True,
            column_config=_market_column_config(),
        )

        section_header("الجدول البحثي الكامل", "Developer metrics and attribution")
        # Typed daily-candle provenance, so a price is never shown without the
        # evidence for how current it is. A generic green "Fresh" is deliberately
        # impossible here: every label names its session. The six Rubix quote
        # columns went with the feed on 2026-09-10 -- they could only ever show
        # the last quotes from before it was retired, beside today's rows.
        freshness_columns = [
            "DailyCandleSession", "DailyFreshnessStatus",
            "DataSource", "DisplayPriceSource", "DecisionPriceSource",
        ]
        available_freshness = [c for c in freshness_columns if c in df.columns]
        if available_freshness:
            with st.expander("Daily-candle provenance"):
                st.dataframe(
                    df[["Ticker", *available_freshness]]
                    if "Ticker" in df.columns else df[available_freshness],
                    use_container_width=True, hide_index=True,
                )
                from core.live_feed import RETIREMENT_NOTE

                st.caption(RETIREMENT_NOTE)

        diagnostic_columns = [
            "Rank", "Rating", "Ticker", NAME_COLUMN, "Regime", "StrategySignal", "Stars",
            "AIProbability", "AILevel", "Confidence", "Score", "Price", "RR",
            "ClassicDecision", "ClassicRR", "BreakoutDecision", "BreakoutRR",
            "BreakoutScore", "BreakoutEdgeScore", "HigherQualityStrategy",
            "MarketRegime", "PreferredStrategy", "SelectorConfidence",
            "StrategyEdgeScore", "FinalRecommendation", "OperationalStatus",
            "FinalActionability", "DataSource", "DataTimestamp",
            "DataAgeSeconds", "OperationalReason", "Reasons",
        ]
        available = [
            column for column in diagnostic_columns if column in df.columns
        ]
        st.dataframe(
            _format_ai_probability(df[available].copy()),
            use_container_width=True,
            hide_index=True,
            column_config=_market_column_config(),
        )


def render_coverage_panel(coverage, results=None):
    """State the coverage plainly, and separate excluded symbols from decisions.

    A partial scan is never presented as a plain "Scan Complete": the operator
    needs to know that most of the market was not analysed before reading a
    ranking of what was.
    """

    if not coverage.complete:
        st.warning(coverage.partial_message())

    row = st.columns(6)
    row[0].metric("Expected session", coverage.expected_session or "—")
    row[1].metric("Universe", coverage.universe_total)
    row[2].metric("History loaded", coverage.history_loaded)
    row[3].metric("Current", coverage.current)
    row[4].metric("Stale", coverage.stale)
    row[5].metric("Current coverage", f"{coverage.current_percent:.1f}%")

    if coverage.distribution:
        st.caption("Observed candle sessions: " + " · ".join(
            f"`{date}` {count}"
            for date, count in sorted(coverage.distribution.items(), reverse=True)))

    if not coverage.market_wide_allowed:
        # Symbol-level results stay visible; only the market-wide claim stops.
        st.error(coverage.blocked_message())
        st.caption(
            f"Market regime: {INSUFFICIENT_CURRENT_COVERAGE} — the gate is the "
            f"data-quality setting `{coverage.threshold_source}` "
            f"({coverage.threshold_percent:.0f}%), not a strategy threshold."
        )

    excluded = [item for item in getattr(results, "freshness", []) or []
                if not item.eligible_for_current_analysis]
    if not excluded:
        return
    with st.expander(f"Excluded due to daily-data freshness ({len(excluded)})"):
        statuses = sorted({item.freshness_status.value for item in excluded})
        chosen = st.multiselect("Filter by status", statuses, default=statuses,
                                key="freshness_exclusion_filter")
        frame = pd.DataFrame([
            item.as_row() for item in excluded
            if item.freshness_status.value in chosen
        ])
        if frame.empty:
            st.caption("No excluded symbols match this filter.")
            return
        # The canonical parameter is ``symbol_column`` and every other caller
        # passes it positionally. This site invented ``ticker_column=``, so a
        # completed 241-symbol scan rendered its results and then crashed the
        # whole page on the exclusion table.
        frame = _with_company_names(frame, "Ticker")
        st.dataframe(frame, use_container_width=True, hide_index=True)
        st.caption(
            "These symbols were NOT analyzed as current opportunities and carry "
            "no BUY/WATCH/AVOID decision."
        )


def show_dashboard():
    page_header(
        "لوحة التداول اليومي",
        "قرارات متعددة الأيام مبنية على شموع يومية مكتملة",
        icon="📊",
        badge="SWING",
    )
    st.info(
        "هذه الصفحة للتداول اليومي والمتوسط فقط. متابعة السكالبنج موجودة في "
        "لوحة السكالبنج."
    )

    live_policy_version = "SWING_HISTORY_PLUS_RUBIX_QUOTE_V5_ADAPTIVE_SELECTOR"
    if st.session_state.get("live_policy_version") != live_policy_version:
        # Invalidate only old in-memory hard-filter results once.
        st.session_state.results = None
        st.session_state.live_scan_completed = False
        st.session_state.live_policy_version = live_policy_version
    if "results" not in st.session_state:
        st.session_state.results = None

    # One workspace identity for the whole session. Resolved from the repository
    # root and a validated provider setting, never from the process working
    # directory, so every rerun looks up the SAME job.
    workspace, workspace_error = resolve_page_workspace()
    if workspace_error:
        st.error(
            "لا يمكن تحديد مساحة عمل الفحص، ولن يبدأ أي فحص: " + workspace_error
        )
        st.caption(
            "A scan workspace that cannot be identified is never guessed: a "
            "placeholder key is what allowed two concurrent scans of the same "
            "universe on 2026-08-03."
        )
        return
    workspace_key = st.session_state[WORKSPACE_KEY]

    # The registry is the source of truth. A refresh, a rerun or a navigation
    # away and back all reattach here instead of offering to start another scan.
    job = discover_job(workspace_key)

    # The provider banner reads the configured operational route and the live job — it
    # never infers a Yahoo fallback from Rubix quote health.
    provider_placeholder = st.empty()
    with provider_placeholder.container():
        _render_scan_status(job.progress() if job is not None else None)

    # Read job state ONCE per render. Reading ``is_active`` separately for the
    # button, the panel and the early return let one render disagree with
    # itself: the page could return early on a stale True while the panel had
    # already taken the terminal branch, leaving a frozen page with no poller.
    active = job is not None and job.is_active
    awaiting_result = job is not None and job.result_pending

    pending_warning = st.session_state.pop("scan_pending_result_warning", None)
    if pending_warning:
        st.warning(pending_warning)

    scan_col, status_col = st.columns([3, 1])
    scan_completed = st.session_state.get("live_scan_completed", False)
    # A zero-result run is a recorded failed experiment, not a completed live
    # scan. Keep the evidence, but allow the operator to retry after repairing
    # the data route instead of leaving the button permanently disabled.
    if scan_completed and st.session_state.results == []:
        st.session_state.live_scan_completed = False
        scan_completed = False
    with scan_col:
        if st.button(
            "فحص السوق اليومي",
            type="primary",
            use_container_width=True,
            disabled=active or awaiting_result or scan_completed,
            help="One immutable market scan is recorded per application session.",
            key="run_market_scan",
        ):
            # Atomic in the registry, not merely disabled in the UI: a double
            # click, a rerun or a second tab attaches to the job that already
            # owns this workspace instead of starting a second scan.
            try:
                job, created = job_manager.start_scan_job(SCAN_SOURCE, "dashboard")
            except job_manager.WorkspaceConfigurationError as error:
                st.error(f"تعذّر بدء الفحص: {error}")
                return
            st.session_state[ACTIVE_JOB_KEY] = job.scan_id
            if not created:
                st.info("يوجد فحص سوق قيد التشغيل بالفعل؛ تم الالتحاق به.")
            st.rerun()
    with status_col:
        if active:
            # Stop Scan remains an explicit, idempotent operator control.
            if st.button("إيقاف الفحص", use_container_width=True,
                         key=f"stop_scan_{job.scan_id}"):
                job.request_cancel()          # idempotent
                st.rerun()
        else:
            st.button(
                "تم حفظ فحص الجلسة" if scan_completed else "جاهز للفحص",
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
        if active or job.result_pending:
            # The scan still owns the page. ``terminal_without_result`` keeps the
            # poller mounted through the brief window between the worker's
            # terminal state and its published result, so the handoff cannot be
            # missed by a render that arrived a moment early.
            return

    archive_warning = st.session_state.get("archive_warning")
    if archive_warning:
        st.warning(archive_warning)

    if st.session_state.results is None:
        if _render_failed_archive_provenance(job):
            return
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

    # --- Daily-data coverage -------------------------------------------------
    # The scan already excluded every symbol whose latest candle was not the
    # expected completed session, so `results` holds CURRENT symbols only and
    # no stale row can reach a decision table. What remains is to say so
    # honestly: how much of the universe that represents, and whether it is
    # enough to support a market-WIDE claim.
    coverage = getattr(results, "universe_coverage", None)
    if coverage is not None:
        render_coverage_panel(coverage, results)
        if not coverage.market_wide_allowed:
            st.session_state["market_regime_label"] = INSUFFICIENT_CURRENT_COVERAGE

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
    # A stable order for the table, and NOT a ranking by quality. Measured
    # within the population that reaches it -- the bars passing every other
    # gate -- the score's rank correlation with the outcome is +0.151 in
    # 2016-2022 and **+0.012 (p = 0.76)** in 2023-2026, and no reweighting of
    # its components recovers that: weights fitted on the first era rank the
    # second at +0.032, p = 0.43.
    #
    # The sort stays because a table needs an order and this one is at least
    # deterministic. What changed is that the page no longer implies the top
    # row is the best candidate. See SCORE_CANNOT_BE_REBUILT.md.
    df = df.sort_values(
        ["_AIProbabilitySort", "Confidence", "Score"], ascending=False
    ).reset_index(drop=True)

    buy = int((df["Signal"] == "BUY").sum())
    watch = int((df["Signal"] == "WATCH").sum())
    avoid = int((df["Signal"] == "AVOID").sum())

    run_id = results[0].get("RunID")
    latest_dates = [
        pd.Timestamp(row["Data"].index[-1])
        for row in results if row.get("Data") is not None and len(row["Data"])
    ]
    latest_date = max(latest_dates).date().isoformat() if latest_dates else "N/A"
    st.caption(
        f"آخر شمعة يومية مكتملة: {latest_date} · "
        f"مرجع الفحص: {run_id or 'غير متاح'}"
    )
    st.caption(
        "الترتيب ثابت وليس تقييمًا للجودة · **The order is stable, not a "
        "quality ranking.** Measured among the candidates that reach it, the "
        "score's rank correlation with the outcome is +0.15 in 2016–2022 and "
        "**+0.01 (p = 0.76)** in 2023–2026, and no reweighting of its "
        "components recovers that. Read the gates, not the position in the "
        "table."
    )

    coverage = list(getattr(results, "coverage", []) or [])
    failed_coverage = [
        row for row in coverage if not row.get("accepted_into_swing_scan")
    ]
    market_state = str(
        df.get("MarketRegime", pd.Series(["UNKNOWN"])).iloc[0]
    )

    # One row, not two. The decision counts and the context they were measured
    # in were separate bands of tiles stacked above the first result, and the
    # counts carried their state only in an emoji in the label -- 🟢 was the
    # entire difference between a buy and a refusal.
    total = len(df)
    attempted = total + len(failed_coverage)
    share = (lambda n: f"{n / total * 100:.0f}%" if total else "")
    # One line of seven readings rather than seven tiles. A tile is for a
    # figure you stop and read; these are read by sweeping across them, and
    # they were taking a third of the screen above the first result.
    st.markdown(
        context_strip([
            ("شراء BUY", str(buy), "green" if buy else "gray", share(buy)),
            ("متابعة WATCH", str(watch), "amber" if watch else "gray", share(watch)),
            # Roughly half of every scan lands here. Saying so is the difference
            # between a table the reader scans and one the reader filters.
            ("تجنب AVOID", str(avoid), "red" if avoid else "gray", share(avoid)),
            ("التغطية", f"{total}/{attempted}",
             "amber" if failed_coverage else "green",
             f"−{len(failed_coverage)}" if failed_coverage else ""),
            ("حالة السوق", market_state, "blue", ""),
            ("الجلسة", str(latest_date), "gray", ""),
            # Who quoted AND whether it is current. It was green "Rubix" on
            # five-day-old quotes after the feed was retired; see
            # price_source_reading.
            ("مصدر السعر", *price_source_reading(results)),
        ]),
        unsafe_allow_html=True,
    )

    if failed_coverage:
        # One line. It was a Streamlit warning box: three lines of chrome for
        # one fact, directly above the first result.
        st.markdown(
            notice("EXCLUSION · استبعاد",
                   f"تعذر تحليل {len(failed_coverage)} سهم لعدم اكتمال البيانات "
                   f"أو عدم تطابق الجلسة — عُزلت لمنع إشارة زائفة. "
                   f"التفاصيل في البحث المتقدم أسفل الصفحة."),
            unsafe_allow_html=True,
        )

    section_header("أهم الفرص القابلة للمتابعة", "Top actionable opportunities")
    top_buy = df[df["Signal"] == "BUY"].head(9)
    if top_buy.empty:
        empty_state(
            "لا توجد فرص شراء اليوم",
            "يمكن متابعة الأسهم الأخرى من الجدول المختصر أدناه.",
            icon="○",
        )
    else:
        _render_opportunities(top_buy)

    section_header("جدول السوق المختصر", "Compact market table")
    search_col, signal_col, result_col = st.columns([2, 1, 1])
    search = search_col.text_input(
        "ابحث عن سهم",
        placeholder="مثال: COMI",
        label_visibility="collapsed",
    ).strip().upper()
    signal_filter = signal_col.selectbox(
        "القرار",
        ["ALL", "BUY", "WATCH", "AVOID"],
        label_visibility="collapsed",
    )

    filtered = df.copy()
    if search:
        filtered = filtered[filtered["Ticker"].astype(str).str.upper().str.contains(search)]
    if signal_filter != "ALL":
        filtered = filtered[filtered["Signal"] == signal_filter]
    result_col.caption(f"عرض {len(filtered)} من {len(df)} سهم")

    if filtered.empty:
        empty_state(
            "لا توجد نتائج مطابقة",
            "غيّر البحث أو فلتر القرار.",
            icon="⌕",
        )
    else:
        st.dataframe(
            _swing_primary_frame(filtered),
            use_container_width=True,
            hide_index=True,
            height=min(650, 82 + len(filtered) * 42),
            column_config={
                EMA20_COLUMN: st.column_config.TextColumn(
                    EMA20_COLUMN, width="medium", help=EMA20_MARKER_HELP),
            },
        )

    _render_swing_advanced_research(
        df,
        buy=buy,
        watch=watch,
        avoid=avoid,
        failed_coverage=failed_coverage,
    )



def show_stock_details_page():
    """Dedicated Swing/Daily details page using the latest in-session scan."""

    page_header(
        "تفاصيل السهم",
        "القرار التاريخي والمؤشرات اليومية والسعر اللحظي",
        icon="🔎",
        badge="SWING · DAILY",
    )
    results = st.session_state.get("results")
    if not results:
        empty_state(
            "لا يوجد فحص سوق متاح",
            "شغّل فحص السوق اليومي أولاً.",
            icon="○",
        )
        return
    symbols = [row.get("Ticker") for row in results if row.get("Ticker")]
    selected = st.selectbox("اختر السهم", symbols,
                            format_func=symbol_option_label)
    stock = next((row for row in results if row.get("Ticker") == selected), None)
    if stock is not None:
        show_stock_details(stock)


def show_scoring_basis():
    """What the breakout score is made of, and where each number came from.

    The weights used to be hand-assigned -- 20 points for a resistance
    breakout, 15 for volume, 15 for consolidation -- which reads as
    quantitative while being nobody's measurement. A reader had no way to tell
    a number that was earned from one that was picked, so the honest fix is
    not a better-looking score but a visible basis for the one being shown.
    """

    import pandas as pd

    from strategy_breakout.breakout_scoring import WEIGHTS, WEIGHTS_ARE_MEASURED
    from strategy_breakout.breakout_strategy import load_breakout_config

    config = load_breakout_config()

    with st.expander("كيف يُحسب هذا التقييم · How this score is built"):
        if not WEIGHTS_ARE_MEASURED:
            st.warning(
                "**These weights are not measured.** They were assigned by "
                "judgement. A data-derived replacement was tried and reverted "
                "— see below.",
                icon="⚠️",
            )

        st.dataframe(
            pd.DataFrame([
                {"Feature": name.replace("_", " ").title(), "Weight": weight}
                for name, weight in sorted(WEIGHTS.items(), key=lambda kv: -kv[1])
            ]),
            hide_index=True, width="stretch",
            column_config={
                "Feature": st.column_config.TextColumn("Feature", width="medium"),
                "Weight": st.column_config.ProgressColumn(
                    "Weight", min_value=0, max_value=25, format="%d"
                ),
            },
        )

        st.markdown(
            f"""
**Two attempts to derive this from data were made on 2026-08-18, and both
failed.** They are recorded here because a failed attempt is the more useful
half: without it, the next reader repeats it.

**The weights.** Re-derived in proportion to each feature's out-of-sample lift
over 166,173 stock-days, they gave 55 points to volume confirmation and zero to
two features that failed validation outright. Backtested, that lost: 2.871% net
per trade at a 51.5% win rate against 3.498% and 55.0% for the weights above.
Per-feature lift asks what one feature predicts alone; the score asks how many
independent confirmations a setup carries, and requiring several weak ones is
itself the selectivity. Concentrating the weight on the two strongest let a
setup qualify on those alone and roughly tripled the signal count.

**The volume gate.** On raw breakouts the case looked overwhelming — twenty-day
return net of cost, validated after 2024-01-01:

| | return | win rate |
| --- | --- | --- |
| no breakout at all | +2.91% | 53.0% |
| breakout, 1.0–1.5× volume | −0.11% | 46.0% |
| **breakout, ≥ 2.5× volume** | **+5.73%** | **59.0%** |

Run through this strategy rather than over raw breakouts, it does not survive.
Varying only the gate across five windows, 1.5× returned 4.151% net per trade
against 3.770% at 2.5×, with a higher median and four of the five windows. The
gate stays at **{config.minimum_volume_ratio:g}×**. An edge measured on an
unfiltered population does not transfer to one already filtered several other
ways.

**What this score is not.** It is not a probability, its weights are not
measured, and the reward/risk it feeds does not subtract the cost of trading.
Re-derive anything here with `scripts/research/breakout_features.py` and
`breakout_volume_bands.py`.
"""
        )


def _market_column_config():
    return {
        "Rank": st.column_config.NumberColumn("#", width="small"),
        "Ticker": st.column_config.TextColumn("الرمز · Ticker", width="small"),
        NAME_COLUMN: st.column_config.TextColumn("اسم السهم", width="large"),
        "Signal": st.column_config.TextColumn("Signal", width="small"),
        "StrategySignal": st.column_config.TextColumn("Strategy signal", width="small"),
        "Regime": st.column_config.TextColumn("Regime", width="small"),
        "AIProbability": st.column_config.TextColumn("AI Advisory", width="medium"),
        "Confidence": st.column_config.ProgressColumn(
            "Confidence", min_value=0, max_value=100, format="%d%%",
            help="Computed from the same inputs as the score, so it is a "
                 "second opinion from the same witness rather than an "
                 "independent one. Not a probability of anything.",
        ),
        # The reachable maximum is 118, not 100: trend 30 + volume 20 +
        # support 15 + entry 28 + momentum 25, after `candle_score` and
        # `breakout_score` were removed from the total. Drawing the bar
        # against 100 clipped every score above it at a full bar, so the
        # strongest rows and the merely-strong ones looked identical.
        "Score": st.column_config.ProgressColumn(
            "Score", min_value=0, max_value=118, format="%d",
            help="Out of 118, not 100. It does not rank: within the "
                 "candidates that reach it, its correlation with the outcome "
                 "is +0.15 in 2016-2022 and +0.01 (p = 0.76) in 2023-2026. "
                 "Reported because it drives the BUY threshold, not because a "
                 "higher one has been shown to be better.",
        ),
        "RR": st.column_config.NumberColumn("R/R", format="%.2f"),
        "ClassicDecision": st.column_config.TextColumn("Classic", width="small"),
        "ClassicRR": st.column_config.NumberColumn("Classic R/R", format="%.2f"),
        "BreakoutDecision": st.column_config.TextColumn("Breakout", width="small"),
        "BreakoutRR": st.column_config.NumberColumn("Breakout R/R", format="%.2f"),
        "BreakoutScore": st.column_config.ProgressColumn(
            "Breakout score", min_value=0, max_value=100, format="%d",
            help=(
                "Sum of the confirmations present, each with a weight "
                "assigned by judgement rather than measured — a data-derived "
                "replacement was tried and backtested worse. What the score "
                "really counts is how many independent confirmations agree. "
                "See 'How this score is built'."
            ),
        ),
        "BreakoutConfidence": st.column_config.ProgressColumn(
            "Breakout confidence", min_value=0, max_value=100, format="%d%%",
            help=(
                "A transform of the score and the volume ratio. It is not a "
                "probability and was never measured as one."
            ),
        ),
        "BreakoutEdgeScore": st.column_config.NumberColumn(
            "Edge score", format="%.2f",
            help=(
                "Combines score, confidence and reward/risk. The reward/risk "
                "term does not account for the cost of trading."
            ),
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

    # Reached only when results were restored from an older run with no job in
    # this session. It reported the Rubix collector, its database, quote
    # freshness and the "Rubix Completed-Daily Bridge" -- all retired on
    # 2026-09-10 -- and warned that a dead overlay was stale. What remains true
    # is the history source and the candle date.
    from core.live_feed import LIVE_QUOTES_STATUS_EN, RETIREMENT_NOTE

    historical = str(summary.get("historical_provider") or "unknown")
    completed = _display_timestamp(summary.get("latest_completed_candle"))
    st.info(
        f"Historical analysis source: {historical}  ·  "
        f"Latest completed candle: {completed}  ·  "
        f"Live quotes: {LIVE_QUOTES_STATUS_EN}"
    )
    with st.expander("Data Status", expanded=False):
        st.caption(RETIREMENT_NOTE)
        reasons = summary.get("fallback_reasons") or []
        if reasons:
            st.warning("Fallback reason: " + "; ".join(map(str, reasons)))
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


def _display_timestamp(value):
    if not value:
        return "Not available"
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(value)
