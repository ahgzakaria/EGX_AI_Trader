"""Provider-independent orchestration for the optional external AI narrative (Layer 2).

This module owns the whole guarded path and is the ONLY entry point callers need:

    AnalysisResult
      → sanitized evidence payload      (core.ai_narrative_prompt)
      → external provider adapter        (core.ai_narrative_openai, or an injected one)
      → structured response
      → schema + numeric + wording validation  (core.ai_narrative_validator)
      → accepted AI narrative
    …and on ANY failure at any step → the deterministic fallback narrative.

Hard guarantees:

  * The deterministic fallback is always available, so an analysis never fails because of
    the AI layer, and a PNG export never depends on it.
  * Nothing here calculates a market number. The narrative text is checked against the
    evidence; the numbers on screen keep coming from Layer 1 only.
  * No market-data provider, data router, universe scan or Yahoo path is reachable from
    this module — it consumes an already-computed ``AnalysisResult`` and nothing else.
  * Secrets are read from the environment by the adapter alone, are never stored in
    settings/JSON/history/logs, and never appear in a returned reason string: failure
    reasons are built from exception *types* and rule ids, never from raw provider text.
  * When disabled or unconfigured the behaviour is byte-identical to V1 — the same
    deterministic narrative with the same ``FALLBACK_MODEL``.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Protocol, runtime_checkable

from core.ai_analysis_narrative import FALLBACK_MODEL, build_fallback_narrative
from core.ai_narrative_prompt import (
    PROMPT_VERSION,
    REQUIRED_SECTIONS,
    build_evidence_payload,
    build_messages,
)
from core.ai_narrative_validator import validate_response
from core.ai_stock_analysis_contract import (
    AnalysisResult,
    NarrativeProvenance,
    NarrativeResult,
)
from core.egx_session import CAIRO

# Narrative source tokens (also the three exact UI states).
SOURCE_AI = "AI_NARRATIVE"
SOURCE_FALLBACK = "DETERMINISTIC_FALLBACK"
SOURCE_UNAVAILABLE = "AI_UNAVAILABLE"

NOT_ATTEMPTED = "NOT_ATTEMPTED"


class NarrativeMode(str, Enum):
    DISABLED = "disabled"
    EXTERNAL_AI = "external_ai"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"


# --------------------------------------------------------------------------- #
# Errors — every one carries a SAFE reason code, never provider text
# --------------------------------------------------------------------------- #

class NarrativeProviderError(Exception):
    """Base class. ``reason`` is safe to log, store and display."""
    reason = "provider_error"
    transient = False

    def __init__(self, reason: str | None = None):
        super().__init__(reason or self.reason)
        self.reason = reason or self.reason


class ProviderNotConfigured(NarrativeProviderError):
    reason = "missing_api_key"


class UnsupportedProvider(NarrativeProviderError):
    reason = "unsupported_provider"


class ProviderTimeout(NarrativeProviderError):
    reason = "timeout"
    transient = True


class ProviderRateLimited(NarrativeProviderError):
    reason = "rate_limited"
    transient = True


class ProviderTransportError(NarrativeProviderError):
    reason = "connection_failure"
    transient = True


class ProviderEmptyResponse(NarrativeProviderError):
    reason = "empty_response"
    transient = True


class ProviderMalformedResponse(NarrativeProviderError):
    reason = "malformed_response"


class ProviderRefused(NarrativeProviderError):
    """The model declined to answer (a Structured-Outputs refusal, never narrative)."""
    reason = "model_refusal"


# --------------------------------------------------------------------------- #
# Provider interface
# --------------------------------------------------------------------------- #

@runtime_checkable
class AINarrativeProvider(Protocol):
    """Minimal contract every adapter implements. No market data, no calculation."""

    name: str
    model: str

    def is_configured(self) -> bool:
        """True when a credential is present. Never returns or logs the credential."""

    def complete(self, messages: list[dict], *, timeout: float) -> str:
        """Return the model's raw text answer, or raise a ``NarrativeProviderError``."""


# --------------------------------------------------------------------------- #
# Configuration (environment only — never settings JSON, never source)
# --------------------------------------------------------------------------- #

_TRUE = {"1", "true", "yes", "on"}
_NULL_PROVIDERS = {"", "none", "off", "disabled"}
_FALLBACK_PROVIDERS = {"deterministic", "fallback", "deterministic_fallback"}


def _flag(env: dict, key: str, default: bool) -> bool:
    raw = env.get(key)
    return default if raw is None or not str(raw).strip() else str(raw).strip().lower() in _TRUE


def _number(env: dict, key: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(str(env.get(key, "")).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class NarrativeConfig:
    """Runtime configuration for the narrative layer. Contains NO secret material."""
    enabled: bool = False
    provider: str = "none"
    model: str = ""
    timeout_seconds: float = 20.0
    max_retries: int = 1
    cache_enabled: bool = True

    @classmethod
    def from_env(cls, env=None) -> "NarrativeConfig":
        source = dict(os.environ if env is None else env)
        return cls(
            enabled=_flag(source, "AI_NARRATIVE_ENABLED", False),
            provider=str(source.get("AI_NARRATIVE_PROVIDER", "none") or "none").strip().lower(),
            model=str(source.get("AI_NARRATIVE_MODEL", "") or "").strip(),
            timeout_seconds=_number(source, "AI_NARRATIVE_TIMEOUT_SECONDS", 20.0,
                                    minimum=1.0, maximum=120.0),
            max_retries=int(_number(source, "AI_NARRATIVE_MAX_RETRIES", 1,
                                    minimum=0, maximum=3)),
            cache_enabled=_flag(source, "AI_NARRATIVE_CACHE_ENABLED", True),
        )

    @property
    def mode(self) -> NarrativeMode:
        if not self.enabled:
            return NarrativeMode.DISABLED
        if self.provider in _NULL_PROVIDERS or self.provider in _FALLBACK_PROVIDERS:
            return NarrativeMode.DETERMINISTIC_FALLBACK
        return NarrativeMode.EXTERNAL_AI


# --------------------------------------------------------------------------- #
# Provider registry
# --------------------------------------------------------------------------- #

ProviderFactory = Callable[[NarrativeConfig], AINarrativeProvider]
_REGISTRY: dict[str, ProviderFactory] = {}


def register_provider(name: str, factory: ProviderFactory) -> None:
    """Register an adapter factory under a configuration name."""
    _REGISTRY[str(name).strip().lower()] = factory


def _openai_factory(config: NarrativeConfig) -> AINarrativeProvider:
    from core.ai_narrative_openai import OpenAIResponsesProvider
    return OpenAIResponsesProvider(model=config.model)


register_provider("openai", _openai_factory)


def build_provider(config: NarrativeConfig) -> AINarrativeProvider:
    """Instantiate the configured adapter. Raises ``UnsupportedProvider`` if unknown."""
    factory = _REGISTRY.get(config.provider)
    if factory is None:
        raise UnsupportedProvider()
    return factory(config)


# --------------------------------------------------------------------------- #
# Narrative cache — keyed by symbol + evidence hash + provider + model + prompt version
# --------------------------------------------------------------------------- #

class NarrativeCache:
    """In-process cache of ACCEPTED narratives only. Failures are never cached."""

    def __init__(self):
        self._entries: dict[tuple, NarrativeResult] = {}

    @staticmethod
    def key(result: AnalysisResult, config: NarrativeConfig, model: str) -> tuple:
        return (result.request.symbol, result.evidence_hash or result.evidence_version,
                config.provider, model or config.model, PROMPT_VERSION)

    def get(self, key: tuple) -> NarrativeResult | None:
        return self._entries.get(key)

    def put(self, key: tuple, narrative: NarrativeResult) -> NarrativeResult:
        self._entries[key] = narrative
        return narrative

    def clear(self) -> None:
        self._entries.clear()


NARRATIVE_CACHE = NarrativeCache()


# --------------------------------------------------------------------------- #
# Response decoding
# --------------------------------------------------------------------------- #

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def decode_response(raw: str):
    """Decode a model answer into a dict, tolerating a surrounding code fence.

    Returns ``None`` when the answer contains no JSON object at all; the validator turns
    that into a schema rejection. No repair of the *content* is ever attempted.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
# Narrative assembly
# --------------------------------------------------------------------------- #

def _now_iso(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(CAIRO).isoformat()


def _fallback(result: AnalysisResult, language: str, *, source: str, reason: str,
              validation_status: str, provider: str = "none", model: str = "",
              latency_ms: int | None = None, now=None) -> NarrativeResult:
    """The V1 deterministic narrative, stamped with honest provenance."""
    narrative = build_fallback_narrative(result, language=language)
    provenance = NarrativeProvenance(
        source=source, provider=provider, model=model, prompt_version=PROMPT_VERSION,
        evidence_hash=result.evidence_hash or "", generated_at=_now_iso(now),
        validation_status=validation_status, latency_ms=latency_ms, cached=False,
        fallback_reason=reason)
    return replace(narrative, provenance=provenance)


def _accepted(result: AnalysisResult, language: str, sections: dict, *, provider: str,
              model: str, latency_ms: int, now=None) -> NarrativeResult:
    """Compose the accepted AI narrative.

    The headline stays deterministic (built from evidence by the Layer-1 fallback writer)
    so the single most prominent line on the page and the PNG can never be model prose;
    the model's eight validated Arabic sections supply everything else.
    """
    base = build_fallback_narrative(result, language=language)
    provenance = NarrativeProvenance(
        source=SOURCE_AI, provider=provider, model=model, prompt_version=PROMPT_VERSION,
        evidence_hash=result.evidence_hash or "", generated_at=_now_iso(now),
        validation_status="VALIDATED", latency_ms=latency_ms, cached=False,
        fallback_reason="")
    return replace(
        base,
        summary=sections["executive_summary_ar"],
        rationale=sections["technical_read_ar"],
        risks=sections["risk_notes_ar"],
        model=model or "ai-narrative",
        sections=tuple((name, sections[name]) for name in REQUIRED_SECTIONS),
        provenance=provenance,
    )


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def build_narrative(
    result: AnalysisResult,
    *,
    config: NarrativeConfig | None = None,
    provider: AINarrativeProvider | None = None,
    language: str = "ar",
    force_refresh: bool = False,
    cache: NarrativeCache | None = None,
    now: datetime | None = None,
) -> NarrativeResult:
    """Return the narrative for ``result`` — validated AI when possible, else fallback.

    Never raises. Every failure mode (disabled, missing key, timeout, rate limit,
    connection failure, malformed output, schema violation, numeric hallucination,
    prohibited wording, unsupported provider, empty response) degrades to the deterministic
    narrative with a safe ``fallback_reason`` recorded on the provenance.
    """
    settings = config or NarrativeConfig.from_env()
    store = NARRATIVE_CACHE if cache is None else cache

    if settings.mode is NarrativeMode.DISABLED:
        return _fallback(result, language, source=SOURCE_FALLBACK, reason="ai_disabled",
                         validation_status=NOT_ATTEMPTED, now=now)
    if settings.mode is NarrativeMode.DETERMINISTIC_FALLBACK:
        return _fallback(result, language, source=SOURCE_FALLBACK,
                         reason="deterministic_fallback_mode",
                         validation_status=NOT_ATTEMPTED, now=now)

    try:
        adapter = provider if provider is not None else build_provider(settings)
    except NarrativeProviderError as error:
        return _fallback(result, language, source=SOURCE_FALLBACK, reason=error.reason,
                         validation_status=NOT_ATTEMPTED, provider=settings.provider,
                         now=now)

    provider_name = str(getattr(adapter, "name", settings.provider) or settings.provider)
    model = str(getattr(adapter, "model", "") or settings.model)

    try:
        configured = bool(adapter.is_configured())
    except Exception:
        configured = False
    if not configured:
        return _fallback(result, language, source=SOURCE_FALLBACK,
                         reason=ProviderNotConfigured.reason,
                         validation_status=NOT_ATTEMPTED, provider=provider_name,
                         model=model, now=now)

    cache_key = NarrativeCache.key(result, settings, model)
    if settings.cache_enabled and not force_refresh:
        cached = store.get(cache_key)
        if cached is not None:
            stamped = replace(cached.provenance, cached=True)
            return replace(cached, provenance=stamped)

    messages = build_messages(build_evidence_payload(result))
    attempts = max(1, int(settings.max_retries) + 1)
    started = time.perf_counter()
    raw = None
    last_reason = "provider_error"

    for attempt in range(attempts):
        try:
            raw = adapter.complete(messages, timeout=float(settings.timeout_seconds))
            break
        except NarrativeProviderError as error:
            last_reason = error.reason
            if not error.transient or attempt == attempts - 1:
                raw = None
                break
        except Exception as error:                      # never trust an adapter to behave
            # Only the exception TYPE is recorded: a raw message could carry a URL, a
            # header, or a key fragment.
            last_reason = f"adapter_error:{type(error).__name__}"
            raw = None
            break

    latency_ms = int((time.perf_counter() - started) * 1000)

    if raw is None:
        return _fallback(result, language, source=SOURCE_FALLBACK, reason=last_reason,
                         validation_status=NOT_ATTEMPTED, provider=provider_name,
                         model=model, latency_ms=latency_ms, now=now)

    decoded = decode_response(raw)
    # Unparsable-but-present text is a schema violation, not an empty response; passing the
    # raw string through lets the validator classify it without ever returning its content.
    payload = decoded if decoded is not None else (str(raw).strip() or None)
    report = validate_response(payload, result)
    if not report.ok:
        # The rejected text is dropped here and never returned, displayed or logged.
        return _fallback(result, language, source=SOURCE_FALLBACK,
                         reason=f"rejected:{report.reason}",
                         validation_status=report.status, provider=provider_name,
                         model=model, latency_ms=latency_ms, now=now)

    narrative = _accepted(result, language, report.sections, provider=provider_name,
                          model=model, latency_ms=latency_ms, now=now)
    if settings.cache_enabled:
        store.put(cache_key, narrative)
    return narrative


def narrative_generator_from_config(
    config: NarrativeConfig | None = None,
    *,
    provider: AINarrativeProvider | None = None,
) -> Callable[[AnalysisResult, str], NarrativeResult] | None:
    """Adapt :func:`build_narrative` to the service's injectable-generator signature.

    Returns ``None`` when the layer is disabled, so the service keeps its exact V1 path.
    """
    settings = config or NarrativeConfig.from_env()
    if settings.mode is NarrativeMode.DISABLED:
        return None

    def _generate(result: AnalysisResult, language: str) -> NarrativeResult:
        return build_narrative(result, config=settings, provider=provider,
                               language=language)

    return _generate


__all__ = [
    "SOURCE_AI", "SOURCE_FALLBACK", "SOURCE_UNAVAILABLE", "NOT_ATTEMPTED",
    "FALLBACK_MODEL", "PROMPT_VERSION",
    "NarrativeMode", "NarrativeConfig", "AINarrativeProvider", "NarrativeCache",
    "NARRATIVE_CACHE", "NarrativeProviderError", "ProviderNotConfigured",
    "UnsupportedProvider", "ProviderTimeout", "ProviderRateLimited",
    "ProviderTransportError", "ProviderEmptyResponse", "ProviderMalformedResponse",
    "ProviderRefused",
    "register_provider", "build_provider", "build_narrative", "decode_response",
    "narrative_generator_from_config",
]
