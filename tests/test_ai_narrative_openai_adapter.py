"""Mocked-HTTP tests for the OpenAI Responses adapter. No network call is ever made.

``urllib.request.urlopen`` is replaced throughout, so the suite exercises the exact request
body and response handling without opening a socket or spending a token.
"""

from __future__ import annotations

import io
import json
import logging
import urllib.error

import pytest

from core.ai_narrative_openai import (
    DEFAULT_BASE_URL,
    RESPONSES_PATH,
    SCHEMA_NAME,
    OpenAIResponsesProvider,
    build_response_schema,
)
from core.ai_narrative_prompt import REQUIRED_SECTIONS
from core.ai_narrative_provider import (
    NarrativeProviderError,
    ProviderEmptyResponse,
    ProviderMalformedResponse,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderRefused,
    ProviderTimeout,
    ProviderTransportError,
)

SECRET_KEY = "sk-test-never-logged-0123456789"
MESSAGES = [
    {"role": "system", "content": "SYSTEM RULES"},
    {"role": "user", "content": "EVIDENCE"},
]


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET_KEY)
    monkeypatch.delenv("AI_NARRATIVE_API_KEY", raising=False)
    monkeypatch.delenv("AI_NARRATIVE_BASE_URL", raising=False)


class _Capture:
    """Stand-in for ``urlopen``: records the request and returns a scripted body."""

    def __init__(self, payload, *, status=200):
        self.payload = payload
        self.status = status
        self.request = None
        self.timeout = None

    def __call__(self, request, timeout=None):
        self.request = request
        self.timeout = timeout
        body = self.payload if isinstance(self.payload, str) else \
            json.dumps(self.payload, ensure_ascii=False)
        return _Response(body)

    # -- convenience accessors ------------------------------------------------ #

    @property
    def body(self) -> dict:
        return json.loads(self.request.data.decode("utf-8"))

    @property
    def headers(self) -> dict:
        return {k.lower(): v for k, v in self.request.headers.items()}


class _Response(io.BytesIO):
    def __init__(self, text: str):
        super().__init__(text.encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _sections() -> dict:
    return {name: f"نص عربي مختصر للقسم {i}" for i, name in enumerate(REQUIRED_SECTIONS)}


def _ok_envelope(sections=None) -> dict:
    return {
        "id": "resp_abc123",
        "object": "response",
        "status": "completed",
        "model": "gpt-4.1-mini-2025-04-14",
        "usage": {"input_tokens": 900, "output_tokens": 300},
        "output": [{
            "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text",
                         "text": json.dumps(sections or _sections(), ensure_ascii=False)}],
        }],
    }


def _patch(monkeypatch, capture):
    monkeypatch.setattr("urllib.request.urlopen", capture)
    return capture


# --------------------------------------------------------------------------- #
# Request shape
# --------------------------------------------------------------------------- #

def test_request_targets_the_responses_endpoint(monkeypatch):
    capture = _patch(monkeypatch, _Capture(_ok_envelope()))
    OpenAIResponsesProvider(model="gpt-4.1-mini").complete(MESSAGES, timeout=7.0)
    assert capture.request.full_url == f"{DEFAULT_BASE_URL}{RESPONSES_PATH}"
    assert capture.request.full_url.endswith("/v1/responses")
    assert capture.request.get_method() == "POST"
    assert capture.timeout == 7.0


def test_authorization_header_is_present_and_never_logged(monkeypatch, caplog):
    capture = _patch(monkeypatch, _Capture(_ok_envelope()))
    caplog.set_level(logging.DEBUG)
    provider = OpenAIResponsesProvider(model="gpt-4.1-mini")
    text = provider.complete(MESSAGES, timeout=5.0)

    assert capture.headers["authorization"] == f"Bearer {SECRET_KEY}"
    # The key exists on the wire and nowhere else.
    assert SECRET_KEY not in caplog.text
    assert SECRET_KEY not in text
    assert SECRET_KEY not in json.dumps(capture.body, ensure_ascii=False)
    assert SECRET_KEY not in json.dumps(provider.__dict__, default=str)


def test_model_comes_from_configuration(monkeypatch):
    capture = _patch(monkeypatch, _Capture(_ok_envelope()))
    OpenAIResponsesProvider(model="configured-model-x").complete(MESSAGES, timeout=5.0)
    assert capture.body["model"] == "configured-model-x"


def test_strict_json_schema_is_sent(monkeypatch):
    capture = _patch(monkeypatch, _Capture(_ok_envelope()))
    OpenAIResponsesProvider(model="gpt-4.1-mini").complete(MESSAGES, timeout=5.0)

    fmt = capture.body["text"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["name"] == SCHEMA_NAME
    assert fmt["strict"] is True
    schema = fmt["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(REQUIRED_SECTIONS)
    assert set(schema["properties"]) == set(REQUIRED_SECTIONS)


def test_schema_helper_lists_every_section_explicitly():
    schema = build_response_schema()
    assert schema["required"] == list(REQUIRED_SECTIONS)
    assert schema["additionalProperties"] is False
    for name in REQUIRED_SECTIONS:
        section = schema["properties"][name]
        assert section["type"] == "object"
        assert section["additionalProperties"] is False
        assert section["required"] == ["qualitative_text_ar", "fact_refs"]
        assert section["properties"]["qualitative_text_ar"]["type"] == "string"
        assert section["properties"]["fact_refs"]["type"] == "array"


def test_caller_supplied_schema_is_sent_verbatim(monkeypatch):
    """The per-analysis schema (with its fact_refs enums) must reach the provider."""
    capture = _patch(monkeypatch, _Capture(_ok_envelope()))
    per_analysis = {"type": "object", "properties": {}, "required": [],
                    "additionalProperties": False, "marker": "per-analysis"}
    OpenAIResponsesProvider(model="gpt-4.1-mini").complete(
        MESSAGES, timeout=5.0, schema=per_analysis)
    assert capture.body["text"]["format"]["schema"] == per_analysis


def test_no_tools_and_no_conversation_persistence(monkeypatch):
    capture = _patch(monkeypatch, _Capture(_ok_envelope()))
    OpenAIResponsesProvider(model="gpt-4.1-mini").complete(MESSAGES, timeout=5.0)

    body = capture.body
    assert body["tools"] == []
    assert body["store"] is False
    assert "previous_response_id" not in body
    assert "conversation" not in body
    serialized = json.dumps(body)
    for forbidden in ("web_search", "file_search", "code_interpreter", "function",
                      "tool_choice"):
        assert forbidden not in serialized


def test_system_and_user_turns_are_separated(monkeypatch):
    capture = _patch(monkeypatch, _Capture(_ok_envelope()))
    OpenAIResponsesProvider(model="gpt-4.1-mini").complete(MESSAGES, timeout=5.0)
    body = capture.body
    assert body["instructions"] == "SYSTEM RULES"
    assert body["input"] == [{"role": "user", "content": "EVIDENCE"}]


# --------------------------------------------------------------------------- #
# Response handling
# --------------------------------------------------------------------------- #

def test_valid_structured_output_is_parsed(monkeypatch):
    sections = _sections()
    _patch(monkeypatch, _Capture(_ok_envelope(sections)))
    text = OpenAIResponsesProvider(model="gpt-4.1-mini").complete(MESSAGES, timeout=5.0)
    assert json.loads(text) == sections


def test_provider_internals_never_reach_the_returned_text(monkeypatch):
    _patch(monkeypatch, _Capture(_ok_envelope()))
    text = OpenAIResponsesProvider(model="gpt-4.1-mini").complete(MESSAGES, timeout=5.0)
    for internal in ("resp_abc123", "usage", "input_tokens",
                     "gpt-4.1-mini-2025-04-14", "output_text"):
        assert internal not in text


def test_refusal_raises_and_never_returns_content(monkeypatch):
    envelope = _ok_envelope()
    envelope["output"][0]["content"] = [
        {"type": "refusal", "refusal": "I cannot help with that."}]
    _patch(monkeypatch, _Capture(envelope))
    with pytest.raises(ProviderRefused) as excinfo:
        OpenAIResponsesProvider(model="gpt-4.1-mini").complete(MESSAGES, timeout=5.0)
    assert excinfo.value.reason == "model_refusal"
    assert "cannot help" not in str(excinfo.value)


def test_empty_output_is_rejected(monkeypatch):
    envelope = _ok_envelope()
    envelope["output"] = []
    _patch(monkeypatch, _Capture(envelope))
    with pytest.raises(ProviderEmptyResponse):
        OpenAIResponsesProvider(model="gpt-4.1-mini").complete(MESSAGES, timeout=5.0)


def test_incomplete_response_is_rejected(monkeypatch):
    envelope = _ok_envelope()
    envelope["status"] = "incomplete"
    envelope["incomplete_details"] = {"reason": "max_output_tokens"}
    _patch(monkeypatch, _Capture(envelope))
    with pytest.raises(ProviderMalformedResponse):
        OpenAIResponsesProvider(model="gpt-4.1-mini").complete(MESSAGES, timeout=5.0)


def test_unparsable_envelope_is_rejected(monkeypatch):
    _patch(monkeypatch, _Capture("<html>gateway error</html>"))
    with pytest.raises(ProviderMalformedResponse) as excinfo:
        OpenAIResponsesProvider(model="gpt-4.1-mini").complete(MESSAGES, timeout=5.0)
    assert excinfo.value.reason == "unparsable_envelope"


def test_non_message_output_items_are_ignored(monkeypatch):
    envelope = _ok_envelope()
    envelope["output"].insert(0, {"type": "reasoning", "summary": ["internal notes"]})
    _patch(monkeypatch, _Capture(envelope))
    text = OpenAIResponsesProvider(model="gpt-4.1-mini").complete(MESSAGES, timeout=5.0)
    assert "internal notes" not in text
    assert json.loads(text).keys() == set(REQUIRED_SECTIONS)


# --------------------------------------------------------------------------- #
# Failure mapping — status codes only, never bodies
# --------------------------------------------------------------------------- #

def _raise_http(status, body=b'{"error":{"message":"sk-leaked-in-body"}}'):
    def _urlopen(*args, **kwargs):
        raise urllib.error.HTTPError("https://api.example/v1/responses", status, "err",
                                     {}, io.BytesIO(body))
    return _urlopen


@pytest.mark.parametrize("status,expected,reason", [
    (429, ProviderRateLimited, "rate_limited"),
    (401, ProviderNotConfigured, "invalid_api_key"),
    (403, ProviderNotConfigured, "invalid_api_key"),
    (503, ProviderTransportError, "http_503"),
    (422, NarrativeProviderError, "http_422"),
])
def test_http_status_maps_to_a_safe_reason(monkeypatch, status, expected, reason):
    monkeypatch.setattr("urllib.request.urlopen", _raise_http(status))
    with pytest.raises(expected) as excinfo:
        OpenAIResponsesProvider(model="gpt-4.1-mini").complete(MESSAGES, timeout=5.0)
    assert excinfo.value.reason == reason


def test_http_error_body_is_never_leaked(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _raise_http(400))
    with pytest.raises(NarrativeProviderError) as excinfo:
        OpenAIResponsesProvider(model="gpt-4.1-mini").complete(MESSAGES, timeout=5.0)
    message = str(excinfo.value)
    assert message == "http_400"
    assert "sk-leaked-in-body" not in message


def test_timeout_and_transport_errors_map_to_typed_errors(monkeypatch):
    import socket

    def _timeout(*args, **kwargs):
        raise socket.timeout()

    def _urlerror(*args, **kwargs):
        raise urllib.error.URLError("dns failure")

    monkeypatch.setattr("urllib.request.urlopen", _timeout)
    with pytest.raises(ProviderTimeout):
        OpenAIResponsesProvider(model="gpt-4.1-mini").complete(MESSAGES, timeout=1.0)

    monkeypatch.setattr("urllib.request.urlopen", _urlerror)
    with pytest.raises(ProviderTransportError):
        OpenAIResponsesProvider(model="gpt-4.1-mini").complete(MESSAGES, timeout=1.0)


def test_missing_key_raises_before_any_request(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def _explode(*args, **kwargs):
        raise AssertionError("no request may be sent without a key")

    monkeypatch.setattr("urllib.request.urlopen", _explode)
    provider = OpenAIResponsesProvider(model="gpt-4.1-mini")
    assert provider.is_configured() is False
    with pytest.raises(ProviderNotConfigured):
        provider.complete(MESSAGES, timeout=5.0)


def test_standard_env_key_is_preferred_and_alias_is_supported(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AI_NARRATIVE_API_KEY", "sk-alias")
    assert OpenAIResponsesProvider(model="m").is_configured() is True
    monkeypatch.setenv("OPENAI_API_KEY", "sk-standard")
    capture = _patch(monkeypatch, _Capture(_ok_envelope()))
    OpenAIResponsesProvider(model="m").complete(MESSAGES, timeout=5.0)
    assert capture.headers["authorization"] == "Bearer sk-standard"
