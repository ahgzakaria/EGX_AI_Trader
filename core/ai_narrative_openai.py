"""OpenAI **Responses API** adapter with strict Structured Outputs.

The narrative contract is enforced at three levels, not one:

  1. **Transport-level schema** — the request carries a strict JSON Schema
     (``text.format.type = "json_schema"``, ``strict: true``,
     ``additionalProperties: false``, every section listed in ``required``), so the model
     is constrained to the eight Arabic sections rather than merely asked for them.
  2. **Refusal detection** — a Structured-Outputs refusal arrives as a ``refusal`` content
     item, not as prose. It is detected explicitly and turned into a typed error so a
     refusal can never be mistaken for a narrative.
  3. **The evidence validator** — everything that survives here is still re-validated
     against the numeric allow-list and wording rules before a user sees it.

The request is deliberately minimal and stateless: no tools, no web search, no file
search, no conversation persistence (``store: false``), no ``previous_response_id``, and no
context beyond the two messages built from the sanitized evidence payload.

Secret handling: the API key is read from the environment at call time, sent only in the
``Authorization`` header, and never stored, returned, logged, or included in an error.
Every failure is a typed error whose ``reason`` is a fixed token or an HTTP status code —
provider error bodies are read for status only and are never propagated.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request

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

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4.1-mini"
RESPONSES_PATH = "/responses"

# Standard key first; the project-specific name is a documented, optional alias.
API_KEY_ENVS = ("OPENAI_API_KEY", "AI_NARRATIVE_API_KEY")

SCHEMA_NAME = "egx_ai_narrative"
MAX_OUTPUT_TOKENS = 1100
TEMPERATURE = 0.2


def _api_key() -> str:
    for name in API_KEY_ENVS:
        value = str(os.environ.get(name, "") or "").strip()
        if value:
            return value
    return ""


def build_response_schema() -> dict:
    """The strict JSON Schema the model must satisfy: exactly the eight Arabic sections."""
    return {
        "type": "object",
        "properties": {
            name: {"type": "string", "description": f"Concise Arabic text for {name}."}
            for name in REQUIRED_SECTIONS
        },
        "required": list(REQUIRED_SECTIONS),
        "additionalProperties": False,
    }


class OpenAIResponsesProvider:
    """Adapter for the OpenAI Responses API (``POST /v1/responses``)."""

    name = "openai"

    def __init__(self, *, model: str | None = None, base_url: str | None = None):
        self.model = str(model or os.environ.get("AI_NARRATIVE_MODEL", "")
                         or DEFAULT_MODEL).strip()
        self.base_url = str(base_url or os.environ.get("AI_NARRATIVE_BASE_URL", "")
                            or DEFAULT_BASE_URL).strip().rstrip("/")

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}{RESPONSES_PATH}"

    # -- credential ---------------------------------------------------------- #

    def is_configured(self) -> bool:
        """True when an API key is present in the environment. Never returns the key."""
        return bool(_api_key())

    # -- request ------------------------------------------------------------- #

    def build_request_body(self, messages: list[dict]) -> dict:
        """Compose the Responses API payload. Stateless, tool-free, strictly typed."""
        system = "\n\n".join(str(m.get("content", "")) for m in messages
                             if m.get("role") == "system")
        turns = [{"role": str(m.get("role")), "content": str(m.get("content", ""))}
                 for m in messages if m.get("role") != "system"]
        return {
            "model": self.model,
            "instructions": system,
            "input": turns,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": SCHEMA_NAME,
                    "strict": True,
                    "schema": build_response_schema(),
                },
            },
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "temperature": TEMPERATURE,
            # No tools of any kind: no function calling, no web search, no file search.
            "tools": [],
            # No conversation persistence and no prior context.
            "store": False,
        }

    # -- transport ----------------------------------------------------------- #

    def _post(self, body: dict, *, timeout: float) -> dict:
        key = _api_key()
        if not key:
            raise ProviderNotConfigured()
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:      # status only — the body is discarded
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

    # -- response extraction -------------------------------------------------- #

    @staticmethod
    def extract_output_text(envelope: dict) -> str:
        """Return the structured JSON text, or raise a typed error.

        Only assistant message text is read. A ``refusal`` item raises
        :class:`ProviderRefused`; an incomplete or empty response raises rather than
        returning partial content. Provider internals (``id``, ``usage``, model metadata)
        are never carried out of this function.
        """
        if envelope.get("status") == "incomplete":
            raise ProviderMalformedResponse("incomplete_response")

        chunks: list[str] = []
        for item in envelope.get("output") or ():
            if not isinstance(item, dict) or item.get("type") != "message":
                continue                                   # ignore any non-message item
            for block in item.get("content") or ():
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "refusal":
                    # A model refusal is never treated as narrative content.
                    raise ProviderRefused()
                if block.get("type") in ("output_text", "text"):
                    chunks.append(str(block.get("text") or ""))

        text = "".join(chunks).strip()
        if not text:
            fallback = envelope.get("output_text")
            text = "".join(fallback).strip() if isinstance(fallback, list) else \
                str(fallback or "").strip()
        if not text:
            raise ProviderEmptyResponse()
        return text

    # -- provider interface -------------------------------------------------- #

    def complete(self, messages: list[dict], *, timeout: float) -> str:
        """Return the model's structured JSON text, or raise a typed provider error."""
        envelope = self._post(self.build_request_body(messages), timeout=timeout)
        return self.extract_output_text(envelope)


# Backwards-compatible alias for the previous chat-completions class name.
OpenAICompatibleProvider = OpenAIResponsesProvider
