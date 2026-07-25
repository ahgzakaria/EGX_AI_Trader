"""Build the internal-universe ↔ EODHD EGX symbol mapping report (Phase 2).

    python -m scripts.audit_eodhd_symbol_mapping

Fetches the real EODHD EGX symbol list (cached), verifies each internal symbol against
it (never suffix-replacement alone), and writes reports/eodhd_symbol_mapping.csv +
a JSON summary. Token-safe; changes no provider.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.environment import load_project_environment       # noqa: E402
from providers.eodhd_client import EODHDClient, EODHDError  # noqa: E402
from providers.eodhd_symbol_map import build_mapping        # noqa: E402

FIELDS = ["internal_symbol", "internal_symbol_ca", "eodhd_symbol", "company_name_internal",
          "company_name_eodhd", "instrument_type", "mapping_status", "mapping_method", "notes"]


def _internal_symbols():
    path = PROJECT_ROOT / "data" / "symbols.csv"
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.lower() == "ticker":
            continue
        out.append(s)
    return out


def main():
    load_project_environment()
    internal = _internal_symbols()
    client = EODHDClient()
    try:
        eodhd_rows = client.exchange_symbols("EGX")
    except EODHDError as error:
        print(json.dumps({"error": str(error)}))
        return 1

    rows, summary = build_mapping(internal, eodhd_rows, exchange="EGX")

    out_dir = PROJECT_ROOT / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "eodhd_symbol_mapping.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    (out_dir / "eodhd_symbol_mapping_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps({k: summary[k] for k in
                      ("internal_count", "eodhd_egx_count", "verified_exact", "non_equity",
                       "duplicate", "not_found", "mapped_total")}, ensure_ascii=False))
    print(f"missing internal ({len(summary['missing_internal'])}): "
          f"{', '.join(summary['missing_internal'][:40])}")
    print(f"extra EODHD symbols ({len(summary['extra_eodhd_symbols'])}): "
          f"{', '.join(summary['extra_eodhd_symbols'][:40])}")
    print(f"cache/live: {client.stats.cache_hits}/{client.stats.live_calls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
