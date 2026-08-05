"""One eligibility answer for every direct analysis view. Composes, never reimplements.

Stock Details, Watchlist and AI Analysis each reach a user without necessarily
passing through ``core/scanner.py``. Rather than repeat freshness rules in three
places - which is how they drift - each page asks this adapter and renders the
answer.

It composes the two existing contracts:

* ``core.daily_data_guard`` - may this symbol produce a current decision?
* ``core.rubix_quote_freshness`` - is this quote live, and what may it do?

and adds the one thing neither could supply alone: a ``cache_identity`` that
changes whenever any freshness input changes. That is what stops a decision or
an AI result computed from 2026-07-30 data being restored under a 2026-08-03
current heading once the expected session advances - the failure the audit
found in all three views, where caches were keyed on the symbol alone.

Pure: the evaluation instant is injected, and nothing here reads Streamlit
state, the wall clock, a database or a network, or calls the strategy or AI
engine.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from core.daily_data_guard import SymbolFreshness, classify_symbol_freshness
from core.rubix_quote_freshness import (
    RubixQuoteStatus,
    classify_rubix_quote,
    evaluate_overlay_permission,
)


#: Wording the pages must use verbatim, so three views cannot describe the same
#: state three different ways.
STALE_HEADLINE = "DAILY DATA STALE FOR THIS SYMBOL"
NOT_ANALYSED_LINE = "This symbol was not analyzed as a current opportunity."
HISTORICAL_LABELS = (
    "HISTORICAL SNAPSHOT",
    "RESEARCH ONLY",
    "NOT CURRENT MARKET ANALYSIS",
    "NOT A CURRENT BUY/WATCH/AVOID DECISION",
)
AI_BLOCKED_HEADLINE = "AI CURRENT ANALYSIS BLOCKED"
AI_HISTORICAL_LABELS = (
    "HISTORICAL AI EXPLANATION",
    "RESEARCH ONLY",
    "NOT CURRENT MARKET ANALYSIS",
    "NOT A BUY SIGNAL",
)
WATCHLIST_WITHHELD = "STALE DATA — DECISION WITHHELD"


@dataclass(frozen=True)
class AnalysisFreshnessContext:
    """Everything a view needs to decide what it may show, and why."""

    symbol: str
    expected_daily_session: str
    actual_daily_session: str
    daily_freshness_status: str
    trading_sessions_behind: int
    daily_eligible: bool
    daily_exclusion_reason: str

    rubix_quote_status: str
    rubix_market_timestamp: str
    rubix_receive_timestamp: str
    rubix_quote_session: str

    display_price_source: str
    decision_price_source: str
    rubix_overlay_applied: bool
    rubix_denial_reason: str

    current_analysis_allowed: bool
    historical_snapshot_allowed: bool
    blocking_reason: str
    cache_identity: str
    config_identity: str = ""

    # -- what a page may render ------------------------------------------- #

    @property
    def may_show_current_decision(self) -> bool:
        """BUY / WATCH / AVOID, score, confidence, ranking, position sizing."""
        return self.current_analysis_allowed

    @property
    def may_invoke_ai(self) -> bool:
        """A blocked symbol must not spend an external request to be refused."""
        return self.current_analysis_allowed

    @property
    def may_label_rubix_live(self) -> bool:
        return self.rubix_quote_status == RubixQuoteStatus.RUBIX_LIVE_CURRENT.value

    def stale_message(self) -> str:
        return (
            f"{STALE_HEADLINE}\n\n"
            f"Expected completed session: {self.expected_daily_session}\n"
            f"Actual latest candle: {self.actual_daily_session or 'unavailable'}\n"
            f"Trading sessions behind: {self.trading_sessions_behind}\n"
            f"Freshness status: {self.daily_freshness_status}\n\n"
            f"{NOT_ANALYSED_LINE}"
        )

    def ai_blocked_message(self) -> str:
        return (
            f"{AI_BLOCKED_HEADLINE}\n\n"
            f"Expected completed session: {self.expected_daily_session}\n"
            f"Actual latest candle: {self.actual_daily_session or 'unavailable'}\n"
            f"Daily freshness: {self.daily_freshness_status}\n\n"
            "AI analysis was not invoked because the symbol does not have "
            "current daily data."
        )

    def provenance_rows(self) -> list[tuple[str, str]]:
        """The one provenance panel every view renders.

        A provider or cache update time is deliberately absent: it is not a
        candle date, and presenting it beside one invites exactly the confusion
        that produced the 2026-08-04 incident.
        """
        return [
            ("Daily source", "EODHD"),
            ("Expected completed session", self.expected_daily_session or "—"),
            ("Actual candle session", self.actual_daily_session or "—"),
            ("Daily freshness", self.daily_freshness_status),
            ("Trading sessions behind", str(self.trading_sessions_behind)),
            ("Rubix quote status", self.rubix_quote_status),
            ("Rubix market time", self.rubix_market_timestamp or "unavailable"),
            ("Rubix receive time", self.rubix_receive_timestamp or "unavailable"),
            ("Display price source", self.display_price_source),
            ("Decision price source", self.decision_price_source),
            ("Current analysis allowed", "Yes" if self.current_analysis_allowed else "No"),
        ]

    def rubix_message(self) -> str:
        """Typed Rubix wording. Never a generic FRESH / LIVE / UPDATED."""
        from core.rubix_quote_freshness import STATUS_LABEL

        status = RubixQuoteStatus(self.rubix_quote_status)
        if status is RubixQuoteStatus.RUBIX_PREVIOUS_SESSION:
            return (f"RUBIX PREVIOUS SESSION\nLast quote session: "
                    f"{self.rubix_quote_session}\nDaily EODHD close retained.")
        if status is RubixQuoteStatus.RUBIX_CURRENT_SESSION_LAST:
            return STATUS_LABEL[status]
        return STATUS_LABEL[status]

    def overlay_denial_message(self) -> str:
        return ("Decision price: EODHD daily close\n"
                f"Rubix overlay: not applied — {self.rubix_denial_reason}")


def _identity(*parts) -> str:
    """A fingerprint over every input that could change what may be shown."""
    payload = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def build_analysis_context(
    symbol,
    *,
    evaluated_at,
    expected_session,
    actual_session,
    mapping_verified=False,
    quote_price=None,
    quote_market_timestamp=None,
    quote_receive_timestamp=None,
    source_identity="",
    config_identity="",
    ai_mode_identity="",
    holidays=None,
    budget_seconds=None,
    source_progressing=None,
    bars=None,
    minimum_bars=None,
):
    """Compose one eligibility answer for a single symbol.

    ``actual_session`` must come from the final accepted candle row - never a
    cache refresh time, a file mtime, the run date, or the expected session.
    """

    daily = classify_symbol_freshness(
        symbol, actual_session, expected_session,
        holidays=holidays, bars=bars, minimum_bars=minimum_bars,
    )
    quote = classify_rubix_quote(
        symbol,
        evaluated_at=evaluated_at,
        mapping_verified=mapping_verified,
        quote_price=quote_price,
        market_timestamp=quote_market_timestamp,
        receive_timestamp=quote_receive_timestamp,
        permitted_session=expected_session,
        holidays=holidays,
        budget_seconds=budget_seconds,
        source_progressing=source_progressing,
    )
    permission = evaluate_overlay_permission(
        quote,
        daily_symbol_current=daily.freshness_status == SymbolFreshness.CURRENT,
    )

    allowed = daily.eligible_for_current_analysis
    blocking = "" if allowed else (
        daily.exclusion_reason
        or f"daily data is {daily.freshness_status.value}")

    return AnalysisFreshnessContext(
        symbol=str(symbol),
        expected_daily_session=daily.expected_session,
        actual_daily_session=daily.actual_latest_session,
        daily_freshness_status=daily.freshness_status.value,
        trading_sessions_behind=daily.trading_sessions_behind,
        daily_eligible=allowed,
        daily_exclusion_reason=daily.exclusion_reason,
        rubix_quote_status=quote.status.value,
        rubix_market_timestamp=quote.market_timestamp,
        rubix_receive_timestamp=quote.receive_timestamp,
        rubix_quote_session=quote.quote_session,
        display_price_source=permission.display_price_source,
        decision_price_source=permission.decision_price_source,
        rubix_overlay_applied=permission.may_overlay_display_price,
        rubix_denial_reason=permission.denial_reason,
        current_analysis_allowed=allowed,
        # Historical viewing is always permitted; it is the *labelling* that
        # must be explicit, never the availability.
        historical_snapshot_allowed=True,
        blocking_reason=blocking,
        config_identity=str(config_identity),
        cache_identity=_identity(
            symbol,
            daily.actual_latest_session,     # the candle the answer came from
            daily.expected_session,          # what "current" meant at the time
            daily.freshness_status.value,
            source_identity,
            permission.decision_price_source,
            quote.status.value,
            quote.market_timestamp,
            config_identity,
            ai_mode_identity,
        ),
    )


def cached_result_is_current(context, cached_identity) -> bool:
    """Whether a cached decision or AI result may still be shown as current.

    Keyed on the full freshness identity rather than the symbol. A result
    computed while 2026-07-30 was current has a different identity once the
    expected session advances, so it can no longer be restored as current -
    which is exactly what all three views previously did.
    """
    if not context.current_analysis_allowed:
        return False
    return bool(cached_identity) and cached_identity == context.cache_identity


__all__ = [
    "AI_BLOCKED_HEADLINE",
    "AI_HISTORICAL_LABELS",
    "HISTORICAL_LABELS",
    "NOT_ANALYSED_LINE",
    "STALE_HEADLINE",
    "WATCHLIST_WITHHELD",
    "AnalysisFreshnessContext",
    "build_analysis_context",
    "cached_result_is_current",
]
