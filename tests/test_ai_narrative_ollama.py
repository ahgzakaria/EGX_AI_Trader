"""Mocked-HTTP tests for the local Ollama provider. No model runs, no network.

``urllib.request.urlopen`` is replaced throughout, so the suite exercises the exact
request body, the health probe and every failure path without contacting a real Ollama
server, downloading a model, or spending anything.
"""

from __future__ import annotations

import io
import json
import logging
import urllib.error

import numpy as np
import pandas as pd
import pytest

from core import ai_analysis_evidence as evidence
from core import ai_narrative_provider as provider_layer
from core import ai_narrative_validator as validator
from core.ai_analysis_narrative import FALLBACK_MODEL
from core.ai_narrative_facts import build_fact_registry
from core.ai_narrative_ollama import (
    CHAT_PATH,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    LOCAL_AI_MODEL_MISSING,
    LOCAL_AI_READY,
    LOCAL_AI_UNAVAILABLE,
    TAGS_PATH,
    OllamaProvider,
    ProviderModelMissing,
    check_health,
)
from core.ai_narrative_prompt import build_section_schema
from core.ai_narrative_provider import (
    SOURCE_FALLBACK,
    SOURCE_LOCAL_AI,
    NarrativeCache,
    NarrativeConfig,
    NarrativeMode,
    ProviderTimeout,
    ProviderTransportError,
    build_narrative,
    build_provider,
)
from core.ai_stock_analysis_contract import AnalysisRequest, MarketPhase
from core.ai_stock_analysis_service import analyze_symbol

GEN_AT = "2026-07-22T13:40:02+03:00"
OLLAMA_CONFIG = NarrativeConfig(enabled=True, provider="ollama", model="qwen3:4b",
                                timeout_seconds=60.0, max_retries=0, cache_enabled=True)


# --------------------------------------------------------------------------- #
# Evidence + answer fixtures
# --------------------------------------------------------------------------- #

def _frame(closes, *, volume=1_000_000.0):
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
        "volume_safe_for_lookback": True, "latest_action_in_lookback": None,
        "freshness_status": "HISTORY_CURRENT",
        "latest_completed_session": idx[-1].date().isoformat(),
        "expected_completed_session": idx[-1].date().isoformat(),
        "history_sufficient": n >= 200,
        "yahoo_network_used": False, "yahoo_seed_present": False,
        "routing_tier": "TIER_A_FORWARD_SAFE", "live_provider": "rubix",
    }
    return df


def _uptrend():
    return _frame([50.0 + i * 0.15 for i in range(260)])


def _evidence(frame=None):
    request = AnalysisRequest(symbol="COMI", request_id="req-ollama-0001", as_of=GEN_AT,
                              market_phase=MarketPhase.CONTINUOUS)
    return evidence.build_evidence(request, frame if frame is not None else _uptrend(),
                                   market_phase=MarketPhase.CONTINUOUS,
                                   generated_at=GEN_AT)


@pytest.fixture()
def result():
    return _evidence()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("AI_NARRATIVE_OLLAMA_URL", "AI_NARRATIVE_OLLAMA_ALLOW_REMOTE",
                 "AI_NARRATIVE_OLLAMA_TEMPERATURE", "AI_NARRATIVE_OLLAMA_KEEP_ALIVE",
                 "AI_NARRATIVE_OLLAMA_DISABLE_THINKING", "AI_NARRATIVE_MODEL"):
        monkeypatch.delenv(name, raising=False)
    provider_layer.NARRATIVE_CACHE.clear()
    yield
    provider_layer.NARRATIVE_CACHE.clear()


def _answer(result, **overrides) -> dict:
    """Valid qualitative sections: number-free prose plus permitted fact references."""
    registry = build_fact_registry(result)

    def _s(name, text, *refs):
        return {"qualitative_text_ar": text,
                "fact_refs": [r for r in refs if r in registry.for_section(name)]}

    payload = {
        "executive_summary_ar": _s("executive_summary_ar",
                                   "الاتجاه العام إيجابي والسيناريو ما زال مشروطًا.",
                                   "price.close", "classification.trend"),
        "technical_read_ar": _s("technical_read_ar",
                                "الزخم إيجابي مع حاجة إلى استمرار التأكيد.",
                                "indicator.rsi_14"),
        "positive_scenario_ar": _s("positive_scenario_ar",
                                   "السيناريو الإيجابي يرتبط بتجاوز مستوى التفعيل.",
                                   "scenario.primary.trigger", "scenario.primary.target"),
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
                                  "data.status"),
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# HTTP doubles
# --------------------------------------------------------------------------- #

class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class _Server:
    """Scripted Ollama: records requests, answers /api/chat and /api/tags."""

    def __init__(self, *, chat=None, tags=None, chat_error=None):
        self.chat_payload = chat
        self.tags_payload = tags if tags is not None else {
            "models": [{"name": "qwen3:4b", "model": "qwen3:4b", "size": 2600000000}]}
        self.chat_error = chat_error
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append((request, timeout))
        url = request.full_url
        if url.endswith(TAGS_PATH):
            body = self.tags_payload
        else:
            if self.chat_error is not None:
                raise self.chat_error
            body = self.chat_payload
        return _Response(json.dumps(body, ensure_ascii=False).encode("utf-8"))

    # -- accessors ------------------------------------------------------------ #

    @property
    def chat_requests(self):
        return [r for r, _ in self.requests if r.full_url.endswith(CHAT_PATH)]

    @property
    def body(self) -> dict:
        return json.loads(self.chat_requests[-1].data.decode("utf-8"))

    @property
    def headers(self) -> dict:
        return {k.lower(): v for k, v in self.chat_requests[-1].headers.items()}


def _chat_envelope(content, *, thinking=None) -> dict:
    message = {"role": "assistant", "content": json.dumps(content, ensure_ascii=False)
               if isinstance(content, dict) else content}
    if thinking is not None:
        message["thinking"] = thinking
    return {"model": "qwen3:4b", "created_at": "2026-07-22T10:40:02Z",
            "message": message, "done": True, "done_reason": "stop",
            "total_duration": 1234567, "eval_count": 210}


def _patch(monkeypatch, server):
    monkeypatch.setattr("urllib.request.urlopen", server)
    return server


def _run(result, server, *, config=OLLAMA_CONFIG, cache=None, **kw):
    return build_narrative(result, config=config, cache=cache, **kw)


# --------------------------------------------------------------------------- #
# Configuration and endpoint policy
# --------------------------------------------------------------------------- #

def test_provider_is_registered_and_needs_no_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AI_NARRATIVE_API_KEY", raising=False)
    adapter = build_provider(OLLAMA_CONFIG)
    assert adapter.name == "ollama"
    assert adapter.model == "qwen3:4b"
    assert adapter.is_configured() is True             # no key exists or is required


def test_defaults_are_local_and_qwen(monkeypatch):
    adapter = OllamaProvider()
    assert adapter.base_url == DEFAULT_BASE_URL == "http://127.0.0.1:11434"
    assert adapter.model == DEFAULT_MODEL == "qwen3:4b"
    assert adapter.chat_endpoint == "http://127.0.0.1:11434/api/chat"
    assert adapter.tags_endpoint == "http://127.0.0.1:11434/api/tags"


def test_model_and_url_stay_configurable(monkeypatch):
    monkeypatch.setenv("AI_NARRATIVE_MODEL", "qwen3:8b")
    monkeypatch.setenv("AI_NARRATIVE_OLLAMA_URL", "http://localhost:11500")
    adapter = OllamaProvider()
    assert adapter.model == "qwen3:8b"
    assert adapter.chat_endpoint == "http://localhost:11500/api/chat"
    assert adapter.is_configured() is True             # localhost is still loopback


@pytest.mark.parametrize("url", [
    "http://10.0.0.5:11434", "https://ollama.example.com", "http://192.168.1.20:11434",
])
def test_remote_url_is_refused_unless_explicitly_allowed(monkeypatch, url):
    monkeypatch.setenv("AI_NARRATIVE_OLLAMA_URL", url)
    adapter = OllamaProvider()
    assert adapter.endpoint_allowed is False
    assert adapter.is_configured() is False

    monkeypatch.setenv("AI_NARRATIVE_OLLAMA_ALLOW_REMOTE", "true")
    assert OllamaProvider().is_configured() is True


def test_remote_url_falls_back_without_contacting_anything(result, monkeypatch):
    monkeypatch.setenv("AI_NARRATIVE_OLLAMA_URL", "https://ollama.example.com")

    def _explode(*args, **kwargs):
        raise AssertionError("a non-local endpoint must never be contacted")

    monkeypatch.setattr("urllib.request.urlopen", _explode)
    narrative = _run(result, None)
    assert narrative.provenance.source == SOURCE_FALLBACK
    assert narrative.provenance.fallback_reason == "missing_api_key"


def test_ollama_cloud_is_never_contacted(result, monkeypatch):
    server = _patch(monkeypatch, _Server(chat=_chat_envelope(_answer(result))))
    _run(result, server)
    for request, _ in server.requests:
        assert "127.0.0.1" in request.full_url or "localhost" in request.full_url
        assert "ollama.com" not in request.full_url


# --------------------------------------------------------------------------- #
# Request shape
# --------------------------------------------------------------------------- #

def test_request_is_a_single_shot_schema_constrained_chat(result, monkeypatch):
    server = _patch(monkeypatch, _Server(chat=_chat_envelope(_answer(result))))
    narrative = _run(result, server)
    assert narrative.provenance.source == SOURCE_LOCAL_AI

    body = server.body
    assert server.chat_requests[-1].full_url == "http://127.0.0.1:11434/api/chat"
    assert body["model"] == "qwen3:4b"
    assert body["stream"] is False
    assert body["think"] is False                    # no Qwen reasoning trace
    assert body["tools"] == []
    assert body["options"]["temperature"] == pytest.approx(0.2)
    assert body["keep_alive"] == "5m"
    assert "format" in body


def test_the_per_analysis_schema_is_sent_as_format(result, monkeypatch):
    server = _patch(monkeypatch, _Server(chat=_chat_envelope(_answer(result))))
    _run(result, server)
    expected = build_section_schema(build_fact_registry(result))
    assert server.body["format"] == expected
    positive = server.body["format"]["properties"]["positive_scenario_ar"]
    assert positive["properties"]["fact_refs"]["items"]["enum"]
    assert positive["additionalProperties"] is False


def test_no_tool_or_network_capability_is_requested(result, monkeypatch):
    server = _patch(monkeypatch, _Server(chat=_chat_envelope(_answer(result))))
    _run(result, server)
    serialized = json.dumps(server.body)
    for forbidden in ("web_search", "browser", "file_search", "retrieval",
                      "code_interpreter", "function_call"):
        assert forbidden not in serialized
    assert server.body["tools"] == []


def test_temperature_and_keep_alive_are_configurable(monkeypatch):
    monkeypatch.setenv("AI_NARRATIVE_OLLAMA_TEMPERATURE", "0.05")
    monkeypatch.setenv("AI_NARRATIVE_OLLAMA_KEEP_ALIVE", "30m")
    body = OllamaProvider().build_request_body([{"role": "user", "content": "x"}], None)
    assert body["options"]["temperature"] == pytest.approx(0.05)
    assert body["keep_alive"] == "30m"


def test_no_authorization_header_is_sent(result, monkeypatch):
    server = _patch(monkeypatch, _Server(chat=_chat_envelope(_answer(result))))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-be-used")
    _run(result, server)
    assert "authorization" not in server.headers
    assert "sk-must-not-be-used" not in json.dumps(server.body, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Response handling
# --------------------------------------------------------------------------- #

def test_valid_structured_output_is_accepted(result, monkeypatch):
    server = _patch(monkeypatch, _Server(chat=_chat_envelope(_answer(result))))
    narrative = _run(result, server)
    assert narrative.provenance.source == SOURCE_LOCAL_AI
    assert narrative.provenance.provider == "ollama"
    assert narrative.provenance.model == "qwen3:4b"
    assert narrative.provenance.validation_status == validator.VALIDATED
    sections = dict(narrative.sections)
    assert "نقطة التفعيل" in sections["positive_scenario_ar"]


def test_reasoning_trace_is_never_read_or_stored(result, monkeypatch):
    secret_trace = "SECRET-CHAIN-OF-THOUGHT الهدف 999.99"
    server = _patch(monkeypatch, _Server(
        chat=_chat_envelope(_answer(result), thinking=secret_trace)))
    narrative = _run(result, server)
    assert narrative.provenance.source == SOURCE_LOCAL_AI
    blob = json.dumps(narrative.__dict__, default=str, ensure_ascii=False)
    assert "SECRET-CHAIN-OF-THOUGHT" not in blob
    assert "999.99" not in blob


def test_extract_content_ignores_the_thinking_field():
    envelope = _chat_envelope({"a": 1}, thinking="internal reasoning")
    assert "internal reasoning" not in OllamaProvider.extract_content(envelope)


def test_malformed_json_falls_back(result, monkeypatch):
    _patch(monkeypatch, _Server(chat=_chat_envelope("{not json at all")))
    narrative = _run(result, None)
    assert narrative.provenance.source == SOURCE_FALLBACK
    assert narrative.provenance.validation_status == validator.SCHEMA_INVALID
    assert narrative.model == FALLBACK_MODEL


def test_missing_section_falls_back(result, monkeypatch):
    payload = _answer(result)
    payload.pop("risk_notes_ar")
    _patch(monkeypatch, _Server(chat=_chat_envelope(payload)))
    narrative = _run(result, None)
    assert narrative.provenance.validation_status == validator.SCHEMA_INVALID


def test_raw_number_in_prose_falls_back(result, monkeypatch):
    payload = _answer(result)
    payload["positive_scenario_ar"] = {
        "qualitative_text_ar": "الهدف المحسوب 97.80 جنيه.", "fact_refs": []}
    _patch(monkeypatch, _Server(chat=_chat_envelope(payload)))
    narrative = _run(result, None)
    assert narrative.provenance.validation_status == validator.RAW_NUMBER_IN_PROSE
    assert narrative.provenance.source == SOURCE_FALLBACK


def test_invalid_fact_reference_falls_back(result, monkeypatch):
    payload = _answer(result)
    payload["technical_read_ar"] = {
        "qualitative_text_ar": "القراءة الفنية تشير إلى استمرار الاتجاه.",
        "fact_refs": ["scenario.primary.target"]}
    _patch(monkeypatch, _Server(chat=_chat_envelope(payload)))
    narrative = _run(result, None)
    assert narrative.provenance.validation_status == validator.FACT_REFERENCE_INVALID


def test_empty_content_falls_back(result, monkeypatch):
    _patch(monkeypatch, _Server(chat=_chat_envelope("")))
    narrative = _run(result, None)
    assert narrative.provenance.source == SOURCE_FALLBACK
    assert narrative.provenance.fallback_reason == "empty_response"


# --------------------------------------------------------------------------- #
# Failure paths
# --------------------------------------------------------------------------- #

def test_ollama_unavailable_falls_back(result, monkeypatch):
    _patch(monkeypatch, _Server(chat_error=urllib.error.URLError(
        ConnectionRefusedError(61, "Connection refused"))))
    narrative = _run(result, None)
    assert narrative.provenance.source == SOURCE_FALLBACK
    assert narrative.provenance.fallback_reason == "ollama_unreachable"
    assert narrative.model == FALLBACK_MODEL


def test_model_not_installed_falls_back(result, monkeypatch):
    _patch(monkeypatch, _Server(chat_error=urllib.error.HTTPError(
        "http://127.0.0.1:11434/api/chat", 404, "not found", {},
        io.BytesIO(b'{"error":"model not found, try pulling it first"}'))))
    narrative = _run(result, None)
    assert narrative.provenance.source == SOURCE_FALLBACK
    assert narrative.provenance.fallback_reason == "model_not_installed"


def test_model_not_found_in_body_falls_back(result, monkeypatch):
    _patch(monkeypatch, _Server(chat={"error": "model 'qwen3:4b' not found"}))
    narrative = _run(result, None)
    assert narrative.provenance.fallback_reason == "model_not_installed"


def test_timeout_falls_back(result, monkeypatch):
    import socket

    _patch(monkeypatch, _Server(chat_error=socket.timeout()))
    narrative = _run(result, None)
    assert narrative.provenance.source == SOURCE_FALLBACK
    assert narrative.provenance.fallback_reason == "timeout"


def test_server_error_body_is_never_leaked(result, monkeypatch):
    _patch(monkeypatch, _Server(chat_error=urllib.error.HTTPError(
        "http://127.0.0.1:11434/api/chat", 500, "boom", {},
        io.BytesIO(b'{"error":"internal detail that must not surface"}'))))
    narrative = _run(result, None)
    assert narrative.provenance.fallback_reason == "http_500"
    assert "internal detail" not in json.dumps(narrative.__dict__, default=str)


def test_think_field_rejection_is_retried_without_it(monkeypatch):
    """An older server that rejects ``think`` must not cost us the narrative."""
    calls = {"n": 0}
    sections = {"ok": True}

    def _urlopen(request, timeout=None):
        calls["n"] += 1
        body = json.loads(request.data.decode("utf-8"))
        if "think" in body:
            raise urllib.error.HTTPError(request.full_url, 400, "bad request", {}, None)
        return _Response(json.dumps(_chat_envelope(sections)).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    text = OllamaProvider().complete([{"role": "user", "content": "x"}], timeout=5.0)
    assert calls["n"] == 2
    assert json.loads(text) == sections


# --------------------------------------------------------------------------- #
# Health check
# --------------------------------------------------------------------------- #

def test_health_reports_ready_when_model_is_installed(monkeypatch):
    _patch(monkeypatch, _Server(tags={"models": [{"name": "qwen3:4b"}]}))
    health = check_health(model="qwen3:4b")
    assert (health.reachable, health.model_installed) == (True, True)
    assert health.state == LOCAL_AI_READY
    assert health.model == "qwen3:4b"
    assert health.endpoint == DEFAULT_BASE_URL
    assert health.error == ""


def test_health_reports_model_missing(monkeypatch):
    _patch(monkeypatch, _Server(tags={"models": [{"name": "llama3:8b"}]}))
    health = check_health(model="qwen3:4b")
    assert (health.reachable, health.model_installed) == (True, False)
    assert health.state == LOCAL_AI_MODEL_MISSING


def test_health_tolerates_the_latest_suffix(monkeypatch):
    _patch(monkeypatch, _Server(tags={"models": [{"name": "qwen3:4b:latest"}]}))
    assert check_health(model="qwen3:4b").model_installed is True


def test_health_reports_unavailable_without_raising(monkeypatch):
    def _refused(*args, **kwargs):
        raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))

    monkeypatch.setattr("urllib.request.urlopen", _refused)
    health = check_health(model="qwen3:4b")
    assert health.reachable is False
    assert health.state == LOCAL_AI_UNAVAILABLE
    assert health.error == "ollama_unreachable"


def test_health_reports_only_sanitized_fields(monkeypatch):
    _patch(monkeypatch, _Server(tags={
        "models": [{"name": "qwen3:4b", "digest": "sha256:secretdigest",
                    "details": {"parent_model": "internal"}}]}))
    health = check_health(model="qwen3:4b")
    blob = json.dumps(health.__dict__, default=str)
    assert "secretdigest" not in blob and "parent_model" not in blob


def test_health_probe_uses_the_tags_endpoint(monkeypatch):
    server = _patch(monkeypatch, _Server())
    check_health(model="qwen3:4b")
    assert server.requests[0][0].full_url == "http://127.0.0.1:11434/api/tags"
    assert server.requests[0][0].get_method() == "GET"


def test_ui_status_is_absent_unless_a_local_provider_is_configured(monkeypatch):
    from dashboard.ai_stock_analysis_components import local_ai_status

    def _explode(*args, **kwargs):
        raise AssertionError("no probe may run when the layer is disabled")

    monkeypatch.setattr("urllib.request.urlopen", _explode)
    assert local_ai_status(NarrativeConfig(enabled=False)) is None
    assert local_ai_status(NarrativeConfig(enabled=True, provider="openai")) is None


@pytest.mark.parametrize("tags,expected_state,expected_en", [
    ({"models": [{"name": "qwen3:4b"}]}, LOCAL_AI_READY, "Local AI Ready"),
    ({"models": []}, LOCAL_AI_MODEL_MISSING, "Local AI Model Missing"),
])
def test_ui_status_labels(monkeypatch, tags, expected_state, expected_en):
    from dashboard.ai_stock_analysis_components import local_ai_status

    _patch(monkeypatch, _Server(tags=tags))
    state, _arabic, english, _tone = local_ai_status(OLLAMA_CONFIG)
    assert state == expected_state and english == expected_en


def test_ui_status_reports_unavailable_on_a_dead_server(monkeypatch):
    from dashboard.ai_stock_analysis_components import local_ai_status

    def _refused(*args, **kwargs):
        raise urllib.error.URLError("down")

    monkeypatch.setattr("urllib.request.urlopen", _refused)
    state, _arabic, english, _tone = local_ai_status(OLLAMA_CONFIG)
    assert state == LOCAL_AI_UNAVAILABLE and english == "Local AI Unavailable"


# --------------------------------------------------------------------------- #
# Caching, service integration, card
# --------------------------------------------------------------------------- #

def test_cache_hit_makes_no_second_request(result, monkeypatch):
    server = _patch(monkeypatch, _Server(chat=_chat_envelope(_answer(result))))
    cache = NarrativeCache()
    first = _run(result, server, cache=cache)
    second = _run(result, server, cache=cache)
    assert len(server.chat_requests) == 1
    assert second.provenance.cached is True
    assert first.summary == second.summary


def test_changed_evidence_hash_makes_a_new_request(result, monkeypatch):
    cache = NarrativeCache()
    server = _patch(monkeypatch, _Server(chat=_chat_envelope(_answer(result))))
    _run(result, server, cache=cache)

    moved = _evidence(_frame([61.0 + i * 0.15 for i in range(260)]))
    assert moved.evidence_hash != result.evidence_hash
    server.chat_payload = _chat_envelope(_answer(moved))
    narrative = _run(moved, server, cache=cache)
    assert len(server.chat_requests) == 2
    assert narrative.provenance.cached is False


def test_local_ai_failure_does_not_block_the_analysis(monkeypatch, tmp_path):
    from core.ai_stock_analysis_history import AnalysisHistoryStore

    _patch(monkeypatch, _Server(chat_error=urllib.error.URLError("refused")))
    store = AnalysisHistoryStore(tmp_path / "history.jsonl")
    response = analyze_symbol("COMI", history_loader=lambda symbol: _uptrend(),
                              include_live=False, narrative_config=OLLAMA_CONFIG,
                              history_store=store)
    assert response.result.recommendation is not None
    assert response.result.price.close is not None
    assert response.narrative.model == FALLBACK_MODEL
    assert response.narrative.provenance.source == SOURCE_FALLBACK
    assert store.records("COMI")[-1].narrative_source == SOURCE_FALLBACK


def test_card_says_local_ai_honestly(result, monkeypatch):
    from core.analysis_card_generator import narrative_source_label
    from dashboard.ai_stock_analysis_components import (
        build_card_payload,
        generate_card_bytes,
        narrative_source,
        narrative_source_token,
    )

    server = _patch(monkeypatch, _Server(chat=_chat_envelope(_answer(result))))
    narrative = _run(result, server)

    assert narrative_source_token(narrative) == SOURCE_LOCAL_AI
    assert narrative_source(narrative)[1] == "Local AI"
    payload = build_card_payload(result, narrative)
    assert payload.narrative_source == SOURCE_LOCAL_AI
    label = narrative_source_label(payload.narrative_model, payload.narrative_source)
    assert label == "Narrative  Local AI  ·  qwen3:4b"
    assert generate_card_bytes(result, narrative)[:8] == b"\x89PNG\r\n\x1a\n"


def test_fallback_card_still_says_deterministic(result, monkeypatch):
    from core.analysis_card_generator import narrative_source_label
    from dashboard.ai_stock_analysis_components import build_card_payload

    _patch(monkeypatch, _Server(chat_error=urllib.error.URLError("refused")))
    narrative = _run(result, None)
    payload = build_card_payload(result, narrative)
    assert narrative_source_label(payload.narrative_model, payload.narrative_source) == \
        "Narrative  Deterministic Fallback"


def test_technical_rows_show_the_local_endpoint(result, monkeypatch):
    from dashboard.ai_stock_analysis_components import narrative_technical_rows
    from core.ai_narrative_prompt import SYSTEM_PROMPT

    server = _patch(monkeypatch, _Server(chat=_chat_envelope(_answer(result))))
    narrative = _run(result, server)
    text = json.dumps(narrative_technical_rows(narrative), ensure_ascii=False)
    assert "ollama" in text and "qwen3:4b" in text
    assert "127.0.0.1:11434" in text
    assert SYSTEM_PROMPT[:40] not in text            # the prompt is never displayed


# --------------------------------------------------------------------------- #
# Safety posture
# --------------------------------------------------------------------------- #

def test_openai_remains_available_but_is_not_selected(monkeypatch):
    """The paid adapter still exists; nothing selects it unless configured."""
    assert build_provider(NarrativeConfig(enabled=True, provider="ollama")).name == "ollama"
    assert build_provider(NarrativeConfig(enabled=True, provider="openai")).name == "openai"
    assert NarrativeConfig.from_env({}).mode is NarrativeMode.DISABLED
    env = {"AI_NARRATIVE_ENABLED": "true", "AI_NARRATIVE_PROVIDER": "ollama",
           "AI_NARRATIVE_MODEL": "qwen3:4b"}
    assert NarrativeConfig.from_env(env).provider == "ollama"


def test_env_example_documents_the_local_default_without_secrets():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    assert "AI_NARRATIVE_PROVIDER=ollama" in text
    assert "AI_NARRATIVE_OLLAMA_URL=http://127.0.0.1:11434" in text
    assert "qwen3:4b" in text
    for line in text.splitlines():
        if line.startswith(("OPENAI_API_KEY", "AI_NARRATIVE_API_KEY")):
            assert line.split("=", 1)[1].strip() == ""


def test_production_remains_disabled(result, monkeypatch):
    from dashboard.ai_stock_analysis_components import SAFETY_BADGES
    from core.ai_narrative_prompt import SYSTEM_PROMPT

    server = _patch(monkeypatch, _Server(chat=_chat_envelope(_answer(result))))
    narrative = _run(result, server)
    assert "التنفيذ الحقيقي والوسيط غير مفعّلين" in narrative.disclaimer
    assert any("Production Disabled" in badge[1] for badge in SAFETY_BADGES)
    assert "DISABLED" in SYSTEM_PROMPT
