"""Fetch + cache EODHD splits/dividends for every mapped symbol (Part 2).

    python -m scripts.audit_eodhd_corporate_actions [--limit N]

Raw provider responses are cached under data/eodhd/corporate_actions/ and never
overwritten destructively (the client cache is content-addressed). Writes
reports/eodhd/corporate_action_inventory.csv. Token-safe; no provider change.
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd                                         # noqa: E402

from core.environment import load_project_environment       # noqa: E402
from providers.eodhd_adjustment import parse_split_ratio, InvalidSplit  # noqa: E402
from providers.eodhd_client import EODHDClient, EODHDError  # noqa: E402

RAW_DIR = PROJECT_ROOT / "data" / "eodhd" / "corporate_actions"
OUT = PROJECT_ROOT / "reports" / "eodhd"


def _mapped():
    # keep_default_na: the EGX ticker "NULL" is a real code, not a missing value.
    df = pd.read_csv(PROJECT_ROOT / "reports" / "eodhd_symbol_mapping.csv",
                     keep_default_na=False)
    return [str(s) for s in df[df["mapping_status"] == "VERIFIED_EXACT"]["internal_symbol"]]


def _save_raw(base, kind, data):
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / f"{base}_{kind}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv=None):
    load_project_environment()
    OUT.mkdir(parents=True, exist_ok=True)
    limit = None
    if argv and "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    symbols = _mapped()
    if limit:
        symbols = symbols[:limit]
    client = EODHDClient(max_live_calls=600)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows, done = [], 0

    for base in symbols:
        for kind in ("splits", "div"):
            try:
                data = client.get_json(f"{kind}/{base}.EGX", cache_ttl_seconds=7 * 86400)
            except EODHDError as error:
                rows.append({"symbol": base, "action_type": kind.upper(),
                             "source": "EODHD", "fetched_at": fetched_at,
                             "validation_status": "FETCH_ERROR", "notes": str(error)})
                continue
            _save_raw(base, kind, data)
            if not isinstance(data, list):
                continue
            for r in data:
                if kind == "splits":
                    raw = r.get("split")
                    try:
                        ratio = parse_split_ratio(raw)
                        status = "VALID"
                    except InvalidSplit as e:
                        ratio, status = None, f"INVALID:{e}"
                    rows.append({
                        "symbol": base, "action_date": r.get("date"), "action_type": "SPLIT",
                        "split_ratio": round(ratio, 6) if ratio else raw, "dividend_amount": "",
                        "currency": "", "source": "EODHD_splits", "fetched_at": fetched_at,
                        "validation_status": status, "notes": f"raw={raw}"})
                else:
                    rows.append({
                        "symbol": base, "action_date": r.get("date"), "action_type": "DIVIDEND",
                        "split_ratio": "", "dividend_amount": r.get("unadjustedValue", r.get("value")),
                        "currency": r.get("currency", "EGP"), "source": "EODHD_div",
                        "fetched_at": fetched_at, "validation_status": "VALID",
                        "notes": f"adjusted_value={r.get('value')}"})
        done += 1
        if done % 25 == 0:
            print(f"...{done}/{len(symbols)} live={client.stats.live_calls} cache={client.stats.cache_hits}",
                  flush=True)

    fields = ["symbol", "action_date", "action_type", "split_ratio", "dividend_amount",
              "currency", "source", "fetched_at", "validation_status", "notes"]
    with (OUT / "corporate_action_inventory.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    from collections import Counter
    print(json.dumps({
        "symbols": len(symbols), "action_rows": len(rows),
        "splits": sum(1 for r in rows if r.get("action_type") == "SPLIT"),
        "dividends": sum(1 for r in rows if r.get("action_type") == "DIVIDEND"),
        "invalid_splits": sum(1 for r in rows if str(r.get("validation_status", "")).startswith("INVALID")),
        "live_calls": client.stats.live_calls, "cache_hits": client.stats.cache_hits}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
