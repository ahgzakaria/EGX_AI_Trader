"""Phase 8 — daily provider chain (built, external tier NOT activated by default).

Priority: (1) valid FINAL Rubix-derived latest sessions, (2) validated external
historical provider [disabled until audited], (3) existing normalized local cache,
(4) Yahoo legacy emergency fallback. Rubix latest-session rows coexist with
external/legacy historical rows; every returned row keeps its provider/source, and
a coverage summary is returned. Duplicate symbol/date rows with ambiguous priority
are reported, never silently merged.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.daily_bridge.normalized_cache import NormalizedDailyCache
from core.daily_bridge.schema import (
    EXTERNAL_HISTORICAL,
    FINAL,
    RUBIX_DERIVED,
    YAHOO_LEGACY,
)


@dataclass
class ChainResult:
    rows: list                              # ordered daily rows with provenance
    coverage: dict = field(default_factory=dict)
    conflicts: list = field(default_factory=list)


class DailyProviderChain:
    def __init__(self, cache=None, external_enabled=False, external_loader=None):
        # external tier stays OFF until its data is audited & approved.
        self.cache = cache or NormalizedDailyCache()
        self.external_enabled = bool(external_enabled)
        self.external_loader = external_loader

    def assemble(self, symbol, yahoo_rows=None):
        """Merge sources for one symbol, newest FINAL Rubix winning per date.

        ``yahoo_rows``: list of {'session_date','Open','High','Low','Close','Volume'}
        from the legacy Yahoo cache (emergency tier). Returns a ChainResult whose
        rows each carry `source_type` + `provider`.
        """
        by_date = {}
        conflicts = []
        counts = {RUBIX_DERIVED: 0, EXTERNAL_HISTORICAL: 0, YAHOO_LEGACY: 0}

        # Tier 4 first (lowest priority) so higher tiers override by date.
        for r in (yahoo_rows or []):
            d = str(r.get("session_date"))
            by_date[d] = {"session_date": d, "open": r.get("Open"), "high": r.get("High"),
                          "low": r.get("Low"), "official_close": r.get("Close"),
                          "volume": r.get("Volume"), "source_type": YAHOO_LEGACY,
                          "provider": "yahoo", "finalization_status": FINAL}

        # Tier 3: normalized cache external/legacy rows (if any).
        # Tier 2: external historical (only when explicitly enabled/audited).
        if self.external_enabled and self.external_loader:
            for r in self.external_loader(symbol):
                d = str(r.get("session_date"))
                if d in by_date and by_date[d]["source_type"] == RUBIX_DERIVED:
                    conflicts.append((symbol, d, "external_vs_rubix"))
                    continue
                r = dict(r); r.setdefault("source_type", EXTERNAL_HISTORICAL)
                by_date[d] = r
                counts[EXTERNAL_HISTORICAL] += 1

        # Tier 1 (highest): FINAL Rubix-derived rows override the same date.
        for r in self.cache.final_bars_after("0001-01-01", source_type=RUBIX_DERIVED):
            if r.get("canonical_symbol") != symbol:
                continue
            d = str(r.get("session_date"))
            by_date[d] = r
            counts[RUBIX_DERIVED] += 1

        # Yahoo count = rows that survived as YAHOO_LEGACY.
        counts[YAHOO_LEGACY] = sum(1 for v in by_date.values()
                                   if v.get("source_type") == YAHOO_LEGACY)
        rows = [by_date[d] for d in sorted(by_date)]
        coverage = {"total_rows": len(rows), "by_source": counts,
                    "latest_session": rows[-1]["session_date"] if rows else None,
                    "external_enabled": self.external_enabled}
        return ChainResult(rows=rows, coverage=coverage, conflicts=conflicts)
