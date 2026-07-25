"""Validation gate between an external model's answer and anything a user ever sees.

Nothing an external provider returns is trusted. A response is accepted only when it
passes every check below; otherwise the whole narrative is rejected — never partially
repaired, never partially displayed — and the caller falls back to the deterministic
narrative.

  * **Schema** — a JSON object with exactly the eight required Arabic sections, each a
    non-empty, length-capped string.
  * **Format** — no markdown tables, no pipes, no code fences, no HTML.
  * **Numeric traceability** — every numeric token must be an approved *textual rendering*
    of a typed evidence value (see :mod:`core.ai_narrative_numbers`). Matching is exact
    string identity on a normalized key: there is **no absolute and no relative
    tolerance**, so a stock evidenced at 1.84 rejects 1.85, 1.89 and 1.80, and nothing can
    ever "match" a large volume or turnover value by proximity.
  * **Wording** — no unconditional buy/sell/enter/size command, no certainty claim.

Rejection reasons are safe by construction: they name a rule and, for numeric failures,
the offending numeric tokens only. Rejected prose is never included, logged, or returned.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from core.ai_narrative_numbers import (
    build_number_allowlist,
    normalize_text,
    validate_numbers,
)
from core.ai_narrative_prompt import REQUIRED_SECTIONS
from core.ai_stock_analysis_contract import AnalysisResult

# Validation statuses recorded in history and shown (as a short token) in the UI.
VALIDATED = "VALIDATED"
EMPTY_RESPONSE = "EMPTY_RESPONSE"
SCHEMA_INVALID = "SCHEMA_INVALID"
FORMAT_VIOLATION = "FORMAT_VIOLATION"
NUMERIC_HALLUCINATION = "NUMERIC_HALLUCINATION"
FORBIDDEN_RECOMMENDATION = "FORBIDDEN_RECOMMENDATION"

MAX_SECTION_CHARS = 700
MAX_TOTAL_CHARS = 4000

# --------------------------------------------------------------------------- #
# Numeral / script normalization
# --------------------------------------------------------------------------- #

def normalize_numerals(text: str) -> str:
    """Render Arabic numerals/separators in Western form (see ai_narrative_numbers)."""
    return normalize_text(text)


def _normalize_arabic(text: str) -> str:
    """Fold diacritics, tatweel and letter variants so wording rules cannot be evaded."""
    folded = unicodedata.normalize("NFKD", str(text or ""))
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = folded.replace("ـ", "")
    for source, target in (("أإآٱ", "ا"), ("ى", "ي"), ("ة", "ه"), ("ؤ", "و"), ("ئ", "ي")):
        for ch in source:
            folded = folded.replace(ch, target)
    return re.sub(r"\s+", " ", folded)


# --------------------------------------------------------------------------- #
# Prohibited wording
# --------------------------------------------------------------------------- #

# Patterns are matched against Arabic-normalized text. Each entry is (rule_id, regex).
# They target direct orders and certainty claims only — ordinary market vocabulary such as
# "ضغط بيع" (selling pressure) or "منطقه شراء" (buying zone) stays allowed, while an
# imperative ("اشترِ الآن", "بيع فورًا", "ادخل بكل السيولة", "ضاعف مركزك") is rejected.
_FORBIDDEN_PATTERNS = (
    ("imperative_buy", r"\bاشتر[ي]?\b"),
    ("imperative_sell", r"\bبع\b|\bبيع\s+(?:فورا|الان|حالا|علي\s+الفور)"),
    ("imperative_enter", r"\bادخل\b|\bاخرج\b"),
    ("all_in", r"بكل\s+(?:السيوله|راس\s*المال|الرصيد)|كل\s+سيولتك"),
    ("size_up", r"\bضاعف\b|\bزود\s+مركزك\b|\bزد\s+مركزك\b"),
    ("execute_now", r"نفذ\s+(?:الان|الصفقه|الامر)|ارسل\s+امر"),
    ("certainty", r"\bمضمون\b|\bمضمونه\b|\bلا\s+شك\b|\bحتما\b|\bبالتاكيد\s+سي"),
)
_FORBIDDEN = tuple((rule, re.compile(pattern)) for rule, pattern in _FORBIDDEN_PATTERNS)

# Markup that must never reach the card or the page.
_FORMAT_PATTERNS = (
    ("markdown_table_or_pipe", re.compile(r"\|")),
    ("code_fence", re.compile(r"```|~~~")),
    ("html_tag", re.compile(r"<[a-zA-Z/!][^>]*>")),
)


@dataclass(frozen=True)
class ValidationReport:
    """Outcome of validating one provider response. ``sections`` is empty on failure."""
    ok: bool
    status: str
    reason: str = ""
    sections: dict = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return self.ok


def _reject(status: str, reason: str) -> ValidationReport:
    return ValidationReport(ok=False, status=status, reason=reason)


def check_forbidden_wording(text: str) -> str | None:
    """Return the rule id of the first prohibited phrase found, else ``None``."""
    normalized = _normalize_arabic(text)
    for rule, pattern in _FORBIDDEN:
        if pattern.search(normalized):
            return rule
    return None


def check_format(text: str) -> str | None:
    """Return the rule id of the first prohibited markup found, else ``None``."""
    for rule, pattern in _FORMAT_PATTERNS:
        if pattern.search(text):
            return rule
    return None


def validate_response(payload, result: AnalysisResult) -> ValidationReport:
    """Validate a decoded provider response against the evidence. All-or-nothing."""
    if payload is None:
        return _reject(EMPTY_RESPONSE, "provider returned no content")
    if not isinstance(payload, dict):
        return _reject(SCHEMA_INVALID, "response was not a JSON object")

    sections: dict[str, str] = {}
    for name in REQUIRED_SECTIONS:
        value = payload.get(name)
        if not isinstance(value, str) or not value.strip():
            return _reject(SCHEMA_INVALID, f"missing or empty section: {name}")
        text = value.strip()
        if len(text) > MAX_SECTION_CHARS:
            return _reject(SCHEMA_INVALID, f"section too long: {name}")
        sections[name] = text

    unexpected = sorted(set(payload) - set(REQUIRED_SECTIONS))
    if unexpected:
        return _reject(SCHEMA_INVALID,
                       f"unexpected keys: {','.join(unexpected[:5])}")

    if sum(len(v) for v in sections.values()) > MAX_TOTAL_CHARS:
        return _reject(SCHEMA_INVALID, "response exceeded the total length budget")

    allowlist = build_number_allowlist(result)
    for name, text in sections.items():
        rule = check_format(text)
        if rule is not None:
            return _reject(FORMAT_VIOLATION, f"{rule} in {name}")
        rule = check_forbidden_wording(text)
        if rule is not None:
            return _reject(FORBIDDEN_RECOMMENDATION, f"{rule} in {name}")
        ok, offending = validate_numbers(text, allowlist)
        if not ok:
            # Only the offending numeric tokens are reported — never the rejected prose.
            return _reject(NUMERIC_HALLUCINATION,
                           f"untraceable number(s) in {name}: {', '.join(offending[:4])}")

    return ValidationReport(ok=True, status=VALIDATED, sections=sections)
