"""ORB scalping signals — a read-only daily assistant view.

Answers "what did the ORB engine find, and what were its own levels?" in one
screen. It renders evidence; it computes no strategy value, ranks nothing, and
places no orders.

Deliberately absent: any buy/sell control, any position sizing, any "fill"
price, any suggestion of what to trade. Every number shown is either an
engine-produced level read back from persisted qualification, or descriptive
context (sector, company, market cap).
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import streamlit as st

from core.sector_context import UNKNOWN_SECTOR_ID, sector_map_provenance
from services.automation_status import (
    COMPLETED,
    NEVER_RAN,
    REFUSED,
    RUNNING,
    read_status,
)
from services.trading_costs import CONFIG_SECTION
from services.orb_daily_assistant import (
    AssistantSourceUnavailable,
    MISSING_FOR_SIGNAL,
    NOT_PERSISTED,
    SESSION_DIR_ENV,
    discover_sessions,
    load_daily_report,
)

DASH = "—"


def _price(value):
    return f"{value:,.3f}" if isinstance(value, (int, float)) else DASH


def _ratio(value):
    return f"{value:.2f}" if isinstance(value, (int, float)) else DASH


def _cap(value):
    return f"{value:,.2f}" if isinstance(value, (int, float)) else DASH


def _rank(value):
    return str(value) if isinstance(value, int) else DASH


def _spread(value):
    return f"{value:.3f}%" if isinstance(value, (int, float)) else DASH


def _percent_of(value):
    return f"{value * 100:.0f}%" if isinstance(value, (int, float)) else DASH


#: A reward/risk of ``None`` from the cost model does not mean "unknown" -- it
#: means the costs consume the entire move, so no ratio exists. Rendering that
#: as the same dash used for missing data would lose the distinction exactly
#: where it matters most.
NO_NET_REWARD = "loses"


def _net_ratio(value):
    return f"{value:.2f}" if isinstance(value, (int, float)) else NO_NET_REWARD


def _eligibility(value):
    """Same-session tradability. UNKNOWN is shown as a question, not a yes."""

    return {"ELIGIBLE": "yes", "NOT_ELIGIBLE": "NO"}.get(value, "?")


def _signal_frame(report) -> pd.DataFrame:
    """One row per signal, in the column order a human reads left to right."""

    rows = []
    for signal in report.signals:
        rows.append({
            "Time": signal.detection_time_label,
            "Ticker": signal.canonical_ticker,
            "Company": signal.company_name or DASH,
            "Sector": signal.sector_name,
            "Rank": _rank(signal.sector_rank),
            "Mkt Cap (bn EGP)": _cap(signal.market_cap_billions),
            "State": signal.signal_state.replace("_", " ").title(),
            "Trigger": _price(signal.trigger_price),
            "Stop": _price(signal.proposed_stop),
            "T1": _price(signal.target_1),
            "T2": _price(signal.target_2),
            "Risk/Share": _price(signal.risk_per_share),
            "Eff R/R": _ratio(signal.effective_reward_risk),
            "T1 move %": _spread(signal.target_1_percent),
            "Spread %": _spread(signal.median_spread_percent),
            "Cost %": _spread(signal.total_cost_percent),
            "Net @T1 %": _spread(signal.net_target_1_percent),
            "Net R/R @T2": _net_ratio(signal.net_reward_risk),
            "Spread/Risk": _percent_of(signal.spread_share_of_risk),
            "T+0": _eligibility(signal.intraday_eligibility),
            "ATR": _price(signal.atr_value),
            "Qualification": signal.qualification_status,
            "Episodes": signal.episode_count,
        })
    return pd.DataFrame(rows)


def _sector_frame(report) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Sector": tally.sector_name,
            "Signals": tally.signal_count,
        }
        for tally in report.sector_summary
    ])


#: Above this share of the first target, the round-trip spread is the dominant
#: term in the trade and the engine's own reward/risk stops describing it.
COST_HEAVY_SHARE = 0.50


def _cost_summary(report) -> None:
    """Say how many signals cannot pay for themselves, before anyone decides.

    The engine sets both targets as pure R multiples of the risk unit, whose
    only floor is the minimum pullback depth (0.1%), and nothing in the
    strategy compares either against the cost of trading. So a shallow pullback
    yields a shallow target, and the engine's reward/risk still reads 2.0
    because it is 2.0 by construction — that ratio holds however small the move
    is, and however much the round trip costs.

    This ranks nothing and removes nothing. It states what the trade costs.
    """

    priced = [s for s in report.signals if s.net_target_1_percent is not None]
    if not priced:
        return

    costs = priced[0].costs

    if not costs.rates_loaded:
        st.error(
            f"**Costs could not be read from settings, so every figure below "
            f"understates what a trade costs.** {costs.load_error or ''} "
            f"Set `commission` and `slippage` under `{CONFIG_SECTION}` in "
            f"`config/settings.json`.",
            icon="⚠️",
        )
        return

    losing = [s for s in priced if s.net_target_1_percent <= 0]

    st.caption(
        f"Costs charged: **{costs.round_trip_percent:.2f}%** round trip "
        f"({costs.commission_per_side * 100:.2f}% commission and "
        f"{costs.slippage_per_side * 100:.3f}% slippage, each side) plus the "
        f"measured spread. "
        + (
            f"Capital gains tax of {costs.capital_gains_tax_percent:.1f}% is "
            f"applied to what survives."
            if costs.tax_configured
            else "**Tax is not included** — no rate is configured."
        )
    )

    if losing:
        worst = min(losing, key=lambda s: s.net_target_1_percent)
        st.warning(
            f"**{len(losing)} of {len(priced)} signals lose money at their own "
            f"first target.** Not on a bad fill, not on a stop — with the "
            f"entry, the exit and the target all going exactly as the engine "
            f"intended. Worst: `{worst.canonical_ticker}`, a "
            f"{worst.target_1_percent:.2f}% move against "
            f"{worst.total_cost_percent:.2f}% of cost, leaving "
            f"**{worst.net_target_1_percent:+.2f}%**. Every one of them still "
            f"reports the engine's reward/risk of "
            f"{worst.effective_reward_risk:.1f} beside it.",
            icon="🛑",
        )
    else:
        st.success(
            f"All {len(priced)} signals clear their costs at the first target.",
            icon="✅",
        )

    thin = [s for s in priced
            if s.target_1_percent is not None and s.target_1_percent < 1.0]
    if len(thin) == len(priced):
        st.caption(
            f"Every one of the {len(priced)} priced signals has a first target "
            f"under 1%. That is the strategy's design, not an anomaly: the risk "
            f"unit is the pullback depth and the targets are 1R and 2R of it."
        )


def _automation_banner(session_date: str) -> None:
    """Say what the scheduler did for this date, especially when it did nothing.

    An empty signals page has two very different causes — the engine found
    nothing, or nothing ever ran — and they were previously indistinguishable
    from this screen. That ambiguity is how two scheduled tasks stayed broken
    for weeks while sessions were started by hand.
    """

    status = read_status(session_date)

    if status.outcome == COMPLETED:
        st.success(f"Scheduled automation completed for {session_date}.", icon="🗓️")
        return

    if status.outcome == NEVER_RAN:
        # Only worth raising for a day that has a session to miss; a future or
        # non-trading date legitimately has no run.
        if session_date <= date.today().isoformat():
            st.warning(
                f"**The scheduled automation left no record for {session_date}.** "
                f"Either the task did not run, or it failed before reaching the "
                f"script. Any session shown below was started by hand. Check "
                f"`EGX ORB Full Shadow Automation` in Task Scheduler.",
                icon="🗓️",
            )
        return

    if status.outcome == RUNNING:
        st.info(
            f"Scheduled automation is running (started {status.started_at or '—'}). "
            f"If this persists past 14:15 the run died without an exit path.",
            icon="🗓️",
        )
        return

    icon = "🛑" if status.outcome == REFUSED else "⚠️"
    body = f"**Scheduled automation reported `{status.outcome}` for {session_date}.**"
    if status.reason:
        body += f"\n\n{status.reason}"
    if status.steps:
        body += "\n\n" + " · ".join(f"`{k}` {v}" for k, v in status.steps.items())
    st.error(body, icon=icon)


#: How often the live panel re-reads the session database. A running session
#: writes continuously, and a page that only refreshes when the reader happens
#: to click something shows a stale signal list at exactly the moment it
#: matters most.
REFRESH_SECONDS = 30


def show_orb_signals() -> None:
    """Streamlit page: today's (or a chosen session's) ORB signals."""

    st.title("⚡ ORB Scalping Signals")
    st.caption(
        "Read-only research view of what the ORB engine detected. "
        "This is an assistant: it never places an order and never reports a fill."
    )

    directory, sessions = discover_sessions()

    if not sessions:
        st.warning("No ORB session databases found.")
        # No database at all is exactly the case where "did the scheduler run?"
        # is the first question, so answer it before the search-path note.
        _automation_banner(date.today().isoformat())
        st.markdown(
            f"Searched `{directory}`. Point the assistant at a session "
            f"directory by setting the `{SESSION_DIR_ENV}` environment variable."
        )
        return

    labels = [str(session_date) for session_date, _ in sessions]
    chosen = st.selectbox("Session", labels, index=0)
    path = dict((str(d), p) for d, p in sessions)[chosen]

    # Only a session that is still being written needs polling; a finished one
    # cannot change, so re-reading it would be pure noise.
    is_today = chosen == date.today().isoformat()
    auto = st.toggle(
        f"Auto-refresh every {REFRESH_SECONDS}s",
        value=is_today,
        help="Re-reads the session database on a timer while a session is live.",
    )

    @st.fragment(run_every=REFRESH_SECONDS if auto else None)
    def _live_panel() -> None:
        _render_session(path, chosen, auto)

    _live_panel()


def _render_session(path, chosen: str, auto: bool) -> None:
    """Everything that must be re-read when the session database changes."""

    _automation_banner(chosen)

    try:
        report = load_daily_report(path)
    except AssistantSourceUnavailable as error:
        st.error(f"Could not read this session: {error}")
        return

    if auto:
        st.caption(
            f"Updated {datetime.now().strftime('%H:%M:%S')} · refreshing every "
            f"{REFRESH_SECONDS}s"
        )

    if report.signal_count == 0:
        st.info(f"The engine produced no ENTRY_READY_RESEARCH signals on {chosen}.")
        return

    known = [s for s in report.signals if s.sector_id != UNKNOWN_SECTOR_ID]
    columns = st.columns(4)
    columns[0].metric("Signals", report.signal_count)
    columns[1].metric("Sectors", len(report.sector_summary))
    columns[2].metric("With engine levels", report.levels_available_count)
    columns[3].metric("Sector resolved", f"{len(known)}/{report.signal_count}")

    # Levels are the point of the table, so their absence is stated plainly
    # rather than left as a column of dashes for the reader to interpret.
    if report.levels_available_count == 0:
        st.warning(
            f"**Engine levels were never persisted for this session.** It was "
            f"recorded at schema v{report.schema_version}, before the "
            f"qualification table existed, so trigger / stop / T1 / T2 / R:R "
            f"show {DASH}. These values are not recoverable after the fact and "
            f"are deliberately **not** recomputed from later price data — that "
            f"would invent levels the engine never used. Sessions recorded with "
            f"qualification persistence active carry the real numbers."
        )
    elif report.levels_available_count < report.signal_count:
        st.info(
            f"{report.levels_available_count} of {report.signal_count} signals "
            f"carry persisted engine levels; the rest report "
            f"`{MISSING_FOR_SIGNAL}`."
        )

    _cost_summary(report)

    st.subheader("Signals")
    st.dataframe(_signal_frame(report), width="stretch", hide_index=True)
    st.caption(
        "**T1 move %** is how far the first target is from the trigger. "
        "**Spread %** is the median quoted bid/ask spread observed on Rubix "
        "across the session — what the book looked like, not a guaranteed fill. "
        "**Cost %** is the whole round trip: that spread once, plus commission "
        "and slippage on both sides. **Net @T1 %** is what is left of the move "
        "after it — negative means the trade loses money at its own first "
        "target. **Net R/R @T2** is the engine's reward/risk after the same "
        "costs, measured to the target the engine would actually run to, which "
        "is T2; `loses` there means the costs consume the entire move, and is "
        "not the same as the `—` used for a missing number. The engine's own "
        "`Eff R/R` is left untouched beside it and reads 2.0 throughout, "
        "because both targets are fixed multiples of the risk unit. "
        "**Spread/Risk** is the spread as a share of the engine's risk unit. "
        "**T+0** is same-session tradability: `?` means unknown, which is not "
        "the same as yes. None of these filter or rank anything."
    )

    priced = [s for s in report.signals if s.median_spread_percent is not None]
    if priced:
        expensive = sorted(
            priced, key=lambda s: s.median_spread_percent, reverse=True
        )[:3]
        st.caption(
            "Widest spreads this session: "
            + " · ".join(
                f"`{s.canonical_ticker}` {s.median_spread_percent:.2f}%"
                for s in expensive
            )
        )

    unknown_eligibility = [
        s for s in report.signals if s.intraday_eligibility == "UNKNOWN"
    ]
    if unknown_eligibility:
        st.warning(
            f"**Same-session eligibility is unknown for "
            f"{len(unknown_eligibility)} of {report.signal_count} signals.** "
            "`data/universe/egx_intraday_eligibility.csv` ships empty on "
            "purpose — the exchange decides which securities may be sold in the "
            "session they were bought, and guessing that list would be worse "
            "than admitting it is unknown. A signal on a security you cannot "
            "close the same day is not a scalping signal. Fill the file in from "
            "the exchange's own published list."
        )

    st.subheader("Sector concentration")
    st.caption(
        "Descriptive only — a signal count per sector. It does not rank, score, "
        "or filter anything, and never feeds back into qualification."
    )
    st.dataframe(_sector_frame(report), width="stretch", hide_index=True)

    unknown = [s for s in report.signals if s.sector_id == UNKNOWN_SECTOR_ID]
    if unknown:
        tickers = ", ".join(f"`{s.canonical_ticker}`" for s in unknown)
        st.caption(
            f"No sector mapping for {tickers} — shown as Unknown and still "
            "listed. Missing context never removes a signal."
        )

    with st.expander("Provenance"):
        provenance = sector_map_provenance()
        st.markdown(
            f"""
**Session evidence**

- database: `{report.database_path}`
- lane: `{report.artifact_lane or DASH}` (mode `{report.run_mode}`)
- run id: `{report.run_id[:16]}…`
- engine: `{report.engine_version}`
- strategy fingerprint: `{report.strategy_fingerprint[:16]}…`
- stop reason: `{report.stop_reason or DASH}`
- schema version: `{report.schema_version}`
- qualification table present: `{report.qualification_available}`

**Sector context**

- map: `{provenance['path']}`
- tickers: {provenance['ticker_count']} across {provenance['sector_count']} sectors
- source: {provenance['source'] or DASH}
- priced as of: {provenance['source_as_of'] or DASH}

Session databases and the Rubix feed are opened `mode=ro` with
`PRAGMA query_only=ON`. Nothing on this page writes to either.
"""
        )
