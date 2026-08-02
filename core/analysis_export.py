"""One export entry point: choose a card type, render its bytes exactly once.

The Streamlit preview and the download button must show the same image. That is
only guaranteed if the bytes are produced ONCE and reused, so this module owns
both the deterministic cache key and the single render call.

The key includes everything that can change the image — ticker, analysis
identity, completed session, card type, language, width, height and the
presentation-model version — so switching any control invalidates the previous
image and a card from a previously selected stock can never be shown.

No provider, network or database access; the caller supplies the already-built
presentation model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.analysis_infographic import (
    INFOGRAPHIC_SIZES,
    LANGUAGES,
    render_infographic_png,
)
from core.analysis_presentation import AnalysisPresentation

#: Bump when the presentation model or any renderer changes shape, so a cached
#: image from an older build is never reused.
PRESENTATION_MODEL_VERSION = "infographic@2"

COMPACT = "COMPACT"
INFOGRAPHIC = "INFOGRAPHIC"
EXTENDED = "EXTENDED"

CARD_TYPES = (COMPACT, INFOGRAPHIC, EXTENDED)

CARD_TYPE_LABELS = {
    COMPACT: "بطاقة مختصرة · Compact",
    INFOGRAPHIC: "إنفوجرافيك تفصيلي · Infographic",
    EXTENDED: "إنفوجرافيك ممتد · Extended Infographic",
}

#: Resolutions each card type genuinely supports. Nothing unsupported is exposed:
#: the Extended layout is authored for 1080x2400 only, so no second option is
#: offered for it rather than shipping a size the layout cannot fill.
CARD_TYPE_RESOLUTIONS = {
    INFOGRAPHIC: ("INFOGRAPHIC", "INFOGRAPHIC_HD"),
    EXTENDED: ("INFOGRAPHIC_EXTENDED",),
}

RESOLUTION_LABELS = {
    "INFOGRAPHIC": "1080×1920",
    "INFOGRAPHIC_HD": "1350×2400",
    "INFOGRAPHIC_EXTENDED": "1080×2400",
}

LANGUAGE_LABELS = {
    "AR": "العربية", "EN": "English", "BILINGUAL": "ثنائي اللغة · Bilingual",
}

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class ExportUnavailable(RuntimeError):
    """The requested card could not be rendered. AI Analysis stays usable."""


@dataclass(frozen=True)
class ExportRequest:
    card_type: str
    language: str = "AR"
    resolution: str = "INFOGRAPHIC"

    def validate(self) -> None:
        if self.card_type not in CARD_TYPES:
            raise ExportUnavailable(f"unknown card type {self.card_type!r}")
        if self.language not in LANGUAGES:
            raise ExportUnavailable(f"unknown language {self.language!r}")
        if self.card_type == COMPACT:
            return
        allowed = CARD_TYPE_RESOLUTIONS[self.card_type]
        if self.resolution not in allowed:
            raise ExportUnavailable(
                f"{self.card_type} does not support {self.resolution!r}; "
                f"expected one of {allowed}")


def resolutions_for(card_type: str) -> tuple:
    """Only the resolutions this card type actually supports."""

    return CARD_TYPE_RESOLUTIONS.get(card_type, ())


def export_cache_key(presentation: AnalysisPresentation, request: ExportRequest) -> str:
    """Deterministic identity of one rendered image.

    Any change to ticker, analysis identity, session, card type, language or
    canvas size produces a different key, so a stale image is never displayed.
    """

    width, height = INFOGRAPHIC_SIZES.get(request.resolution, (0, 0))
    return "|".join((
        PRESENTATION_MODEL_VERSION,
        presentation.ticker,
        presentation.analysis_date,
        presentation.last_completed_session,
        presentation.evidence_hash or "",
        request.card_type,
        request.language,
        str(width), str(height),
    ))


def export_filename(presentation: AnalysisPresentation, request: ExportRequest) -> str:
    """``EGX_AI_RAYA_2026-07-30_AR_1080x1920.png`` — sanitised, never the name."""

    width, height = INFOGRAPHIC_SIZES.get(request.resolution, (0, 0))
    parts = ["EGX_AI", presentation.ticker,
             presentation.last_completed_session, request.language]
    if width and height:
        parts.append(f"{width}x{height}")
    stem = _UNSAFE.sub("_", "_".join(str(part) for part in parts)).strip("_")
    return f"{stem}.png"


def render_export(presentation: AnalysisPresentation, request: ExportRequest, *,
                  compact_renderer=None) -> bytes:
    """Render ONCE. The caller reuses these bytes for preview and download."""

    request.validate()
    if presentation is None:
        raise ExportUnavailable("no completed analysis is available to export")
    try:
        if request.card_type == COMPACT:
            if compact_renderer is None:
                raise ExportUnavailable("compact renderer was not supplied")
            return compact_renderer()
        return render_infographic_png(presentation, size=request.resolution,
                                      language=request.language)
    except ExportUnavailable:
        raise
    except Exception as error:                       # noqa: BLE001 - reported locally
        raise ExportUnavailable(
            f"card rendering failed locally: {type(error).__name__}: {error}") from error


__all__ = [
    "CARD_TYPES", "CARD_TYPE_LABELS", "CARD_TYPE_RESOLUTIONS", "COMPACT",
    "EXTENDED", "INFOGRAPHIC", "LANGUAGE_LABELS", "PRESENTATION_MODEL_VERSION",
    "RESOLUTION_LABELS", "ExportRequest", "ExportUnavailable", "export_cache_key",
    "export_filename", "render_export", "resolutions_for",
]
