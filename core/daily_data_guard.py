"""Fail-closed guard for daily candle provenance. No Streamlit, no provider.

On 2026-08-04 the Daily Dashboard headlined "Latest completed candle
2026-08-03" above a table whose prices were Thursday 2026-07-30 closes. The
verified cause was not a relabelled candle - every row's date and OHLCV agreed
at every layer, and a bounded no-cache EODHD probe confirmed that EODHD itself
had published 2026-08-03 for only 6 of 241 EGX symbols. The headline was
``max(session_dates)``, so six current rows described a table that was
overwhelmingly one session behind.

This module decides, from evidence alone, whether the displayed data may be
used for analysis. It never fetches, never guesses a date, and never treats an
expected session as an observed one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class DailyDataVerdict(str, Enum):
    """Why current analysis is or is not permitted."""

    #: Observed session matches what the exchange calendar expects.
    CURRENT = "CURRENT"
    #: Observed session is behind the expected completed session.
    STALE_BEHIND_EXPECTED = "STALE_BEHIND_EXPECTED"
    #: Rows disagree about which session they belong to.
    MIXED_SESSION_DATES = "MIXED_SESSION_DATES"
    #: A displayed date does not match the date its own source recorded.
    DISPLAYED_DATE_MISMATCH = "DISPLAYED_DATE_MISMATCH"
    #: Nothing dated to judge.
    NO_DATED_CANDLE = "NO_DATED_CANDLE"


#: Verdicts that must stop BUY/WATCH/AVOID from being produced.
BLOCKING_VERDICTS = frozenset({
    DailyDataVerdict.STALE_BEHIND_EXPECTED,
    DailyDataVerdict.MIXED_SESSION_DATES,
    DailyDataVerdict.DISPLAYED_DATE_MISMATCH,
    DailyDataVerdict.NO_DATED_CANDLE,
})


@dataclass(frozen=True)
class DailyDataDecision:
    verdict: DailyDataVerdict
    expected_session: str
    actual_session: str
    message: str
    detail: str = ""
    distribution: dict = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return self.verdict in BLOCKING_VERDICTS

    @property
    def analysis_allowed(self) -> bool:
        return not self.blocked


def _mismatch_message(expected: str, actual: str) -> str:
    """The exact operator-facing wording for a date mismatch."""
    return (
        "DAILY DATA DATE MISMATCH\n"
        f"Expected completed session: {expected}\n"
        f"Actual EODHD candle date: {actual}\n"
        "Analysis is blocked because the displayed prices are not from the "
        "labelled session."
    )


def evaluate_daily_data(coverage, expected_session, displayed_session=None):
    """Decide whether the displayed daily data may drive analysis.

    ``coverage`` is the observed session distribution (see
    ``dashboard.home.session_coverage``); ``expected_session`` is the exchange
    calendar's expectation. Both are compared as ``YYYY-MM-DD`` strings, which
    order correctly and carry no timezone ambiguity.

    ``displayed_session`` is what the UI is about to print. When it disagrees
    with the observed session the guard blocks, because a headline that does
    not match its own rows is the defect this module exists to stop.
    """

    expected = str(expected_session or "")[:10]
    actual = str((coverage or {}).get("dominant") or "")[:10]
    distribution = dict((coverage or {}).get("distribution") or {})
    total = int((coverage or {}).get("total") or 0)
    dominant_rows = int((coverage or {}).get("dominant_rows") or 0)

    if not actual:
        return DailyDataDecision(
            DailyDataVerdict.NO_DATED_CANDLE, expected, "",
            "DAILY DATA UNAVAILABLE\nNo dated candle was observed, so no "
            "current analysis can be produced.",
            distribution=distribution,
        )

    if displayed_session is not None and str(displayed_session)[:10] != actual:
        return DailyDataDecision(
            DailyDataVerdict.DISPLAYED_DATE_MISMATCH, expected, actual,
            _mismatch_message(expected or str(displayed_session)[:10], actual),
            detail=(f"the page was about to display {str(displayed_session)[:10]} "
                    f"while the rows carry {actual}"),
            distribution=distribution,
        )

    if (coverage or {}).get("mixed"):
        share = (100.0 * dominant_rows / total) if total else 0.0
        return DailyDataDecision(
            DailyDataVerdict.MIXED_SESSION_DATES, expected, actual,
            _mismatch_message(expected or actual, actual),
            detail=(f"rows disagree about the session: {dominant_rows}/{total} "
                    f"({share:.1f}%) are {actual}; distribution {distribution}"),
            distribution=distribution,
        )

    if expected and actual < expected:
        return DailyDataDecision(
            DailyDataVerdict.STALE_BEHIND_EXPECTED, expected, actual,
            _mismatch_message(expected, actual),
            detail=(f"the provider has not published {expected} for these "
                    f"symbols; the newest accepted candle is {actual}"),
            distribution=distribution,
        )

    return DailyDataDecision(
        DailyDataVerdict.CURRENT, expected, actual,
        f"Daily data is current for {actual}.",
        distribution=distribution,
    )


# --------------------------------------------------------------------------- #
# Live overlay freshness
# --------------------------------------------------------------------------- #

RUBIX_STALE_HEADLINE = "RUBIX QUOTE STALE"


@dataclass(frozen=True)
class QuoteFreshness:
    fresh: bool
    quote_session: str
    message: str = ""

    @property
    def may_overwrite_close(self) -> bool:
        """A quote from an earlier session never replaces a daily close."""
        return self.fresh


def evaluate_quote_freshness(quote_timestamp, session_date):
    """A quote is current only if it belongs to the session being displayed.

    Age in seconds is deliberately not the test. A quote captured at the close
    of 2026-08-03 is hours old but belongs to that session, while the same
    number of seconds spanning a session boundary does not. What matters is
    which session produced it.
    """

    quote_session = str(quote_timestamp or "")[:10]
    displayed = str(session_date or "")[:10]
    if not quote_session:
        return QuoteFreshness(False, "", f"{RUBIX_STALE_HEADLINE}\nLast quote "
                                         "session: unknown")
    if not displayed or quote_session == displayed:
        return QuoteFreshness(True, quote_session)
    return QuoteFreshness(
        False, quote_session,
        f"{RUBIX_STALE_HEADLINE}\nLast quote session: {quote_session}",
    )


__all__ = [
    "BLOCKING_VERDICTS",
    "RUBIX_STALE_HEADLINE",
    "DailyDataDecision",
    "DailyDataVerdict",
    "QuoteFreshness",
    "evaluate_daily_data",
    "evaluate_quote_freshness",
]
