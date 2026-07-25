"""Append-only history store for completed AI Stock Analysis runs.

Each completed analysis writes exactly one immutable JSONL line capturing evidence and
narrative provenance (recommendation, data status, evidence version/hash, confidence,
narrative model). The store is append-only by construction: it never rewrites or deletes
existing lines, so the history is a durable audit trail.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from core.ai_stock_analysis_contract import (
    AnalysisHistoryRecord,
    AnalysisResult,
    DataStatus,
    MarketPhase,
    NarrativeResult,
    Recommendation,
)

DEFAULT_HISTORY_PATH = Path("data/ai_analysis/history.jsonl")


def build_history_record(
    result: AnalysisResult,
    narrative: NarrativeResult | None = None,
    *,
    created_at: str,
    record_id: str | None = None,
) -> AnalysisHistoryRecord:
    """Compose a durable record from evidence (+ optional narrative) provenance."""
    hash_token = (result.evidence_hash or "").split(":")[-1][:12] or "nohash"
    rid = record_id or f"hist@{result.request.symbol}@{created_at}@{hash_token}"
    summary = f"{result.recommendation.value}"
    if result.scenarios:
        primary = result.scenarios[0]
        if primary.entry_low is not None:
            summary += f" · entry {primary.entry_low}"
        elif primary.confirmation_requirements:
            summary += f" · awaiting {primary.confirmation_requirements[0]}"
        elif primary.invalidation_conditions:
            summary += f" · needs {primary.invalidation_conditions[0]}"
    provenance = getattr(narrative, "provenance", None) if narrative else None
    return AnalysisHistoryRecord(
        record_id=rid,
        symbol=result.request.symbol,
        created_at=created_at,
        market_phase=result.market_phase,
        recommendation=result.recommendation,
        data_status=result.data_quality.status,
        evidence_version=result.evidence_version,
        confidence_overall=result.confidence.overall,
        summary_snapshot=summary,
        evidence_hash=result.evidence_hash or "",
        narrative_model=(narrative.model if narrative else None),
        language=result.request.language,
        **_narrative_metadata(provenance),
    )


# Narrative provenance is metadata only: no key, no prompt, no rejected narrative body.
_PROVENANCE_DEFAULTS = {
    "narrative_source": "DETERMINISTIC_FALLBACK",
    "narrative_provider": "none",
    "narrative_prompt_version": "",
    "narrative_validation_status": "NOT_ATTEMPTED",
    "narrative_generated_at": "",
    "narrative_latency_ms": None,
    "narrative_fallback_reason": "",
}


def _narrative_metadata(provenance) -> dict:
    """Flatten a ``NarrativeProvenance`` into record fields (defaults when absent)."""
    if provenance is None:
        return dict(_PROVENANCE_DEFAULTS)
    latency = getattr(provenance, "latency_ms", None)
    return {
        "narrative_source": str(getattr(provenance, "source", "") or
                                _PROVENANCE_DEFAULTS["narrative_source"]),
        "narrative_provider": str(getattr(provenance, "provider", "") or "none"),
        "narrative_prompt_version": str(getattr(provenance, "prompt_version", "") or ""),
        "narrative_validation_status": str(getattr(provenance, "validation_status", "") or
                                           _PROVENANCE_DEFAULTS["narrative_validation_status"]),
        "narrative_generated_at": str(getattr(provenance, "generated_at", "") or ""),
        "narrative_latency_ms": None if latency is None else int(latency),
        "narrative_fallback_reason": str(getattr(provenance, "fallback_reason", "") or ""),
    }


def _record_to_dict(record: AnalysisHistoryRecord) -> dict:
    data = asdict(record)
    # Enums serialize to their string values.
    data["market_phase"] = record.market_phase.value
    data["recommendation"] = record.recommendation.value
    data["data_status"] = record.data_status.value
    return data


def _record_from_dict(data: dict) -> AnalysisHistoryRecord:
    return AnalysisHistoryRecord(
        record_id=data["record_id"],
        symbol=data["symbol"],
        created_at=data["created_at"],
        market_phase=MarketPhase(data["market_phase"]),
        recommendation=Recommendation(data["recommendation"]),
        data_status=DataStatus(data["data_status"]),
        evidence_version=data["evidence_version"],
        confidence_overall=float(data["confidence_overall"]),
        summary_snapshot=data.get("summary_snapshot", ""),
        evidence_hash=data.get("evidence_hash", ""),
        narrative_model=data.get("narrative_model"),
        language=data.get("language", "ar"),
        # Pre-v1.2 lines carry no narrative metadata; they read back with the defaults.
        narrative_source=data.get("narrative_source",
                                  _PROVENANCE_DEFAULTS["narrative_source"]),
        narrative_provider=data.get("narrative_provider", "none"),
        narrative_prompt_version=data.get("narrative_prompt_version", ""),
        narrative_validation_status=data.get(
            "narrative_validation_status",
            _PROVENANCE_DEFAULTS["narrative_validation_status"]),
        narrative_generated_at=data.get("narrative_generated_at", ""),
        narrative_latency_ms=data.get("narrative_latency_ms"),
        narrative_fallback_reason=data.get("narrative_fallback_reason", ""),
    )


class AnalysisHistoryStore:
    """Durable, append-only JSONL store of ``AnalysisHistoryRecord`` entries."""

    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path) if path is not None else DEFAULT_HISTORY_PATH

    def append(self, record: AnalysisHistoryRecord) -> AnalysisHistoryRecord:
        """Append one record as a JSONL line. Never rewrites existing content."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(_record_to_dict(record), ensure_ascii=False, sort_keys=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return record

    def record_analysis(
        self,
        result: AnalysisResult,
        narrative: NarrativeResult | None = None,
        *,
        created_at: str,
        record_id: str | None = None,
    ) -> AnalysisHistoryRecord:
        """Build and append a record in one step; returns the stored record."""
        record = build_history_record(result, narrative, created_at=created_at,
                                      record_id=record_id)
        return self.append(record)

    def records(self, symbol: str | None = None) -> list[AnalysisHistoryRecord]:
        """Return all records (optionally filtered by symbol), oldest first."""
        if not self.path.exists():
            return []
        out: list[AnalysisHistoryRecord] = []
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = _record_from_dict(json.loads(raw))
            except (json.JSONDecodeError, KeyError, ValueError):
                continue  # tolerate a corrupt line without losing the rest of the log
            if symbol is None or record.symbol.upper() == symbol.upper():
                out.append(record)
        return out

    def latest(self, symbol: str) -> AnalysisHistoryRecord | None:
        """Return the most recently appended record for ``symbol`` (or None)."""
        recs = self.records(symbol)
        return recs[-1] if recs else None
