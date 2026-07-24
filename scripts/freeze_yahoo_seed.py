"""One-time freeze of the Yahoo-derived local seed for EODHD-unsupported symbols.

Captures the CURRENT local-cache history for every Tier-D (EODHD-unsupported) symbol
into the immutable ``data/frozen_yahoo_seed`` store, so the operational local route reads
a frozen bootstrap instead of the live cache the (now-disabled) daily Yahoo refresh task
kept advancing. No network call is made — this only copies what is already cached.

    python -m scripts.freeze_yahoo_seed

Re-running re-snapshots from the current cache; run it only when you deliberately want to
re-bootstrap the frozen seed (e.g. after a one-off manual backfill), never on a schedule.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core import frozen_seed_store as store          # noqa: E402
import core.research_router as router                 # noqa: E402
from providers.base_provider import ProviderError     # noqa: E402
from providers.local_cache_provider import LocalCacheProvider  # noqa: E402


def _tier_d_symbols():
    out = []
    for base, entry in router.tier_map().items():
        if entry.get("tier") == "TIER_D_UNSUPPORTED_OR_MANUAL":
            out.append(base)
    return sorted(out)


def _load_cached(cache, base):
    for key in (f"{base}.CA", base):
        try:
            frame = cache.load_cached("yahoo", key, "10y", "1d", allow_expired=True)
        except (ProviderError, Exception):
            frame = None
        if frame is not None and not getattr(frame, "empty", True):
            return frame
    return None


def main():
    cache = LocalCacheProvider()
    records, missing = [], []
    for base in _tier_d_symbols():
        frame = _load_cached(cache, base)
        if frame is None:
            missing.append(base)
            continue
        records.append(store.freeze_frame(base, frame))
    manifest = store.write_manifest(
        records, source="local_cache:yahoo (snapshot, no network)",
        note="Frozen after disabling EGX_AI_Trader_YahooCacheRefresh; extend only via Rubix bridge.")
    summary = {"frozen": len(records), "missing_no_cache": len(missing),
               "missing_symbols": missing, "manifest": str(store.MANIFEST_PATH)}
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
