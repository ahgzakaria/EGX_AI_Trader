"""Presentation-only formatting + label helpers for the trading UI.

CRITICAL: these functions format values for DISPLAY only. They never mutate,
round-trip, or feed back into any strategy calculation. The underlying numeric
data is always kept intact; only the rendered string changes.
"""

from __future__ import annotations

import math

from core.universe import _is_missing as _missing_symbol
from core.universe import canonical as canonical_symbol
from core.universe import company_name as universe_company_name
from core.universe import display_label as universe_display_label

EM_DASH = "—"

# --- symbol identity --------------------------------------------------------

#: Arabic table headers. Ticker and company name are ALWAYS separate columns so
#: filtering, sorting and exporting stay usable; only dropdowns combine them.
SYMBOL_COLUMN = "الرمز"
NAME_COLUMN = "اسم السهم"


def symbol_option_label(symbol) -> str:
    """Dropdown label: ``COMI — Commercial International Bank-Egypt (CIB)``.

    The SELECTED value stays the canonical ticker, so strategy code is unaffected.
    A ticker that has left the active universe renders its archived name, or
    ``TICKER — Historical / Inactive Symbol`` when no name was ever recorded.
    """

    return universe_display_label(symbol)


def symbol_ticker(symbol) -> str:
    """The canonical ticker behind any accepted spelling or display label.

    Missing values (None / NaN / pandas NA) yield ``""``. The literal ticker
    ``NULL`` is a real EGX symbol and survives unchanged.
    """

    if _missing_symbol(symbol):
        return ""
    text = str(symbol)
    if EM_DASH in text:
        text = text.split(EM_DASH, 1)[0]
    return canonical_symbol(text)


def company_name(symbol) -> str:
    """Full company name for a table cell; archived symbols keep their name."""

    ticker = symbol_ticker(symbol)
    if not ticker:
        return EM_DASH
    from core.universe import INACTIVE_NAME, lookup

    record = lookup(ticker)
    if record is None:
        return INACTIVE_NAME
    return record.company_name or INACTIVE_NAME


def company_names(symbols) -> list:
    return [company_name(symbol) for symbol in symbols]


def with_company_name_column(frame, symbol_column, name_column=NAME_COLUMN):
    """Return a copy of ``frame`` with a company-name column beside the ticker.

    Presentation only: the ticker column is untouched, so any consumer that
    filters, groups or exports on the symbol keeps working unchanged.
    """

    if frame is None or getattr(frame, "empty", True):
        return frame
    if symbol_column not in frame.columns:
        return frame
    out = frame.copy()
    out[name_column] = [company_name(value) for value in out[symbol_column]]
    order = list(out.columns)
    order.remove(name_column)
    order.insert(order.index(symbol_column) + 1, name_column)
    return out[order]


# --- numbers ----------------------------------------------------------------


def _is_missing(x) -> bool:
    if x is None:
        return True
    try:
        return isinstance(x, float) and math.isnan(x)
    except (TypeError, ValueError):
        return False


def fmt_compact(x, decimals=2) -> str:
    """1,234 → '1.23K', 6,346,629 → '6.35M', 1.08e9 → '1.08B'."""
    if _is_missing(x):
        return EM_DASH
    try:
        n = float(x)
    except (TypeError, ValueError):
        return str(x)
    sign = "-" if n < 0 else ""
    n = abs(n)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= div:
            return f"{sign}{n / div:.{decimals}f}{suf}"
    if n == int(n):
        return f"{sign}{int(n)}"
    return f"{sign}{n:.{decimals}f}"


def fmt_volume(x):
    """Compact traded-share volume (e.g. 928,858 → '928.9K')."""
    return fmt_compact(x, decimals=1)


def fmt_turnover(x):
    """Compact EGP turnover (e.g. 56,330,152 → '56.3M EGP')."""
    if _is_missing(x):
        return EM_DASH
    return f"{fmt_compact(x, decimals=2)} EGP"


def fmt_percent(x, decimals=2) -> str:
    """A percentage value already expressed in percent units (4.4711 → '4.47%')."""
    if _is_missing(x):
        return EM_DASH
    try:
        return f"{float(x):.{decimals}f}%"
    except (TypeError, ValueError):
        return str(x)


def fmt_frequency(x) -> str:
    """A 0..1 frequency → percent (1.0 → '100%', 0.85 → '85%')."""
    if _is_missing(x):
        return EM_DASH
    try:
        return f"{round(float(x) * 100)}%"
    except (TypeError, ValueError):
        return str(x)


def fmt_price(x) -> str:
    """Symbol-appropriate price precision (penny shares keep more decimals)."""
    if _is_missing(x):
        return EM_DASH
    try:
        n = float(x)
    except (TypeError, ValueError):
        return str(x)
    a = abs(n)
    if a == 0:
        return "0.00"
    if a < 1:
        return f"{n:.3f}"
    if a < 100:
        return f"{n:.2f}"
    return f"{n:,.2f}"


def fmt_score(x) -> str:
    """Score with no needless decimals: 90.0 → '90', 88.5 → '88.5', missing → em dash.

    Display only — never changes the stored score used for ranking.
    """
    if _is_missing(x):
        return EM_DASH
    try:
        n = float(x)
    except (TypeError, ValueError):
        return str(x)
    if abs(n - round(n)) < 0.05:
        return str(int(round(n)))
    return f"{n:.1f}"


def fmt_range(low, high) -> str:
    if _is_missing(low) or _is_missing(high):
        return EM_DASH
    return f"{fmt_price(low)} – {fmt_price(high)}"


def dash_if_missing(x) -> str:
    return EM_DASH if _is_missing(x) else str(x)


# --- status / scenario labels + tones ---------------------------------------

# tone -> semantic colour class used by the badge renderer
GREEN, AMBER, RED, GRAY, BLUE = "green", "amber", "red", "gray", "blue"

# Advisory / status -> (short human label, tone). The full technical string is
# always preserved for the tooltip / drawer.
_STATUS_LABELS = {
    "READY_LOWER_RANGE_BOUNCE": ("Ready: Lower Bounce", GREEN),
    "READY_DIP_RECLAIM": ("Ready: Dip Reclaim", GREEN),
    "READY_CONTINUATION": ("Ready: Continuation", GREEN),
    "READY_BREAKOUT_RETEST": ("Ready: Breakout Retest", GREEN),
    "READY_GAP_RECOVERY": ("Ready: Gap Recovery", GREEN),
    "READY": ("Ready", GREEN),
    "BOUNCE_READY": ("Ready: Lower Bounce", GREEN),
    "WAIT_LOWER_RANGE_BOUNCE": ("Waiting: Lower Bounce", AMBER),
    "BOUNCE_WAIT": ("Waiting", AMBER),
    "WATCH_HIGH_VOLUME_VOLATILITY": ("Watch", AMBER),
    "LOWER_RANGE_NOT_REACHED": ("Not Reached", AMBER),
    "FALLING_WITHOUT_CONFIRMATION": ("Falling (unconfirmed)", AMBER),
    "RANGE_CONSUMED": ("No Chase", GRAY),
    "NO_CHASE_RANGE_CONSUMED": ("No Chase", GRAY),
    "TARGET_ROOM_INSUFFICIENT": ("Target Room Low", GRAY),
    "NO_TRADE": ("No Trade", GRAY),
    "DATA_STALE": ("Data Stale", RED),
    "DATA_INSUFFICIENT": ("Data Insufficient", RED),
    "SPREAD_TOO_WIDE": ("Wide Spread", RED),
    "LIQUIDITY_TOO_LOW": ("Low Liquidity", RED),
    "INVALID": ("Invalid", RED),
    "LIQUIDITY_VALID": ("Liquid", GREEN),
    "LIQUIDITY_LIMITED": ("Limited", AMBER),
    "OK": ("OK", GREEN),
    "HISTORY_CURRENT": ("Current", GREEN),
    "TODAY_CANDLE_NOT_YET_COMPLETE": ("Current", GREEN),
    "PROVIDER_FINALIZATION_PENDING": ("Pending Publish", BLUE),
    "MISSING": ("Missing", RED),
}

# Canonical scenario id -> short label (for the "Best Scenario" column / drawer).
_SCENARIO_LABELS = {
    "EXPECTED_LOWER_RANGE_BOUNCE": "Lower Bounce",
    "DIP_AND_RECLAIM": "Dip Reclaim",
    "TREND_CONTINUATION_INSIDE_RANGE": "Continuation",
    "TREND_CONTINUATION": "Continuation",
    "EXPECTED_RANGE_BREAKOUT_AND_RETEST": "Breakout Retest",
    "EXPECTED_RANGE_BREAKOUT_RETEST": "Breakout Retest",
    "GAP_UP_WITH_REMAINING_ROOM": "Gap-Up Room",
    "GAP_UP_REMAINING_ROOM": "Gap-Up Room",
    "GAP_DOWN_RECOVERY": "Gap-Down Recovery",
    "NO_CHASE_RANGE_CONSUMED": "No Chase",
    "RANGE_CONSUMED_NO_CHASE": "No Chase",
}


# Fuller, decision-support scenario names for the Opportunities page (never a raw
# "BUY"). The compact _SCENARIO_LABELS above stay for narrow dashboard tables.
_SCENARIO_FULL_LABELS = {
    "EXPECTED_LOWER_RANGE_BOUNCE": "Lower Range Bounce",
    "DIP_AND_RECLAIM": "Dip and Reclaim",
    "TREND_CONTINUATION_INSIDE_RANGE": "Trend Continuation",
    "TREND_CONTINUATION": "Trend Continuation",
    "EXPECTED_RANGE_BREAKOUT_AND_RETEST": "Breakout Retest",
    "EXPECTED_RANGE_BREAKOUT_RETEST": "Breakout Retest",
    "GAP_UP_WITH_REMAINING_ROOM": "Gap-Up Continuation",
    "GAP_UP_REMAINING_ROOM": "Gap-Up Continuation",
    "GAP_DOWN_RECOVERY": "Gap-Down Recovery",
    "NO_CHASE_RANGE_CONSUMED": "No Chase (Range Consumed)",
    "RANGE_CONSUMED_NO_CHASE": "No Chase (Range Consumed)",
}


def scenario_full_label(name) -> str:
    """Decision-support scenario name for the Opportunities page (full, human)."""
    if _is_missing(name) or name == "":
        return EM_DASH
    return _SCENARIO_FULL_LABELS.get(str(name), _titleize(name))


# Paper outcome (durable signal + matured outcome) -> (short label, tone). Never
# implies execution — these describe a RECORDED paper signal only.
_PAPER_OUTCOME_LABELS = {
    "TARGET": ("Target First", GREEN),
    "STOP": ("Stop First", RED),
    "NEITHER": ("Neither", GRAY),
    "PENDING": ("Outcome Pending", AMBER),
    "MATURED": ("Outcome Matured", BLUE),
    "INVALID": ("Invalid Outcome", RED),
    "RECORDED": ("Signal Recorded", BLUE),
    "NONE": ("No Paper Signal", GRAY),
}


def paper_outcome_label(key) -> tuple:
    """(label, tone) for a paper signal/outcome key; unknown -> No Paper Signal."""
    return _PAPER_OUTCOME_LABELS.get(str(key).upper(), _PAPER_OUTCOME_LABELS["NONE"])


# Durable scenario states that count as an INVALIDATION (was watching/ready, then
# became not-actionable). Kept as data only; the original signal is never rewritten.
_INVALIDATION_LABELS = {
    "INVALID": "Support/Setup Failed",
    "NO_TRADE": "No Trade",
    "DATA_STALE": "Quote Became Stale",
    "DATA_INSUFFICIENT": "Data Insufficient",
    "SPREAD_TOO_WIDE": "Spread Widened",
    "LIQUIDITY_TOO_LOW": "Liquidity Dropped",
    "RANGE_CONSUMED": "Range Consumed",
}
INVALIDATION_STATES = set(_INVALIDATION_LABELS)


def invalidation_label(to_state, note="") -> str:
    """Human invalidation reason for a durable transition (note wins when present)."""
    if str(note).strip() == "NO_NEW_ENTRY_AFTER_1415":
        return "Session Phase Ended (auction)"
    return _INVALIDATION_LABELS.get(str(to_state), _titleize(to_state))


def status_label(status) -> str:
    if _is_missing(status) or status == "":
        return EM_DASH
    return _STATUS_LABELS.get(str(status), (_titleize(status), GRAY))[0]


def status_tone(status) -> str:
    if _is_missing(status):
        return GRAY
    return _STATUS_LABELS.get(str(status), (None, GRAY))[1]


def scenario_label(name) -> str:
    if _is_missing(name) or name == "":
        return EM_DASH
    return _SCENARIO_LABELS.get(str(name), _titleize(name))


def _titleize(s) -> str:
    return str(s).replace("_", " ").title()


# Scenario STATE (the scenario disposition) — kept separate from data quality.
_STATE_LABELS = {
    "READY_LOWER_RANGE_BOUNCE": ("Ready", GREEN), "READY_DIP_RECLAIM": ("Ready", GREEN),
    "READY_CONTINUATION": ("Ready", GREEN), "READY_BREAKOUT_RETEST": ("Ready", GREEN),
    "READY_GAP_RECOVERY": ("Ready", GREEN),
    "WAIT_LOWER_RANGE_BOUNCE": ("Waiting", AMBER), "WATCH_HIGH_VOLUME_VOLATILITY": ("Watch", AMBER),
    "RANGE_CONSUMED": ("Consumed", GRAY), "NO_TRADE": ("No Trade", GRAY),
    # data-blocked advisories carry no scenario state (data quality explains it)
    "DATA_STALE": ("—", GRAY), "DATA_INSUFFICIENT": ("—", GRAY),
    "SPREAD_TOO_WIDE": ("—", GRAY), "LIQUIDITY_TOO_LOW": ("—", GRAY),
}


def scenario_state_label(advisory) -> str:
    if _is_missing(advisory) or advisory == "":
        return EM_DASH
    return _STATE_LABELS.get(str(advisory), ("Waiting", AMBER))[0]


def scenario_state_tone(advisory) -> str:
    if _is_missing(advisory):
        return GRAY
    return _STATE_LABELS.get(str(advisory), (None, AMBER))[1]


def is_valid_price(x) -> bool:
    """A live price is valid only when it is a real, positive number."""
    try:
        return x is not None and not (isinstance(x, float) and math.isnan(x)) and float(x) > 0
    except (TypeError, ValueError):
        return False


def fmt_live_price(x) -> str:
    """Format a live price; a missing/NaN/zero value is em-dash, never '0.00'."""
    return fmt_price(x) if is_valid_price(x) else EM_DASH


def data_quality_label(*, data_status=None, hist_stale=False, advisory=None,
                       phase="CONTINUOUS", last_valid=True) -> tuple:
    """(label, tone) — the DATA-QUALITY dimension, session-aware, independent of state.

    After market close a quote is a last-session snapshot, never 'Live Quote Stale'
    merely because time has passed; a genuine history issue is flagged in every phase.
    """
    if str(data_status) == "MISSING":
        return ("No History", RED)
    if bool(hist_stale) or str(data_status) == "DATA_STALE":
        return ("History Lag", AMBER)
    if str(advisory) == "SPREAD_TOO_WIDE":
        return ("Wide Spread", RED)
    if not last_valid:
        return ("Live Data Missing", RED) if phase == "CONTINUOUS" else ("Last Price Unavailable", RED)
    if phase == "AUCTION":
        return ("Auction Snapshot", BLUE)
    if phase != "CONTINUOUS":                       # CLOSED / PRE_OPEN
        return ("Last Session Snapshot", BLUE)
    # continuous session
    if str(advisory) == "DATA_STALE":
        return ("Live Quote Stale", AMBER)
    if str(data_status) == "DATA_INSUFFICIENT":
        return ("Data Insufficient", AMBER)
    return ("Current", GREEN)


# Compact table variants of long data-quality labels. The FULL label + the
# original quote timestamp are always preserved in the tooltip / drawer / details.
_DQ_COMPACT = {
    "Last Session Snapshot": "Last Snapshot",
}


def dq_compact(label) -> str:
    """Shorten a data-quality label for a narrow table cell (full text kept elsewhere)."""
    return _DQ_COMPACT.get(str(label), str(label))


def range_zone(position_percent):
    """Semantic zone label for a range-position % (0..100, can exceed)."""
    if _is_missing(position_percent):
        return ("unknown", GRAY)
    p = float(position_percent)
    if p < 0:
        return ("below range", BLUE)
    if p <= 25:
        return ("lower zone", GREEN)
    if p <= 75:
        return ("middle zone", AMBER)
    if p <= 100:
        return ("upper zone", AMBER)
    return ("above range / extension", RED)
