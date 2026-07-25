"""Validation gate between an external model's answer and anything a user ever sees.

Nothing a provider returns is trusted. A response is accepted only when it passes every
check below; otherwise the whole narrative is rejected — never partially repaired, never
partially displayed — and the caller falls back to the deterministic narrative.

  * **Schema** — a JSON object with exactly the eight sections, each an object with
    exactly ``qualitative_text_ar`` and ``fact_refs``.
  * **No raw numbers** — the model's prose must contain NO digit in any script, and no
    percent sign. This is what defeats *semantic misattribution*: a value-only check would
    accept "الهدف 1.90" because 1.90 exists as the risk/reward, so the model is not
    allowed to write figures at all.
  * **Fact binding** — every cited ``fact_id`` must exist in the registry for this
    analysis AND be permitted in the citing section, so a target cannot be cited from the
    technical read and a stop cannot headline the positive scenario.
  * **Format** — no markdown tables, no pipes, no code fences, no HTML.
  * **Wording** — no unconditional buy/sell/enter/size command, no certainty claim.
  * **Defence in depth** — the COMPOSED section (model prose + deterministic fact lines)
    is re-checked against the exact numeric allow-list, so even a rendering bug cannot put
    an untraceable number on screen.

Rejection reasons are safe by construction: they name a rule, a section, or a fact id —
never the rejected prose, which is dropped here and never logged or returned.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from core.ai_narrative_facts import FactRegistry
from core.ai_narrative_numbers import (
    build_number_allowlist,
    normalize_text,
    validate_numbers,
)
from core.ai_narrative_prompt import (
    FACT_REFS_FIELD,
    QUALITATIVE_FIELD,
    REQUIRED_SECTIONS,
)
from core.ai_stock_analysis_contract import AnalysisResult

# Validation statuses recorded in history and shown (as a short token) in the UI.
VALIDATED = "VALIDATED"
EMPTY_RESPONSE = "EMPTY_RESPONSE"
SCHEMA_INVALID = "SCHEMA_INVALID"
FORMAT_VIOLATION = "FORMAT_VIOLATION"
NUMERIC_HALLUCINATION = "NUMERIC_HALLUCINATION"
FORBIDDEN_RECOMMENDATION = "FORBIDDEN_RECOMMENDATION"
RAW_NUMBER_IN_PROSE = "RAW_NUMBER_IN_PROSE"
FACT_REFERENCE_INVALID = "FACT_REFERENCE_INVALID"

MAX_SECTION_CHARS = 700
MAX_TOTAL_CHARS = 4000
MAX_FACT_REFS = 6


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

# Any percent sign, in either script, is prose the model must not write.
_PERCENT_CHARS = frozenset("%٪")


@dataclass(frozen=True)
class ValidationReport:
    """Outcome of validating one provider response.

    ``sections`` holds the COMPOSED text (model prose + deterministic fact lines) and is
    empty on failure. ``fact_refs`` records what each section cited, for audit.
    """
    ok: bool
    status: str
    reason: str = ""
    sections: dict = field(default_factory=dict)
    fact_refs: dict = field(default_factory=dict)

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


def find_raw_number(text: str) -> str | None:
    """Return a short description of the first digit or percent sign found, else None.

    Every Unicode decimal digit counts — Western, Arabic-Indic and Extended Arabic-Indic
    alike — so a figure cannot be smuggled in by changing script.
    """
    for ch in str(text or ""):
        if ch.isdigit() or unicodedata.category(ch) == "Nd":
            return f"digit U+{ord(ch):04X}"
        if ch in _PERCENT_CHARS:
            return f"percent sign U+{ord(ch):04X}"
    return None


def _validate_fact_refs(section: str, refs, registry: FactRegistry):
    """Return (fact_ids, rejection_reason). Order and duplicates are normalized away."""
    if not isinstance(refs, list):
        return None, f"fact_refs is not a list in {section}"
    if len(refs) > MAX_FACT_REFS:
        return None, f"too many fact_refs in {section}"
    seen: list[str] = []
    for ref in refs:
        if not isinstance(ref, str):
            return None, f"non-string fact ref in {section}"
        fact_id = ref.strip()
        if not fact_id:
            continue
        if fact_id not in registry:
            return None, f"unknown fact '{fact_id}' in {section}"
        if not registry.permits(section, fact_id):
            return None, f"fact '{fact_id}' is not permitted in {section}"
        if fact_id not in seen:
            seen.append(fact_id)
    return seen, ""


def validate_response(payload, result: AnalysisResult,
                      registry: FactRegistry | None = None) -> ValidationReport:
    """Validate a decoded provider response and compose the final sections.

    All-or-nothing: any failure rejects the entire answer.
    """
    from core.ai_narrative_facts import build_fact_registry

    registry = registry if registry is not None else build_fact_registry(result)

    if payload is None:
        return _reject(EMPTY_RESPONSE, "provider returned no content")
    if not isinstance(payload, dict):
        return _reject(SCHEMA_INVALID, "response was not a JSON object")

    unexpected = sorted(set(payload) - set(REQUIRED_SECTIONS))
    if unexpected:
        return _reject(SCHEMA_INVALID, f"unexpected keys: {','.join(unexpected[:5])}")

    prose: dict[str, str] = {}
    refs: dict[str, list] = {}
    for section in REQUIRED_SECTIONS:
        block = payload.get(section)
        if not isinstance(block, dict):
            return _reject(SCHEMA_INVALID, f"missing or malformed section: {section}")
        extra = sorted(set(block) - {QUALITATIVE_FIELD, FACT_REFS_FIELD})
        if extra:
            return _reject(SCHEMA_INVALID,
                           f"unexpected field(s) in {section}: {','.join(extra[:3])}")

        text = block.get(QUALITATIVE_FIELD)
        if not isinstance(text, str) or not text.strip():
            return _reject(SCHEMA_INVALID, f"missing or empty prose: {section}")
        text = text.strip()
        if len(text) > MAX_SECTION_CHARS:
            return _reject(SCHEMA_INVALID, f"section too long: {section}")

        rule = check_format(text)
        if rule is not None:
            return _reject(FORMAT_VIOLATION, f"{rule} in {section}")
        rule = check_forbidden_wording(text)
        if rule is not None:
            return _reject(FORBIDDEN_RECOMMENDATION, f"{rule} in {section}")
        found = find_raw_number(text)
        if found is not None:
            # The model must cite a fact, never write a figure.
            return _reject(RAW_NUMBER_IN_PROSE, f"{found} in {section}")

        cited, reason = _validate_fact_refs(section, block.get(FACT_REFS_FIELD), registry)
        if cited is None:
            return _reject(FACT_REFERENCE_INVALID, reason)

        prose[section] = text
        refs[section] = cited

    if sum(len(v) for v in prose.values()) > MAX_TOTAL_CHARS:
        return _reject(SCHEMA_INVALID, "response exceeded the total length budget")

    # Compose: model prose first, then the deterministic fact lines in registry order.
    sections = {name: registry.compose(prose[name], refs[name])
                for name in REQUIRED_SECTIONS}

    # Defence in depth: every number in the COMPOSED text must still be an approved
    # rendering of a typed evidence value.
    allowlist = build_number_allowlist(result)
    for name, text in sections.items():
        ok, offending = validate_numbers(text, allowlist)
        if not ok:
            return _reject(NUMERIC_HALLUCINATION,
                           f"untraceable number(s) in {name}: {', '.join(offending[:4])}")

    return ValidationReport(ok=True, status=VALIDATED, sections=sections,
                            fact_refs={k: tuple(v) for k, v in refs.items()})
