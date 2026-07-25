"""Local **Ollama** adapter — the free, offline narrative provider.

Talks to an Ollama server on the loopback interface (``POST /api/chat``) using the same
guarded contract as every other provider: it receives the already-sanitized, number-free
messages and the per-analysis strict JSON Schema produced by the fact-binding layer, and
returns raw text for the validator to judge. It calculates nothing and decides nothing.

Why this is the operational default:

  * **Free and offline** — no API key exists, none is read, and nothing leaves the
    machine. The endpoint is pinned to loopback; a non-local URL is refused unless the
    operator explicitly opts in via ``AI_NARRATIVE_OLLAMA_ALLOW_REMOTE``.
  * **Ollama Cloud is never used** — only the local server is contacted.
  * **Structured output** — the request carries ``format: <schema>`` so the model is
    constrained to the eight ``{qualitative_text_ar, fact_refs}`` sections, with
    ``stream: false`` so exactly one complete document is parsed.
  * **No tools** — no function calling, no web tool, no file tool, no retrieval.
  * **No reasoning traces** — Qwen-family thinking is disabled via ``think: false``, and
    only ``message.content`` is ever read. A ``thinking`` field, if a server sends one, is
    ignored: never parsed, never returned, never stored, never displayed.

Failures are typed and safe (a fixed token or an HTTP status), so the caller always
degrades to the Deterministic Fallback and no server text is ever propagated.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from core.ai_narrative_provider import (
    NarrativeProviderError,
    ProviderEmptyResponse,
    ProviderMalformedResponse,
    ProviderNotConfigured,
    ProviderTimeout,
    ProviderTransportError,
)

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:4b"
CHAT_PATH = "/api/chat"
TAGS_PATH = "/api/tags"

# Loopback only unless the operator explicitly opts in.
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]", "0.0.0.0"})

DEFAULT_TEMPERATURE = 0.2
DEFAULT_KEEP_ALIVE = "5m"
MAX_OUTPUT_TOKENS = 1100
HEALTH_TIMEOUT_SECONDS = 3.0

# Health states surfaced to the UI.
LOCAL_AI_READY = "LOCAL_AI_READY"
LOCAL_AI_MODEL_MISSING = "LOCAL_AI_MODEL_MISSING"
LOCAL_AI_UNAVAILABLE = "LOCAL_AI_UNAVAILABLE"


class ProviderModelMissing(NarrativeProviderError):
    """The requested model is not installed on the local Ollama server."""
    reason = "model_not_installed"


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _is_local(url: str) -> bool:
    """True when ``url`` points at the loopback interface."""
    try:
        host = (urllib.parse.urlsplit(url).hostname or "").strip().lower()
    except ValueError:
        return False
    return host in LOCAL_HOSTS


@dataclass(frozen=True)
class OllamaHealth:
    """Safe health summary. Carries no server text beyond a sanitized error token."""
    reachable: bool
    model_installed: bool
    model: str
    endpoint: str
    error: str = ""

    @property
    def state(self) -> str:
        if not self.reachable:
            return LOCAL_AI_UNAVAILABLE
        return LOCAL_AI_READY if self.model_installed else LOCAL_AI_MODEL_MISSING


class OllamaProvider:
    """Adapter for a local Ollama server. No API key, no cloud, no tools."""

    name = "ollama"

    def __init__(self, *, model: str | None = None, base_url: str | None = None,
                 temperature: float | None = None, keep_alive: str | None = None,
                 think: bool | None = None, allow_remote: bool | None = None):
        self.model = str(model or os.environ.get("AI_NARRATIVE_MODEL", "")
                         or DEFAULT_MODEL).strip()
        self.base_url = str(base_url or os.environ.get("AI_NARRATIVE_OLLAMA_URL", "")
                            or DEFAULT_BASE_URL).strip().rstrip("/")
        self.temperature = float(temperature if temperature is not None
                                 else _number("AI_NARRATIVE_OLLAMA_TEMPERATURE",
                                              DEFAULT_TEMPERATURE))
        self.keep_alive = str(keep_alive or os.environ.get("AI_NARRATIVE_OLLAMA_KEEP_ALIVE", "")
                              or DEFAULT_KEEP_ALIVE).strip()
        # ``think`` False asks Qwen-family models for a final answer with no reasoning.
        self.disable_thinking = (not bool(think)) if think is not None else \
            _flag("AI_NARRATIVE_OLLAMA_DISABLE_THINKING", True)
        self.allow_remote = bool(allow_remote) if allow_remote is not None else \
            _flag("AI_NARRATIVE_OLLAMA_ALLOW_REMOTE", False)
        # Set to False after a server rejects the ``think`` field, so the retry omits it.
        self._think_field_supported = True

    # -- endpoints ----------------------------------------------------------- #

    @property
    def chat_endpoint(self) -> str:
        return f"{self.base_url}{CHAT_PATH}"

    @property
    def tags_endpoint(self) -> str:
        return f"{self.base_url}{TAGS_PATH}"

    @property
    def endpoint_allowed(self) -> bool:
        """Loopback only, unless remote access was explicitly enabled."""
        return self.allow_remote or _is_local(self.base_url)

    # -- credential (there is none) ------------------------------------------- #

    def is_configured(self) -> bool:
        """True when the endpoint is permitted. A local provider needs no API key."""
        return self.endpoint_allowed

    # -- request ------------------------------------------------------------- #

    def build_request_body(self, messages: list[dict], schema: dict | None = None,
                           *, include_think: bool | None = None) -> dict:
        """Compose the /api/chat payload: one shot, schema-constrained, tool-free."""
        body = {
            "model": self.model,
            "messages": [{"role": str(m.get("role")),
                          "content": str(m.get("content", ""))} for m in messages],
            # One complete document, never a token stream.
            "stream": False,
            # No tools of any kind: no function calling, no web, no file, no retrieval.
            "tools": [],
            "keep_alive": self.keep_alive,
            "options": {"temperature": self.temperature,
                        "num_predict": MAX_OUTPUT_TOKENS},
        }
        if schema is not None:
            body["format"] = schema
        think = self._think_field_supported if include_think is None else include_think
        if self.disable_thinking and think:
            body["think"] = False
        return body

    # -- transport ----------------------------------------------------------- #

    def _request(self, url: str, body: dict | None, *, timeout: float) -> dict:
        if not self.endpoint_allowed:
            raise ProviderNotConfigured("remote_ollama_url_not_allowed")
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:      # status only — body is discarded
            status = int(getattr(error, "code", 0) or 0)
            if status == 404:
                # Ollama answers 404 for an un-pulled model.
                raise ProviderModelMissing() from None
            if status >= 500:
                raise ProviderTransportError(f"http_{status}") from None
            raise NarrativeProviderError(f"http_{status}") from None
        except socket.timeout:
            raise ProviderTimeout() from None
        except urllib.error.URLError as error:
            if isinstance(getattr(error, "reason", None), socket.timeout):
                raise ProviderTimeout() from None
            raise ProviderTransportError("ollama_unreachable") from None
        except (TimeoutError, OSError):
            raise ProviderTransportError("ollama_unreachable") from None

        try:
            decoded = json.loads(payload)
        except (ValueError, TypeError):
            raise ProviderMalformedResponse("unparsable_envelope") from None
        if not isinstance(decoded, dict):
            raise ProviderMalformedResponse("unexpected_envelope") from None
        return decoded

    # -- response extraction --------------------------------------------------- #

    @staticmethod
    def extract_content(envelope: dict) -> str:
        """Return ``message.content`` only.

        A ``thinking`` field (Qwen-family reasoning trace) is deliberately not read: it is
        never parsed, returned, logged or stored. An error envelope or an empty answer
        raises rather than returning partial content.
        """
        if envelope.get("error"):
            # Ollama reports a missing model in the body on some versions.
            raise ProviderModelMissing()
        message = envelope.get("message")
        if not isinstance(message, dict):
            raise ProviderMalformedResponse("missing_message")
        text = str(message.get("content") or "").strip()
        if not text:
            raise ProviderEmptyResponse()
        return text

    # -- provider interface ---------------------------------------------------- #

    def complete(self, messages: list[dict], *, timeout: float,
                 schema: dict | None = None) -> str:
        """Return the model's structured JSON text, or raise a typed provider error."""
        body = self.build_request_body(messages, schema)
        try:
            envelope = self._request(self.chat_endpoint, body, timeout=timeout)
        except NarrativeProviderError as error:
            # An older server may reject the ``think`` field; retry once without it.
            if "think" in body and str(error.reason).startswith("http_4"):
                self._think_field_supported = False
                retry = self.build_request_body(messages, schema, include_think=False)
                envelope = self._request(self.chat_endpoint, retry, timeout=timeout)
            else:
                raise
        return self.extract_content(envelope)

    # -- health ----------------------------------------------------------------- #

    def health(self, *, timeout: float = HEALTH_TIMEOUT_SECONDS) -> OllamaHealth:
        """Read-only local check. Never raises; never blocks deterministic analysis."""
        if not self.endpoint_allowed:
            return OllamaHealth(reachable=False, model_installed=False, model=self.model,
                                endpoint=self.base_url,
                                error="remote_ollama_url_not_allowed")
        try:
            envelope = self._request(self.tags_endpoint, None, timeout=timeout)
        except NarrativeProviderError as error:
            return OllamaHealth(reachable=False, model_installed=False, model=self.model,
                                endpoint=self.base_url, error=str(error.reason))
        except Exception as error:                    # never let a probe break the page
            return OllamaHealth(reachable=False, model_installed=False, model=self.model,
                                endpoint=self.base_url,
                                error=f"probe_error:{type(error).__name__}")
        return OllamaHealth(reachable=True, model_installed=self._installed(envelope),
                            model=self.model, endpoint=self.base_url)

    def _installed(self, envelope: dict) -> bool:
        wanted = _normalize_model(self.model)
        for entry in envelope.get("models") or ():
            if not isinstance(entry, dict):
                continue
            for key in ("name", "model"):
                if _normalize_model(entry.get(key)) == wanted:
                    return True
        return False


def _normalize_model(name) -> str:
    """``qwen3:4b`` and ``qwen3:4b:latest``-style variants compare equal."""
    text = str(name or "").strip().lower()
    return text[:-7] if text.endswith(":latest") else text


def _number(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, "")).strip())
    except (TypeError, ValueError):
        return default


def check_health(*, model: str | None = None, base_url: str | None = None,
                 timeout: float = HEALTH_TIMEOUT_SECONDS) -> OllamaHealth:
    """Convenience probe used by the UI. Read-only, local, and never raises."""
    return OllamaProvider(model=model, base_url=base_url).health(timeout=timeout)
