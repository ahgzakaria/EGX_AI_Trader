"""OpenAI-compatible chat-completions adapter for the AI narrative layer.

Deliberately thin and dependency-free (stdlib ``urllib`` only): it posts the already-built,
already-sanitized messages and returns the model's raw text. It performs no calculation, no
retry policy (the provider layer owns that), no logging, and no market-data access.

Secret handling:
  * the API key is read from the environment at call time — never from settings JSON,
    never from a constructor default, never written anywhere;
  * :meth:`is_configured` reports only presence, never the value;
  * every failure is converted to a typed error whose ``reason`` is a fixed token or an
    HTTP status code, so a provider message that happened to echo a header or key fragment
    can never propagate into a UI, a history record, or a log line.

The adapter works with any OpenAI-compatible ``/chat/completions`` endpoint via
``AI_NARRATIVE_BASE_URL``.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request

from core.ai_narrative_provider import (
    ProviderEmptyResponse,
    ProviderMalformedResponse,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderTransportError,
    NarrativeProviderError,
)

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
API_KEY_ENVS = ("AI_NARRATIVE_API_KEY", "OPENAI_API_KEY")

# Conservative generation settings: deterministic-leaning, short, JSON-only.
TEMPERATURE = 0.2
MAX_OUTPUT_TOKENS = 1100


def _api_key() -> str:
    for name in API_KEY_ENVS:
        value = str(os.environ.get(name, "") or "").strip()
        if value:
            return value
    return ""


class OpenAICompatibleProvider:
    """Adapter for an OpenAI-compatible chat-completions endpoint."""

    name = "openai"

    def __init__(self, *, model: str | None = None, base_url: str | None = None):
        self.model = str(model or os.environ.get("AI_NARRATIVE_MODEL", "")
                         or DEFAULT_MODEL).strip()
        self.base_url = str(base_url or os.environ.get("AI_NARRATIVE_BASE_URL", "")
                            or DEFAULT_BASE_URL).strip().rstrip("/")

    # -- credential ---------------------------------------------------------- #

    def is_configured(self) -> bool:
        """True when an API key is present in the environment. Never returns the key."""
        return bool(_api_key())

    # -- transport ----------------------------------------------------------- #

    def _post(self, body: dict, *, timeout: float) -> dict:
        key = _api_key()
        if not key:
            raise ProviderNotConfigured()
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:      # status only — body is never read out
            status = int(getattr(error, "code", 0) or 0)
            if status == 429:
                raise ProviderRateLimited() from None
            if status in (401, 403):
                raise ProviderNotConfigured("invalid_api_key") from None
            if status >= 500:
                raise ProviderTransportError(f"http_{status}") from None
            raise NarrativeProviderError(f"http_{status}") from None
        except socket.timeout:
            raise ProviderTimeout() from None
        except urllib.error.URLError as error:
            if isinstance(getattr(error, "reason", None), socket.timeout):
                raise ProviderTimeout() from None
            raise ProviderTransportError() from None
        except (TimeoutError, OSError):
            raise ProviderTransportError() from None

        try:
            decoded = json.loads(payload)
        except (ValueError, TypeError):
            raise ProviderMalformedResponse("unparsable_envelope") from None
        if not isinstance(decoded, dict):
            raise ProviderMalformedResponse("unexpected_envelope") from None
        return decoded

    # -- provider interface -------------------------------------------------- #

    def complete(self, messages: list[dict], *, timeout: float) -> str:
        """Return the model's raw text answer, or raise a typed provider error."""
        envelope = self._post(
            {
                "model": self.model,
                "messages": messages,
                "temperature": TEMPERATURE,
                "max_tokens": MAX_OUTPUT_TOKENS,
                "response_format": {"type": "json_object"},
                "n": 1,
            },
            timeout=timeout,
        )
        try:
            content = envelope["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ProviderMalformedResponse("missing_choice") from None
        text = str(content or "").strip()
        if not text:
            raise ProviderEmptyResponse()
        return text
