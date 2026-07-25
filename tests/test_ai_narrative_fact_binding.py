"""Semantic fact-binding tests — a real number can no longer land on the wrong meaning.

The exact allow-list already stopped invented values, but it was global across the
evidence: a risk/reward of 1.9 is a genuine number, so "الهدف 1.90" would have passed.
These tests prove the fix — the model writes no digits at all, cites facts by id, and the
application renders every number with its own label, unit, rounding and order.
"""

from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from core import ai_analysis_evidence as evidence
from core import ai_narrative_provider as provider_layer
from core import ai_narrative_validator as validator
from core.ai_narrative_facts import (
    KIND_LABEL,
    SECTIONS,
    build_fact_registry,
    format_value,
)
from core.ai_narrative_numbers import (
    KIND_PRICE,
    build_number_allowlist,
    canonical_key,
    normalize_text,
    validate_numbers,
)
from core.ai_narrative_prompt import (
    build_messages,
    build_qualitative_payload,
    build_section_schema,
)
from core.ai_narrative_provider import (
    SOURCE_AI,
    SOURCE_FALLBACK,
    NarrativeCache,
    NarrativeConfig,
    build_narrative,
)
from core.ai_stock_analysis_contract import AnalysisRequest, MarketPhase

GEN_AT = "2026-07-22T13:40:02+03:00"
EXTERNAL = NarrativeConfig(enabled=True, provider="mock-openai", model="mock-model-1",
                           timeout_seconds=5.0, max_retries=0, cache_enabled=True)
ARABIC_DIGITS = {ord(str(i)): chr(0x0660 + i) for i in range(10)}


# --------------------------------------------------------------------------- #
# Evidence fixtures
# --------------------------------------------------------------------------- #

def _frame(closes, *, volume=1_000_000.0, volume_safe=True):
    n = len(closes)
    idx = pd.date_range("2024-01-01", periods=n, freq="B", name="Date")
    close = pd.Series(closes, dtype=float, index=idx)
    df = pd.DataFrame({
        "Open": close.shift(1).fillna(close.iloc[0]),
        "High": close * 1.01, "Low": close * 0.99, "Close": close, "Adj Close": close,
        "Volume": pd.Series(np.full(n, volume, dtype=float), index=idx),
    }, index=idx)
    df.attrs["market_data"] = {
        "data_domain": "CURRENT_RESEARCH_V2", "provider": "eodhd",
        "price_series": "SPLIT_ADJUSTED",
        "price_adjustment_policy": "SPLIT_ADJUSTED_ALL_EVENTS",
        "volume_series": "RAW_EODHD", "volume_adjustment_policy": "NONE",
        "volume_safe_for_lookback": volume_safe,
        "latest_action_in_lookback": None if volume_safe else "2024-06-01",
        "freshness_status": "HISTORY_CURRENT",
        "latest_completed_session": idx[-1].date().isoformat(),
        "expected_completed_session": idx[-1].date().isoformat(),
        "history_sufficient": n >= 200,
        "yahoo_network_used": False, "yahoo_seed_present": False,
        "routing_tier": "TIER_A_FORWARD_SAFE", "live_provider": "rubix",
    }
    return df


def _request(symbol="COMI"):
    return AnalysisRequest(symbol=symbol, request_id="req-fact-0001", as_of=GEN_AT,
                           market_phase=MarketPhase.CONTINUOUS)


def _evidence(frame):
    return evidence.build_evidence(_request(), frame,
                                   market_phase=MarketPhase.CONTINUOUS,
                                   generated_at=GEN_AT)


@pytest.fixture()
def result():
    return _evidence(_frame([50.0 + i * 0.15 for i in range(260)]))


@pytest.fixture()
def unsafe_volume_result():
    return _evidence(_frame([50.0 + i * 0.15 for i in range(260)], volume_safe=False))


@pytest.fixture()
def registry(result):
    return build_fact_registry(result)


@pytest.fixture(autouse=True)
def _clean_cache():
    """The process-wide narrative cache must never leak between tests."""
    provider_layer.NARRATIVE_CACHE.clear()
    yield
    provider_layer.NARRATIVE_CACHE.clear()


# --------------------------------------------------------------------------- #
# Mock provider
# --------------------------------------------------------------------------- #

class MockProvider:
    name, model = "mock-openai", "mock-model-1"

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.schemas = []
        self.messages = []

    def is_configured(self):
        return True

    def complete(self, messages, *, timeout, schema=None):
        self.calls += 1
        self.schemas.append(schema)
        self.messages.append(messages)
        return json.dumps(self.payload, ensure_ascii=False)


def _answer(result, **overrides) -> dict:
    """A valid, number-free answer; ``overrides`` replace whole sections."""
    reg = build_fact_registry(result)

    def _s(name, text, *refs):
        return {"qualitative_text_ar": text,
                "fact_refs": [r for r in refs if r in reg.for_section(name)]}

    base = {
        "executive_summary_ar": _s("executive_summary_ar",
                                   "الاتجاه العام إيجابي والسيناريو ما زال مشروطًا.",
                                   "price.close", "classification.trend",
                                   "confidence.overall"),
        "technical_read_ar": _s("technical_read_ar",
                                "الزخم إيجابي مع حاجة إلى استمرار التأكيد.",
                                "indicator.rsi_14", "indicator.macd_histogram"),
        "positive_scenario_ar": _s("positive_scenario_ar",
                                   "السيناريو الإيجابي يرتبط بتجاوز مستوى التفعيل.",
                                   "scenario.primary.trigger", "scenario.primary.target",
                                   "scenario.primary.risk_reward"),
        "negative_scenario_ar": _s("negative_scenario_ar",
                                   "يبطل السيناريو عند مستوى الإلغاء المحسوب.",
                                   "scenario.primary.stop"),
        "confirmation_conditions_ar": _s("confirmation_conditions_ar",
                                         "يحتاج إلى تأكيد بإغلاق فوق مستوى التفعيل.",
                                         "scenario.primary.trigger"),
        "invalidation_conditions_ar": _s("invalidation_conditions_ar",
                                         "يبطل السيناريو عند كسر مستوى الإلغاء.",
                                         "scenario.primary.stop"),
        "risk_notes_ar": _s("risk_notes_ar", "تقلب السعر قد يغيّر نتيجة المتابعة.",
                            "indicator.atr_14"),
        "data_limitations_ar": _s("data_limitations_ar",
                                  "الأدلة تقتصر على الجلسات المكتملة المتاحة.",
                                  "data.status", "data.latest_session"),
    }
    base.update(overrides)
    return base


def _section(name, text, refs) -> dict:
    return {"qualitative_text_ar": text, "fact_refs": list(refs)}


def _run(result, payload, *, config=EXTERNAL, cache=None):
    mock = MockProvider(payload)
    narrative = build_narrative(result, config=config, provider=mock, cache=cache)
    return narrative, mock


def _validate(result, payload):
    return validator.validate_response(payload, result, build_fact_registry(result))


# --------------------------------------------------------------------------- #
# The reported hole: a real evidence number on the wrong meaning
# --------------------------------------------------------------------------- #

def test_risk_reward_value_cannot_be_emitted_as_a_raw_price(result):
    """1.90 is a genuine risk/reward, so a value-only check would have accepted it."""
    risk_reward = result.scenarios[0].risk_reward
    assert risk_reward is not None
    # It IS traceable as a value — the old gate would have let this through.
    ok, _ = validate_numbers(f"الهدف {risk_reward:.2f}", build_number_allowlist(result))
    assert ok
    # The fact-bound gate rejects it, because prose may not carry digits at all.
    report = _validate(result, _answer(result, positive_scenario_ar=_section(
        "positive_scenario_ar", f"الهدف المحسوب {risk_reward:.2f} جنيه.", [])))
    assert not report.ok
    assert report.status == validator.RAW_NUMBER_IN_PROSE


def test_target_cannot_be_referenced_from_technical_read(result):
    report = _validate(result, _answer(result, technical_read_ar=_section(
        "technical_read_ar", "القراءة الفنية تشير إلى استمرار الاتجاه.",
        ["scenario.primary.target"])))
    assert report.status == validator.FACT_REFERENCE_INVALID
    assert "scenario.primary.target" in report.reason
    assert "technical_read_ar" in report.reason


def test_rsi_cannot_be_referenced_as_a_target(result):
    report = _validate(result, _answer(result, positive_scenario_ar=_section(
        "positive_scenario_ar", "الهدف المحسوب يمثل نهاية السيناريو.",
        ["indicator.rsi_14"])))
    assert report.status == validator.FACT_REFERENCE_INVALID
    assert "indicator.rsi_14" in report.reason


def test_stop_cannot_be_used_in_the_positive_scenario(result):
    report = _validate(result, _answer(result, positive_scenario_ar=_section(
        "positive_scenario_ar", "السيناريو الإيجابي يرتبط بمستوى محسوب.",
        ["scenario.primary.stop"])))
    assert report.status == validator.FACT_REFERENCE_INVALID
    assert "scenario.primary.stop" in report.reason


def test_volume_and_turnover_are_distinct_facts(result, registry):
    volume = registry.get("price.volume")
    turnover = registry.get("price.turnover")
    assert volume is not None and turnover is not None
    assert volume.value == result.price.volume
    assert turnover.value == result.price.turnover
    assert volume.label_ar != turnover.label_ar
    # Citing volume renders the volume label with the volume value — never turnover's.
    lines = registry.fact_lines(["price.volume"])
    assert lines == (f"{volume.label_ar}: {volume.formatted}",)
    assert turnover.label_ar not in lines[0]


def test_remaining_room_cannot_be_presented_as_the_daily_change(result):
    # remaining_room_percent belongs to the positive scenario, change_percent to the
    # executive summary; neither can borrow the other's section.
    room = _validate(result, _answer(result, executive_summary_ar=_section(
        "executive_summary_ar", "التغير اليومي يظهر ضمن الملخص.",
        ["scenario.primary.remaining_room_percent"])))
    assert room.status == validator.FACT_REFERENCE_INVALID

    change = _validate(result, _answer(result, positive_scenario_ar=_section(
        "positive_scenario_ar", "المسافة المتبقية إلى الهدف تظهر هنا.",
        ["price.change_percent"])))
    assert change.status == validator.FACT_REFERENCE_INVALID


def test_the_fact_reference_cap_is_announced_and_constrained(result, registry):
    """A limit the model is never told about produces spurious fallbacks.

    Found in real local-model validation: qwen3:4b cited seven permitted facts in the
    technical read and the whole answer was discarded by an internal cap that appeared
    neither in the prompt nor in the schema.
    """
    from core.ai_narrative_prompt import MAX_FACT_REFS, SYSTEM_PROMPT

    assert "AT MOST SIX fact ids per section" in SYSTEM_PROMPT
    schema = build_section_schema(registry)
    for section in SECTIONS:
        refs = schema["properties"][section]["properties"]["fact_refs"]
        assert refs["maxItems"] == MAX_FACT_REFS

    # The cap is still enforced — it is now merely reachable by a compliant model.
    allowed = list(registry.for_section("technical_read_ar"))[:MAX_FACT_REFS + 1]
    assert len(allowed) == MAX_FACT_REFS + 1
    report = _validate(result, _answer(result, technical_read_ar=_section(
        "technical_read_ar", "القراءة الفنية تشير إلى استمرار الاتجاه.", allowed)))
    assert report.status == validator.FACT_REFERENCE_INVALID
    assert "too many fact_refs" in report.reason

    at_cap = allowed[:MAX_FACT_REFS]
    ok = _validate(result, _answer(result, technical_read_ar=_section(
        "technical_read_ar", "القراءة الفنية تشير إلى استمرار الاتجاه.", at_cap)))
    assert ok.ok, ok.reason


def test_unknown_fact_id_is_rejected(result):
    report = _validate(result, _answer(result, risk_notes_ar=_section(
        "risk_notes_ar", "ملاحظة مخاطر.", ["scenario.primary.made_up"])))
    assert report.status == validator.FACT_REFERENCE_INVALID
    assert "unknown fact" in report.reason


# --------------------------------------------------------------------------- #
# No raw numbers from the model, in any script
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text", [
    "الهدف 1.90 جنيه.",
    "نسبة التغير 15%.",
    "خط الماكد -1.42.",
    "حجم التداول 3,200,000 سهم.",
    "آخر جلسة 2026-07-22.",
    "الإغلاق 92 جنيه.",
])
def test_western_numeric_literals_in_prose_are_rejected(result, text):
    report = _validate(result, _answer(result, risk_notes_ar=_section(
        "risk_notes_ar", text, [])))
    assert report.status == validator.RAW_NUMBER_IN_PROSE


@pytest.mark.parametrize("text", [
    "الهدف ١٫٩٠ جنيه.",
    "نسبة التغير ١٥٪.",
    "الإغلاق ۹۲ جنيه.",
])
def test_arabic_indic_numeric_literals_are_rejected(result, text):
    report = _validate(result, _answer(result, risk_notes_ar=_section(
        "risk_notes_ar", text, [])))
    assert report.status == validator.RAW_NUMBER_IN_PROSE


@pytest.mark.parametrize("text", ["نسبة مئوية ٪ من المدى.", "نسبة % من الحجم."])
def test_bare_percent_signs_in_prose_are_rejected(result, text):
    report = _validate(result, _answer(result, risk_notes_ar=_section(
        "risk_notes_ar", text, [])))
    assert report.status == validator.RAW_NUMBER_IN_PROSE


def test_find_raw_number_covers_every_digit_script():
    assert validator.find_raw_number("لا أرقام هنا") is None
    assert validator.find_raw_number("قيمة 5") is not None
    assert validator.find_raw_number("قيمة ٥") is not None
    assert validator.find_raw_number("قيمة ۵") is not None
    assert validator.find_raw_number("نسبة ٪") is not None


def test_approved_qualitative_prose_with_valid_refs_passes(result):
    report = _validate(result, _answer(result))
    assert report.ok and report.status == validator.VALIDATED
    assert set(report.sections) == set(SECTIONS)
    assert report.fact_refs["positive_scenario_ar"][0] == "scenario.primary.trigger"


# --------------------------------------------------------------------------- #
# Deterministic rendering — the application owns every number
# --------------------------------------------------------------------------- #

def test_renderer_inserts_the_exact_typed_values(result, registry):
    report = _validate(result, _answer(result))
    positive = report.sections["positive_scenario_ar"].split("\n")
    prose, lines = positive[0], positive[1:]
    assert not any(ch.isdigit() for ch in prose)          # prose stays number-free

    trigger = registry.get("scenario.primary.trigger")
    target = registry.get("scenario.primary.target")
    risk_reward = registry.get("scenario.primary.risk_reward")
    assert lines == [
        f"{trigger.label_ar}: {trigger.formatted}",
        f"{target.label_ar}: {target.formatted}",
        f"{risk_reward.label_ar}: {risk_reward.formatted}",
    ]
    # The rendered figures are the raw typed values, not re-derived ones.
    assert canonical_key(trigger.formatted.split()[0]) == f"{result.scenarios[0].trigger:.2f}"
    assert canonical_key(target.formatted.split()[0]) == f"{result.scenarios[0].target:.2f}"


def test_fact_line_order_is_the_registry_order_not_the_model_order(result, registry):
    shuffled = ["scenario.primary.risk_reward", "scenario.primary.target",
                "scenario.primary.trigger"]
    report = _validate(result, _answer(result, positive_scenario_ar=_section(
        "positive_scenario_ar", "السيناريو الإيجابي مشروط بالتأكيد.", shuffled)))
    lines = report.sections["positive_scenario_ar"].split("\n")[1:]
    labels = [registry.get(fid).label_ar for fid in
              ("scenario.primary.trigger", "scenario.primary.target",
               "scenario.primary.risk_reward")]
    assert [line.split(":")[0] for line in lines] == labels


def test_duplicate_references_render_once(result):
    report = _validate(result, _answer(result, negative_scenario_ar=_section(
        "negative_scenario_ar", "يبطل السيناريو عند المستوى المحسوب.",
        ["scenario.primary.stop", "scenario.primary.stop"])))
    lines = report.sections["negative_scenario_ar"].split("\n")[1:]
    assert len(lines) == 1


def test_rounding_comes_only_from_the_deterministic_formatter():
    assert format_value(1.8449, KIND_PRICE) == "1.84 جنيه"
    assert format_value(92.4, KIND_PRICE) == "92.40 جنيه"
    assert format_value(1.9, "RATIO") == "1.90"
    assert format_value(0.55, "UNIT_RATIO") == "55 / 100"
    assert format_value(63.0, "SCORE") == "63 / 100"
    assert format_value(3_200_000.0, "VOLUME") == "3,200,000"
    assert format_value(15.37, "PERCENT") == "15.37٪"
    assert format_value(1.43, "PERCENT", signed=True) == "+1.43٪"
    assert format_value("محدّث", KIND_LABEL) == "محدّث"


def test_every_rendered_fact_survives_the_exact_numeric_gate(result, registry):
    """Defence in depth: the renderer's own output must be evidence-traceable."""
    allowlist = build_number_allowlist(result)
    for fact_id in registry.fact_ids:
        fact = registry.get(fact_id)
        ok, offending = validate_numbers(fact.formatted, allowlist)
        assert ok, (fact_id, fact.formatted, offending)


def test_composed_section_is_re_checked_by_the_numeric_gate(result, monkeypatch):
    """A tampered renderer cannot slip an untraceable number past the gate."""
    import core.ai_narrative_facts as facts_module

    original = facts_module.FactRegistry.fact_lines
    monkeypatch.setattr(facts_module.FactRegistry, "fact_lines",
                        lambda self, ids: ("الهدف المحسوب: 987654.31 جنيه",))
    report = _validate(result, _answer(result))
    assert report.status == validator.NUMERIC_HALLUCINATION
    monkeypatch.setattr(facts_module.FactRegistry, "fact_lines", original)


# --------------------------------------------------------------------------- #
# Volume safety
# --------------------------------------------------------------------------- #

def test_volume_facts_are_absent_when_volume_is_not_safe(unsafe_volume_result):
    registry = build_fact_registry(unsafe_volume_result)
    for fact_id in ("price.volume", "price.turnover", "indicator.volume_ratio",
                    "indicator.average_volume_20"):
        assert fact_id not in registry, fact_id
    assert "الحجم غير موثوق" in registry.get("data.volume_safe").formatted


def test_volume_reference_is_rejected_when_volume_is_not_safe(unsafe_volume_result):
    report = _validate(unsafe_volume_result, _answer(
        unsafe_volume_result, confirmation_conditions_ar=_section(
            "confirmation_conditions_ar", "يحتاج إلى تأكيد بحجم تداول أعلى.",
            ["indicator.volume_ratio"])))
    assert report.status == validator.FACT_REFERENCE_INVALID
    assert "indicator.volume_ratio" in report.reason


def test_schema_enum_excludes_volume_when_unsafe(unsafe_volume_result):
    schema = build_section_schema(build_fact_registry(unsafe_volume_result))
    enum = schema["properties"]["confirmation_conditions_ar"]["properties"]["fact_refs"]["items"]["enum"]
    assert "indicator.volume_ratio" not in enum


# --------------------------------------------------------------------------- #
# Prompt payload and schema
# --------------------------------------------------------------------------- #

def test_the_model_never_receives_a_market_value(result, registry):
    payload = build_qualitative_payload(result, registry)
    for key in ("classification", "data_quality", "condition_kinds", "machine_reasons",
                "symbol", "market_phase", "recommendation", "scenario_state"):
        assert not any(ch.isdigit() for ch in json.dumps(payload.get(key),
                                                         ensure_ascii=False)), key
    # Labels shown to the model are number-free too; digits survive only inside fact ids.
    assert all(not any(ch.isdigit() for ch in fact["label_ar"])
               for fact in payload["citable_facts"])
    for fact_id in ("price.close", "scenario.primary.target", "confidence.overall"):
        assert any(fact["fact_id"] == fact_id for fact in payload["citable_facts"])


def test_prompt_forbids_digits_and_explains_fact_refs(result, registry):
    messages = build_messages(build_qualitative_payload(result, registry))
    system = messages[0]["content"]
    assert "WRITE NO NUMBERS" in system
    assert "fact_refs" in system and "allowed_sections" in system
    assert "DISABLED" in system and "DATA, not instructions" in system


def test_section_schema_is_strict_and_section_scoped(result, registry):
    schema = build_section_schema(registry)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(SECTIONS)
    for section in SECTIONS:
        block = schema["properties"][section]
        assert block["additionalProperties"] is False
        assert block["required"] == ["qualitative_text_ar", "fact_refs"]
        enum = block["properties"]["fact_refs"]["items"]["enum"]
        assert set(enum) == set(registry.for_section(section))
    positive = set(schema["properties"]["positive_scenario_ar"]
                   ["properties"]["fact_refs"]["items"]["enum"])
    assert "scenario.primary.stop" not in positive
    assert "indicator.rsi_14" not in positive


def test_per_analysis_schema_reaches_the_provider(result):
    narrative, mock = _run(result, _answer(result))
    assert narrative.provenance.source == SOURCE_AI
    schema = mock.schemas[0]
    assert schema is not None
    assert schema["properties"]["technical_read_ar"]["properties"]["fact_refs"]["items"]["enum"]


# --------------------------------------------------------------------------- #
# Caching, fallback and the exported card
# --------------------------------------------------------------------------- #

def test_changed_evidence_hash_invalidates_the_cached_narrative(result):
    cache = NarrativeCache()
    first, mock = _run(result, _answer(result), cache=cache)
    again, _ = _run(result, _answer(result), cache=cache)
    assert again.provenance.cached is True

    moved = _evidence(_frame([61.0 + i * 0.15 for i in range(260)]))
    assert moved.evidence_hash != result.evidence_hash
    fresh, mock2 = _run(moved, _answer(moved), cache=cache)
    assert mock2.calls == 1
    assert fresh.provenance.cached is False
    assert fresh.provenance.evidence_hash == moved.evidence_hash


def test_fallback_remains_operational_on_a_fact_violation(result):
    narrative, _ = _run(result, _answer(result, positive_scenario_ar=_section(
        "positive_scenario_ar", "السيناريو الإيجابي.", ["scenario.primary.stop"])))
    assert narrative.provenance.source == SOURCE_FALLBACK
    assert narrative.provenance.validation_status == validator.FACT_REFERENCE_INVALID
    # The deterministic narrative is intact and still evidence-only.
    baseline = build_narrative(result, config=NarrativeConfig(enabled=False))
    assert narrative.summary == baseline.summary
    assert narrative.sections == ()


def test_png_contains_only_deterministically_rendered_numbers(result, registry):
    from dashboard.ai_stock_analysis_components import build_card_payload, generate_card_bytes

    narrative, _ = _run(result, _answer(result))
    payload = build_card_payload(result, narrative)
    card_text = f"{payload.narrative_headline}\n{payload.narrative_summary}"

    # Every number on the card is an approved rendering of a typed evidence value…
    ok, offending = validate_numbers(card_text, build_number_allowlist(result))
    assert ok, offending
    # …and each numeric line of the narrative body is a registry-rendered fact line.
    rendered = {f"{registry.get(f).label_ar}: {registry.get(f).formatted}"
                for f in registry.fact_ids}
    for line in payload.narrative_summary.split("\n")[1:]:
        assert line in rendered, line
    assert generate_card_bytes(result, narrative)[:8] == b"\x89PNG\r\n\x1a\n"


def test_prose_on_the_card_carries_no_model_written_number(result):
    from dashboard.ai_stock_analysis_components import build_card_payload

    narrative, _ = _run(result, _answer(result))
    payload = build_card_payload(result, narrative)
    prose = payload.narrative_summary.split("\n")[0]
    assert normalize_text(prose) == prose or not any(ch.isdigit() for ch in prose)
    assert not any(ch.isdigit() for ch in prose)


def test_disabled_mode_is_untouched_by_fact_binding(result):
    narrative = build_narrative(result, config=NarrativeConfig(enabled=False))
    assert narrative.provenance.source == SOURCE_FALLBACK
    assert narrative.sections == ()
    assert narrative.provenance.fallback_reason == "ai_disabled"
    assert narrative.headline == replace(narrative).headline
