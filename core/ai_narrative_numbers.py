"""Field-aware EXACT numeric allow-list for narrative text.

A narrative may restate an evidence number; it may never state a different one. This
module decides that question by string identity, not by numeric proximity — there is **no
absolute epsilon and no relative tolerance anywhere in this file**. A tolerance of any size
is unsafe on low-priced EGX securities: with a 0.05 epsilon a stock evidenced at 1.84 would
accept "1.89", a 2.7% error.

Instead, every typed evidence value is expanded into the finite set of textual forms that
are *approved renderings of that exact value*:

  * every decimal rendering from 0 to 6 places that preserves the value exactly
    (this yields the trailing-zero family for free: 1.84 → 1.84, 1.840, 1.8400 …);
  * the one canonical display precision for that field's kind (price/indicator 2 dp,
    percent 2 dp, ratio 2 dp, score/volume/count 0 dp) — the same rendering the UI and the
    deterministic writer already print;
  * for a 0..1 unit ratio (scenario confidence, level strength), the ×100 form the UI
    itself displays as ``55 / 100``;
  * Arabic-Indic digits, Arabic decimal/thousands separators, Western thousands
    separators, a leading ``+``, and the Unicode minus — all handled by normalizing text
    tokens and approved renderings to one canonical key before comparison.

Consequences, all intended:

  * 1.84 accepts 1.84 / 1.8400 / ١٫٨٤ and rejects 1.80, 1.85 and 1.89;
  * 97.80 rejects 97.85; 91.00 rejects 91.05; 15.37% rejects 15.42%;
  * a value can never match an unrelated figure because a large volume or turnover
    happens to be numerically nearby — nothing is compared numerically at all;
  * a negative indicator value approves only its own sign.

Numbers embedded in auditable evidence STRINGS (level bases, machine reasons, session
dates, timestamps) stay traceable: those strings are tokenized with the same tokenizer and
their tokens are approved verbatim, so a restated session date is not a hallucination.

A percent sign is gated separately: a token written as a percentage is approved only when
it came from a percentage-typed field (change/distance/remaining-room/spread), a 0..100
score, or a source string where that number was itself followed by a percent sign.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from core.ai_stock_analysis_contract import AnalysisResult

# Structural constants a narrative may reference without them being market values:
# indicator periods and the 0-100 percentage/confidence scale.
STRUCTURAL_NUMBERS = (0, 9, 12, 14, 20, 26, 50, 100, 200)

MAX_DECIMALS = 6

# Field kinds and the single canonical display precision each one is printed with.
KIND_PRICE = "PRICE"
KIND_PERCENT = "PERCENT"
KIND_RATIO = "RATIO"
KIND_UNIT_RATIO = "UNIT_RATIO"       # 0..1, displayed by the UI as N / 100
KIND_INDICATOR = "INDICATOR"
KIND_SCORE = "SCORE"                 # 0..100
KIND_VOLUME = "VOLUME"
KIND_COUNT = "COUNT"

_CANONICAL_DP = {
    KIND_PRICE: 2, KIND_PERCENT: 2, KIND_RATIO: 2, KIND_UNIT_RATIO: 2,
    KIND_INDICATOR: 2, KIND_SCORE: 0, KIND_VOLUME: 0, KIND_COUNT: 0,
}

# Kinds whose values may legitimately be written with a percent sign.
_PERCENT_KINDS = frozenset({KIND_PERCENT, KIND_SCORE, KIND_UNIT_RATIO})

# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #

_DIGIT_MAP = {ord("٠") + i: str(i) for i in range(10)}          # ٠-٩
_DIGIT_MAP.update({ord("۰") + i: str(i) for i in range(10)})    # ۰-۹
_CHAR_MAP = {
    ord("٫"): ".",   # Arabic decimal separator
    ord("٬"): ",",   # Arabic thousands separator
    ord("،"): ",",   # Arabic comma
    ord("٪"): "%",   # Arabic percent sign
    ord("−"): "-",   # minus sign
    0x200E: " ", 0x200F: " ",   # bidi marks
}


def normalize_text(text) -> str:
    """Render Arabic numerals/separators in Western form for tokenizing."""
    return unicodedata.normalize("NFKC", str(text or "")).translate(
        {**_DIGIT_MAP, **_CHAR_MAP})


# A number token: optional sign, digits with optional thousands groups, optional decimals.
_TOKEN_RE = re.compile(r"[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[-+]?\d+(?:\.\d+)?")
_PERCENT_AFTER_RE = re.compile(r"\s*%")


def canonical_key(token: str) -> str:
    """One canonical key per written number: no separators, no leading ``+``.

    Digits and the decimal point are preserved exactly — ``1.84`` and ``1.840`` stay
    distinct keys, and both are generated for a value of 1.84, while ``1.80`` is not
    generated for it at all.
    """
    text = str(token).strip().replace(",", "").replace(" ", "")
    if text.startswith("+"):
        text = text[1:]
    if text.startswith("-") and set(text[1:]) <= set("0."):    # -0, -0.00 → 0 form
        text = text[1:]
    return text


# --------------------------------------------------------------------------- #
# Approved renderings of one value
# --------------------------------------------------------------------------- #

def _exactly_representable(value: float, dp: int) -> bool:
    """True when rendering ``value`` at ``dp`` decimals loses nothing."""
    return abs(round(value, dp) - value) < 1e-9


def renderings(value, kind: str) -> set[str]:
    """Every approved textual rendering of ``value`` for a field of ``kind``."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return set()
    if number != number or number in (float("inf"), float("-inf")):   # NaN / inf
        return set()

    out: set[str] = set()
    for dp in range(0, MAX_DECIMALS + 1):
        if _exactly_representable(number, dp):
            out.add(canonical_key(f"{number:.{dp}f}"))
    # The canonical display precision is approved even when it rounds: it is the rendering
    # the UI and the deterministic writer already print for this same field.
    canonical_dp = _CANONICAL_DP.get(kind, 2)
    out.add(canonical_key(f"{number:.{canonical_dp}f}"))
    if kind == KIND_UNIT_RATIO:
        # The UI renders a 0..1 confidence/strength as "N / 100"; both forms are approved.
        scaled = number * 100.0
        out.add(canonical_key(f"{scaled:.0f}"))
        if _exactly_representable(scaled, 2):
            out.add(canonical_key(f"{scaled:.2f}"))
    return out


# --------------------------------------------------------------------------- #
# The allow-list
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class NumberAllowlist:
    """Approved number keys for one ``AnalysisResult``. Membership is exact."""
    approved: frozenset = frozenset()
    percent_approved: frozenset = frozenset()

    def permits(self, token: str, *, as_percent: bool = False) -> bool:
        key = canonical_key(token)
        if key not in self.approved:
            return False
        return (key in self.percent_approved) if as_percent else True

    def offending(self, text: str) -> list[str]:
        """Return the written numbers in ``text`` that the evidence does not support."""
        normalized = normalize_text(text)
        found: list[str] = []
        for match in _TOKEN_RE.finditer(normalized):
            as_percent = bool(_PERCENT_AFTER_RE.match(normalized, match.end()))
            if not self.permits(match.group(0), as_percent=as_percent):
                token = match.group(0) + ("%" if as_percent else "")
                if token not in found:
                    found.append(token)
        return found


@dataclass
class _Builder:
    approved: set = field(default_factory=set)
    percent_approved: set = field(default_factory=set)

    def value(self, raw, kind: str) -> None:
        if raw is None or isinstance(raw, bool):
            return
        keys = renderings(raw, kind)
        self.approved |= keys
        if kind in _PERCENT_KINDS:
            self.percent_approved |= keys

    def values(self, pairs) -> None:
        for raw, kind in pairs:
            self.value(raw, kind)

    def text(self, raw) -> None:
        """Approve the numbers written inside an auditable evidence string, verbatim."""
        if not raw:
            return
        normalized = normalize_text(raw)
        for match in _TOKEN_RE.finditer(normalized):
            key = canonical_key(match.group(0))
            variants = {key, key.lstrip("-")}
            unsigned = key.lstrip("-")
            if unsigned.startswith("0") and unsigned.strip("0."):
                variants.add(unsigned.lstrip("0") or "0")       # 07 (a month) → 7
            self.approved |= variants
            if _PERCENT_AFTER_RE.match(normalized, match.end()):
                self.percent_approved |= variants

    def texts(self, values) -> None:
        for value in values or ():
            self.text(value)


def build_number_allowlist(result: AnalysisResult) -> NumberAllowlist:
    """Build the exact allow-list of numbers a narrative for ``result`` may write."""
    builder = _Builder()
    for constant in STRUCTURAL_NUMBERS:
        builder.value(constant, KIND_COUNT)

    price = result.price
    builder.values((
        (price.close, KIND_PRICE), (price.previous_close, KIND_PRICE),
        (price.open, KIND_PRICE), (price.high, KIND_PRICE), (price.low, KIND_PRICE),
        (price.last, KIND_PRICE), (price.bid, KIND_PRICE), (price.ask, KIND_PRICE),
        (price.change_amount, KIND_PRICE),
        (price.change_percent, KIND_PERCENT), (price.spread_percent, KIND_PERCENT),
        (price.volume, KIND_VOLUME), (price.turnover, KIND_VOLUME),
    ))
    builder.texts((price.session_date, price.quote_timestamp))

    ind = result.indicators
    builder.values((
        (ind.sma_20, KIND_PRICE), (ind.sma_50, KIND_PRICE), (ind.sma_200, KIND_PRICE),
        (ind.ema_20, KIND_PRICE), (ind.ema_50, KIND_PRICE), (ind.ema_200, KIND_PRICE),
        (ind.atr_14, KIND_PRICE),
        (ind.rsi_14, KIND_INDICATOR), (ind.macd, KIND_INDICATOR),
        (ind.macd_signal, KIND_INDICATOR), (ind.macd_histogram, KIND_INDICATOR),
        (ind.trend_strength, KIND_SCORE), (ind.momentum_strength, KIND_SCORE),
        (ind.expected_range_position, KIND_SCORE),
        (ind.average_volume_20, KIND_VOLUME), (ind.obv, KIND_VOLUME),
        (ind.turnover, KIND_VOLUME),
        (ind.volume_ratio, KIND_RATIO),
        (ind.computed_from_sessions, KIND_COUNT),
    ))
    builder.text(ind.latest_action_in_lookback)

    for level in result.key_levels:
        builder.values((
            (level.price, KIND_PRICE),
            (level.distance_percent, KIND_PERCENT),
            (level.strength, KIND_UNIT_RATIO), (level.confidence, KIND_UNIT_RATIO),
            (level.touches, KIND_COUNT),
        ))
        builder.texts((level.basis, level.last_touch_date, level.timeframe))

    for scenario in result.scenarios:
        builder.values((
            (scenario.trigger, KIND_PRICE), (scenario.entry_low, KIND_PRICE),
            (scenario.entry_high, KIND_PRICE), (scenario.target, KIND_PRICE),
            (scenario.stop, KIND_PRICE),
            (scenario.remaining_room_percent, KIND_PERCENT),
            (scenario.risk_reward, KIND_RATIO),
            (scenario.confidence, KIND_UNIT_RATIO),
        ))
        builder.texts(scenario.confirmation_requirements)
        builder.texts(scenario.invalidation_conditions)
        builder.texts((scenario.title,))

    confidence = result.confidence
    builder.value(confidence.overall, KIND_SCORE)
    for component in confidence.components:
        builder.value(component.score, KIND_SCORE)
        builder.value(component.weight, KIND_UNIT_RATIO)

    quality = result.data_quality
    builder.texts((quality.latest_completed_session, quality.expected_completed_session))
    builder.texts(quality.notes)
    builder.texts(result.recommendation_reasons)
    builder.text(result.generated_at)
    builder.text(result.request.as_of)

    return NumberAllowlist(approved=frozenset(builder.approved),
                           percent_approved=frozenset(builder.percent_approved))


def validate_numbers(text: str, allowlist: NumberAllowlist) -> tuple[bool, list[str]]:
    """Return ``(ok, offending_tokens)`` for one narrative field."""
    offending = allowlist.offending(text)
    return (not offending, offending)
