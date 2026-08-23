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
from dashboard.ui import page_header, signal_card
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


def _eligibility(value):
    """Same-session tradability. UNKNOWN is shown as a question, not a yes."""

    return {"ELIGIBLE": "yes", "NOT_ELIGIBLE": "NO"}.get(value, "?")


def _signal_frame(report) -> pd.DataFrame:
    """One row per signal, with numbers kept as numbers.

    Every column used to be a pre-formatted string, which left the whole table
    ragged: prices and percentages aligned left like prose, and no column
    sorted in any useful order. Keeping the real types and formatting them at
    render time through ``column_config`` fixes the alignment, the sorting and
    the decimal places at once.

    ``None`` stays ``None`` rather than becoming a dash here, so a missing
    number renders as an empty cell instead of contaminating a numeric column
    with text.
    """

    rows = []
    for signal in report.signals:
        rows.append({
            "Time": signal.detection_time_label,
            "Ticker": signal.canonical_ticker,
            "Company": signal.company_name or "",
            # -- what the trade is worth after what it costs ---------------
            "T1 move %": signal.target_1_percent,
            "Cost %": signal.total_cost_percent,
            "Net @T1 %": signal.net_target_1_percent,
            "Net R/R": signal.net_reward_risk,
            "Eff R/R": signal.effective_reward_risk,
            # -- the engine's own levels -----------------------------------
            "Trigger": signal.trigger_price,
            "Stop": signal.proposed_stop,
            "T1": signal.target_1,
            "T2": signal.target_2,
            "Risk/Share": signal.risk_per_share,
            # -- market and context ----------------------------------------
            "Spread %": signal.median_spread_percent,
            "Spread/Risk": signal.spread_share_of_risk,
            "T+0": _eligibility(signal.intraday_eligibility),
            "Sector": signal.sector_name,
            "Mkt Cap (bn)": signal.market_cap_billions,
            "ATR": signal.atr_value,
            "Episodes": signal.episode_count,
            "Qualification": signal.qualification_status,
        })
    return pd.DataFrame(rows)


def _percent_column(label: str, help_text: str, decimals: int = 2):
    return st.column_config.NumberColumn(
        label, help=help_text, format=f"%.{decimals}f%%", width="small"
    )


def _signal_column_config() -> dict:
    """Formatting, widths and per-column help for the signals table."""

    return {
        "Time": st.column_config.TextColumn("Time", width="small"),
        "Ticker": st.column_config.TextColumn("Ticker", width="small", pinned=True),
        "Company": st.column_config.TextColumn("Company", width="medium"),
        "T1 move %": _percent_column(
            "T1 move %", "How far the first target is from the trigger."
        ),
        "Cost %": _percent_column(
            "Cost %",
            "The whole round trip: the measured spread once, plus commission "
            "and slippage on both sides. Empty when the spread was never "
            "observed, which makes the cost unknown rather than smaller.",
        ),
        "Net @T1 %": _percent_column(
            "Net @T1 %",
            "What is left of the move after costs. Negative means the trade "
            "loses money at its own first target.",
        ),
        "Net R/R": st.column_config.NumberColumn(
            "Net R/R",
            help="Reward/risk after the same costs, to the target the engine "
                 "would run to (T2). Empty means no positive net reward "
                 "exists -- the costs consume the whole move.",
            format="%.2f", width="small",
        ),
        "Eff R/R": st.column_config.NumberColumn(
            "Eff R/R",
            help="The engine's own ratio, untouched. Reads 2.00 throughout "
                 "because both targets are fixed multiples of the risk unit, "
                 "however small the move and however large the cost.",
            format="%.2f", width="small",
        ),
        "Trigger": st.column_config.NumberColumn("Trigger", format="%.3f", width="small"),
        "Stop": st.column_config.NumberColumn("Stop", format="%.3f", width="small"),
        "T1": st.column_config.NumberColumn("T1", format="%.3f", width="small"),
        "T2": st.column_config.NumberColumn("T2", format="%.3f", width="small"),
        "Risk/Share": st.column_config.NumberColumn(
            "Risk/Share", format="%.3f", width="small"
        ),
        "Spread %": _percent_column(
            "Spread %",
            "Median quoted bid/ask spread observed on Rubix across the "
            "session -- what the book looked like, not a guaranteed fill.",
            decimals=3,
        ),
        "Spread/Risk": st.column_config.NumberColumn(
            "Spread/Risk",
            help="Spread as a share of the engine's risk unit.",
            format="percent", width="small",
        ),
        "T+0": st.column_config.TextColumn(
            "T+0",
            help="Same-session tradability. '?' means unknown, which is not "
                 "the same as yes.",
            width="small",
        ),
        "Sector": st.column_config.TextColumn("Sector", width="medium"),
        "Mkt Cap (bn)": st.column_config.NumberColumn(
            "Mkt Cap (bn)", format="%.2f", width="small"
        ),
        "ATR": st.column_config.NumberColumn("ATR", format="%.3f", width="small"),
        "Episodes": st.column_config.NumberColumn("Episodes", format="%d", width="small"),
        "Qualification": st.column_config.TextColumn("Qualification", width="medium"),
    }


def _style_signals(frame: pd.DataFrame):
    """Colour the one column a reader must not skim past.

    Only ``Net @T1 %`` is coloured. Colouring more would turn a table of
    evidence into a recommendation, which this page does not make.
    """

    def net_colour(value):
        if not isinstance(value, (int, float)) or pd.isna(value):
            return ""
        return "color: #d13212" if value <= 0 else "color: #1a7f37"

    return frame.style.map(net_colour, subset=["Net @T1 %"])


def _sector_frame(report) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Sector": tally.sector_name,
            "Signals": tally.signal_count,
        }
        for tally in report.sector_summary
    ])


def _tax_note(costs) -> str:
    """Say where tax stands, distinguishing "none" from "nobody said".

    Zero and unset are different claims. A rate of zero is an answer -- there
    is no capital gains tax on this market, and the transaction tax that does
    apply is the stamp duty already charged as a fee line. An unset rate is the
    absence of an answer, and reporting it as zero would put a number nobody
    chose behind every net figure on the page.
    """

    if not costs.tax_configured:
        return "**Tax is not included** — no rate is configured."
    if not costs.capital_gains_tax_percent:
        return (
            "No capital gains tax applies; the stamp duty that does is already "
            "a line in the fee schedule and is not charged twice."
        )
    return (
        f"Capital gains tax of {costs.capital_gains_tax_percent:.1f}% is "
        f"applied to what survives."
    )


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
        f"Costs charged: **{costs.round_trip_percent:.3f}%** round trip "
        f"({costs.commission_per_side * 100:.4f}% fees and "
        f"{costs.slippage_per_side * 100:.3f}% slippage, each side) plus the "
        f"measured spread. "
        + _tax_note(costs)
    )

    if costs.fee_lines:
        with st.expander("Fee schedule"):
            st.caption(
                "Transcribed from a broker contract note so every line can be "
                "checked against it. Edit `scalping.fee_schedule` in "
                "`config/settings.json` if your rates differ."
            )
            st.dataframe(
                pd.DataFrame(
                    [
                        {"Fee": name.replace("_", " ").title(), "Per side %": percent}
                        for name, percent in costs.fee_lines
                    ]
                    + [{"Fee": "Total per side", "Per side %":
                        costs.commission_per_side * 100}]
                ),
                hide_index=True, width="stretch",
                column_config={
                    "Fee": st.column_config.TextColumn("Fee", width="medium"),
                    "Per side %": st.column_config.NumberColumn(
                        "Per side %", format="%.4f%%", width="small"
                    ),
                },
            )
            if costs.order_fee_egp:
                st.caption(
                    f"A flat **{costs.order_fee_egp:.2f} EGP** order fee applies "
                    f"per side on top. It is not in the percentages above "
                    f"because its weight depends entirely on position size: on "
                    f"a 20,000 EGP position it is "
                    f"{costs.order_fee_percent(20000):.3f}% round trip; on "
                    f"100,000 EGP, {costs.order_fee_percent(100000):.3f}%."
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

    page_header("ORB Scalping Signals", "Read-only view of what the engine recorded. It places no order, manages no position, and reports no fill.", icon="⚡")
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


def _signal_note(signal, eligibility_is_universal: bool = False) -> str:
    """The one qualifying sentence *this* signal needs, or nothing.

    A caveat that is true of every signal on the page is not a property of any
    one of them. The eligibility file ships empty on purpose, so UNKNOWN is
    currently the state of the whole list; repeating it on each card would put
    the same sentence five times under five different tickers and teach the eye
    to skip the line that will one day say something specific.
    """
    if signal.intraday_eligibility == "UNKNOWN" and not eligibility_is_universal:
        return ("Same-session eligibility is unknown for this security — which "
                "is not the same as yes.")
    if signal.intraday_eligibility == "NOT_ELIGIBLE":
        return ("Cannot be closed in the session it was opened, so it is not a "
                "scalping signal.")
    if signal.total_cost_percent is None:
        return ("The spread was never observed, so the cost here is unknown "
                "rather than zero.")
    return ""


def _sorted_signals(report):
    """Signals ordered by what is left after costs, best first.

    Ordering is not filtering: every signal stays on the page. Unknown net sits
    last because an unmeasured result cannot be ranked against a measured one.
    """
    return sorted(
        report.signals,
        key=lambda s: (s.net_target_1_percent is not None,
                       s.net_target_1_percent or 0.0),
        reverse=True,
    )


def _draw_card(signal, eligibility_is_universal: bool = False) -> None:
    signal_card(
        ticker=signal.canonical_ticker,
        subtitle=signal.sector_name or "",
        when=signal.detection_time_label,
        entry=signal.trigger_price,
        stop=signal.proposed_stop,
        target=signal.target_1,
        move_percent=signal.target_1_percent,
        cost_percent=signal.total_cost_percent,
        net_percent=signal.net_target_1_percent,
        note=_signal_note(signal, eligibility_is_universal),
    )


def _render_signal_cards(report) -> None:
    """Each signal drawn to scale, with the ones that lose money folded away.

    Folding is presentation, never suppression: the count is stated, the panel
    opens in one click, and the full table below always lists every signal.
    """
    ordered = _sorted_signals(report)
    clears_cost = [s for s in ordered
                   if s.net_target_1_percent is not None
                   and s.net_target_1_percent > 0]
    rest = [s for s in ordered if s not in clears_cost]

    # The page already carries one warning when eligibility is unknown across
    # the board; the cards then say nothing about it rather than repeating it.
    universal = all(s.intraday_eligibility == "UNKNOWN" for s in ordered)

    for signal in clears_cost:
        _draw_card(signal, universal)

    if not clears_cost:
        st.warning(
            f"**No signal on {report.session_date} clears its own cost at the "
            f"first target.** All {report.signal_count} are below, with entry, "
            f"exit and target all going exactly as the engine intended."
        )

    if rest:
        losing = sum(1 for s in rest if s.net_target_1_percent is not None)
        unknown = len(rest) - losing
        label = []
        if losing:
            label.append(f"{losing} below cost")
        if unknown:
            label.append(f"{unknown} with an unknown cost")
        with st.expander(" · ".join(label), expanded=not clears_cost):
            for signal in rest:
                _draw_card(signal, universal)


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
    _render_signal_cards(report)

    frame = _signal_frame(report)
    with st.expander(f"Every column, all {report.signal_count} signals"):
        st.dataframe(
            _style_signals(frame),
            width="stretch",
            hide_index=True,
            column_config=_signal_column_config(),
        )
        st.caption(
            "Hover any column header for what it means. An empty **Net R/R** "
            "with a **Cost %** present means the costs consume the whole move, "
            "so no positive net reward exists — different from an empty "
            "**Cost %**, which means the spread was never observed and the "
            "cost is unknown. None of these columns filter, rank or "
            "recommend anything."
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
