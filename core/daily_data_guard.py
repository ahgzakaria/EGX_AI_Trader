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


# --------------------------------------------------------------------------- #
# Per-symbol freshness
# --------------------------------------------------------------------------- #
#
# A single global block was too coarse. On 2026-08-04 EODHD had published the
# 2026-08-03 session for 6 of 241 EGX symbols and 2026-07-30 for the rest, all
# date-honest. Blocking the whole scan hid the six genuinely current symbols;
# allowing the whole scan would have produced decisions from bars one session
# old. Freshness is therefore a property of each symbol, not of the run.


class SymbolFreshness(str, Enum):
    """Why one symbol may or may not drive a current decision."""

    #: Latest accepted candle is exactly the expected completed session.
    CURRENT = "CURRENT"
    #: Latest accepted candle is behind the expected completed session.
    STALE = "STALE"
    #: No candle date could be read from the accepted history.
    MISSING_DATE = "MISSING_DATE"
    #: A date was present but could not be interpreted.
    INVALID_DATE = "INVALID_DATE"
    #: Dated after the expected completed session - a provenance error.
    FUTURE_DATE = "FUTURE_DATE"
    #: The row's own provenance disagrees with the candle it carries.
    SESSION_MISMATCH = "SESSION_MISMATCH"
    #: Too few bars to evaluate, independent of date.
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


#: Only this status may contribute to a current trading decision.
ELIGIBLE_STATUSES = frozenset({SymbolFreshness.CURRENT})

#: Typed scan outcomes for symbols the freshness gate excluded. Kept distinct
#: from provider failures: a stale symbol is not a failure, and reporting it as
#: one hides a data-coverage problem behind an error rate.
FRESHNESS_OUTCOME = {
    SymbolFreshness.STALE: "SKIPPED_STALE_DAILY_DATA",
    SymbolFreshness.MISSING_DATE: "SKIPPED_MISSING_DAILY_DATE",
    SymbolFreshness.INVALID_DATE: "SKIPPED_MISSING_DAILY_DATE",
    SymbolFreshness.FUTURE_DATE: "SKIPPED_FUTURE_DAILY_DATE",
    SymbolFreshness.SESSION_MISMATCH: "SKIPPED_STALE_DAILY_DATA",
    SymbolFreshness.INSUFFICIENT_HISTORY: "INSUFFICIENT_HISTORY",
    SymbolFreshness.CURRENT: "SUCCESS_CURRENT",
}


@dataclass(frozen=True)
class SymbolFreshnessResult:
    """Everything needed to justify including or excluding one symbol."""

    symbol: str
    expected_session: str
    actual_latest_session: str
    trading_sessions_behind: int
    freshness_status: SymbolFreshness
    eligible_for_current_analysis: bool
    source_provider: str = ""
    source_mode: str = ""
    candle_identity: str = ""
    exclusion_reason: str = ""
    source_latest_ohlcv: dict = field(default_factory=dict)

    @property
    def outcome_status(self) -> str:
        return FRESHNESS_OUTCOME[self.freshness_status]

    def as_row(self) -> dict:
        """Flat, CSV-safe provenance for the audit export."""
        return {
            "Ticker": self.symbol,
            "ExpectedSession": self.expected_session,
            "ActualSession": self.actual_latest_session,
            "TradingSessionsBehind": self.trading_sessions_behind,
            "FreshnessStatus": self.freshness_status.value,
            "EligibleForCurrentAnalysis": self.eligible_for_current_analysis,
            "FreshnessOutcome": self.outcome_status,
            "ExclusionReason": self.exclusion_reason,
            "SourceProvider": self.source_provider,
            "SourceMode": self.source_mode,
            "CandleIdentity": self.candle_identity,
        }


def _as_date(value):
    """Parse a date from a date, datetime or ``YYYY-MM-DD`` prefix. Never guess."""
    from datetime import date as _date, datetime as _datetime

    if value is None or value == "":
        return None
    if isinstance(value, _datetime):
        return value.date()
    if isinstance(value, _date):
        return value
    text = str(value)[:10]
    try:
        return _datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def classify_symbol_freshness(
    symbol,
    actual_session,
    expected_session,
    *,
    source_provider="",
    source_mode="",
    candle_identity="",
    ohlcv=None,
    bars=None,
    minimum_bars=None,
    holidays=None,
):
    """Classify one symbol from its OWN latest accepted candle date.

    ``actual_session`` must come from the final accepted candle row. It is never
    inferred from a file mtime, a cache refresh timestamp, the scan run date or
    the expected session - each of those describes when something was fetched
    or what the calendar wanted, not which bar is actually held.
    """

    expected = _as_date(expected_session)
    actual_raw = actual_session
    actual = _as_date(actual_session)
    expected_text = expected.isoformat() if expected else ""
    actual_text = actual.isoformat() if actual else ""

    def result(status, behind, reason):
        return SymbolFreshnessResult(
            symbol=str(symbol),
            expected_session=expected_text,
            actual_latest_session=actual_text,
            trading_sessions_behind=behind,
            freshness_status=status,
            eligible_for_current_analysis=status in ELIGIBLE_STATUSES,
            source_provider=str(source_provider or ""),
            source_mode=str(source_mode or ""),
            candle_identity=str(candle_identity or ""),
            exclusion_reason=reason,
            source_latest_ohlcv=dict(ohlcv or {}),
        )

    if bars is not None and minimum_bars is not None and int(bars) < int(minimum_bars):
        return result(SymbolFreshness.INSUFFICIENT_HISTORY, 0,
                      f"{bars} bars available; {minimum_bars} required")

    if actual is None:
        if actual_raw in (None, ""):
            return result(SymbolFreshness.MISSING_DATE, 0,
                          "the accepted history carries no candle date")
        return result(SymbolFreshness.INVALID_DATE, 0,
                      f"candle date {actual_raw!r} could not be interpreted")

    if expected is None:
        # Without an authoritative expectation nothing may be called current.
        return result(SymbolFreshness.SESSION_MISMATCH, 0,
                      "the expected completed session is unavailable")

    if actual > expected:
        return result(SymbolFreshness.FUTURE_DATE, 0,
                      f"candle {actual_text} is dated after the expected "
                      f"completed session {expected_text}")

    if actual == expected:
        return result(SymbolFreshness.CURRENT, 0, "")

    behind = _sessions_behind(actual, expected, holidays)
    return result(
        SymbolFreshness.STALE, behind,
        f"latest candle {actual_text} is {behind} trading session"
        f"{'' if behind == 1 else 's'} behind {expected_text}")


def _sessions_behind(actual, expected, holidays=None):
    """Trading sessions between two dates, not calendar days.

    EGX trades Sunday-Thursday, so 2026-07-30 (Thursday) to 2026-08-03 (Monday)
    is four calendar days but two trading sessions. Reporting calendar distance
    would overstate how stale a symbol is across every weekend.
    """
    from core.egx_session import TRADING_WEEKDAYS, _as_holiday_set
    from datetime import timedelta

    holiday_set = _as_holiday_set(holidays)
    count = 0
    candidate = actual + timedelta(days=1)
    while candidate <= expected:
        if candidate.weekday() in TRADING_WEEKDAYS and candidate not in holiday_set:
            count += 1
        candidate += timedelta(days=1)
    return count


# --------------------------------------------------------------------------- #
# Universe coverage and market-wide conclusions
# --------------------------------------------------------------------------- #

#: Settings key for the data-quality coverage gate.
#:
#: This is NOT a strategy threshold. It does not change an indicator, a score,
#: a reward/risk requirement or a BUY/WATCH/AVOID rule, and no symbol-level
#: decision depends on it. It governs one thing: whether the *market-wide*
#: aggregates may be stated at all.
COVERAGE_SETTING_KEY = "minimum_daily_market_coverage_percent"

#: Conservative default.
#:
#: Chosen against the observed runs rather than invented. Healthy scans in this
#: repository analyse roughly 194 of 241 approved symbols (~80% of the
#: universe), and the incident scan had 6 of 241 (2.5%) genuinely current. A
#: market-wide claim needs most of the market behind it, so the gate sits at
#: 60% of the operational universe: comfortably below a normal run, far above a
#: partial-provider morning, and low enough that a few unavailable symbols
#: never suppress a legitimate summary.
DEFAULT_MINIMUM_COVERAGE_PERCENT = 60.0

#: Shown instead of BULL / BEAR / SIDEWAYS when coverage is too thin.
INSUFFICIENT_CURRENT_COVERAGE = "INSUFFICIENT_CURRENT_COVERAGE"


def minimum_coverage_percent(settings_data=None):
    """The configured data-quality gate, or the documented default."""
    if settings_data is None:
        try:
            from config.settings_manager import settings

            settings_data = settings.data
        except Exception:
            return DEFAULT_MINIMUM_COVERAGE_PERCENT
    try:
        value = float((settings_data or {}).get(
            COVERAGE_SETTING_KEY, DEFAULT_MINIMUM_COVERAGE_PERCENT))
    except (TypeError, ValueError):
        return DEFAULT_MINIMUM_COVERAGE_PERCENT
    return value if 0.0 <= value <= 100.0 else DEFAULT_MINIMUM_COVERAGE_PERCENT


@dataclass(frozen=True)
class UniverseCoverage:
    """What a scan actually covered, in the operator's terms."""

    expected_session: str
    universe_total: int
    history_loaded: int
    current: int
    stale: int
    unavailable: int
    distribution: dict = field(default_factory=dict)
    threshold_percent: float = DEFAULT_MINIMUM_COVERAGE_PERCENT
    threshold_source: str = COVERAGE_SETTING_KEY

    @property
    def current_percent(self) -> float:
        if not self.universe_total:
            return 0.0
        return round(100.0 * self.current / self.universe_total, 1)

    @property
    def complete(self) -> bool:
        return self.universe_total > 0 and self.current == self.universe_total

    @property
    def market_wide_allowed(self) -> bool:
        """Whether market-level aggregates may be stated.

        Symbol-level decisions are never gated by this: a current symbol is
        current whatever the rest of the exchange is doing.
        """
        return self.current_percent >= float(self.threshold_percent)

    @property
    def market_regime_label(self) -> str:
        return "" if self.market_wide_allowed else INSUFFICIENT_CURRENT_COVERAGE

    def blocked_message(self) -> str:
        return (
            "MARKET-WIDE SUMMARY BLOCKED\n\n"
            f"Current daily-data coverage is {self.current}/{self.universe_total} "
            f"({self.current_percent:.1f}%).\n"
            "Symbol-level results are available only for current symbols."
        )

    def partial_message(self) -> str:
        return (
            "PARTIAL DAILY DATA COVERAGE\n\n"
            f"Only symbols with candles from {self.expected_session} were analyzed.\n"
            "Stale symbols were excluded from BUY/WATCH/AVOID decisions."
        )

    def as_dict(self) -> dict:
        return {
            "expected_session": self.expected_session,
            "universe_total": self.universe_total,
            "history_loaded": self.history_loaded,
            "current": self.current,
            "stale": self.stale,
            "unavailable": self.unavailable,
            "current_percent": self.current_percent,
            "session_distribution": dict(self.distribution),
            "market_wide_allowed": self.market_wide_allowed,
            "coverage_threshold_percent": float(self.threshold_percent),
            "coverage_threshold_setting": self.threshold_source,
        }


def summarize_universe_coverage(results, expected_session, universe_total=None,
                                settings_data=None):
    """Build the coverage panel from typed per-symbol freshness results."""

    results = list(results or [])
    distribution = {}
    current = stale = unavailable = 0
    for item in results:
        # A distribution OF OBSERVED SESSIONS. A symbol with no candle has not
        # observed one, so it belongs in the unavailable count below and not in
        # a session bucket: an "unknown" bucket both broke the archive's
        # distribution invariant and could win the dominant-session vote,
        # printing "Unknown - no dated candle" over a run full of dated rows.
        date = item.actual_latest_session
        if date:
            distribution[date] = distribution.get(date, 0) + 1
        if item.freshness_status is SymbolFreshness.CURRENT:
            current += 1
        elif item.freshness_status is SymbolFreshness.STALE:
            stale += 1
        else:
            unavailable += 1
    return UniverseCoverage(
        expected_session=str(expected_session or "")[:10],
        universe_total=int(universe_total if universe_total is not None else len(results)),
        history_loaded=len(results),
        current=current,
        stale=stale,
        unavailable=unavailable,
        distribution=dict(sorted(distribution.items())),
        threshold_percent=minimum_coverage_percent(settings_data),
    )


__all__ = [
    "BLOCKING_VERDICTS",
    "COVERAGE_SETTING_KEY",
    "DEFAULT_MINIMUM_COVERAGE_PERCENT",
    "INSUFFICIENT_CURRENT_COVERAGE",
    "ELIGIBLE_STATUSES",
    "FRESHNESS_OUTCOME",
    "RUBIX_STALE_HEADLINE",
    "DailyDataDecision",
    "DailyDataVerdict",
    "QuoteFreshness",
    "SymbolFreshness",
    "SymbolFreshnessResult",
    "UniverseCoverage",
    "classify_symbol_freshness",
    "minimum_coverage_percent",
    "summarize_universe_coverage",
    "evaluate_daily_data",
    "evaluate_quote_freshness",
]
