"""EODHD × Yahoo shadow validation for the five pilot symbols (Phase 4).

    python -m scripts.eodhd_shadow_validation

Fetches EODHD separately, compares to Yahoo (the active provider), writes per-window
CSVs + a summary. Read-only: never changes provider selection, Yahoo, Rubix, strategy,
or execution. EODHD responses are cached persistently so reruns do not consume budget.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.environment import load_project_environment       # noqa: E402
from providers.eodhd_client import EODHDClient              # noqa: E402
from providers import eodhd_shadow                          # noqa: E402
from providers.provider_mode import current_mode, active_historical_provider  # noqa: E402

SYMBOLS = ["COMI", "SWDY", "FWRY", "ORAS", "TMGH"]
OUT = PROJECT_ROOT / "reports" / "eodhd_shadow"


def main():
    load_project_environment()
    OUT.mkdir(parents=True, exist_ok=True)
    client = EODHDClient(max_live_calls=60)     # budget guard for the pilot
    today = date.today()
    windows = [
        ("30_sessions", (today - timedelta(days=50)).isoformat(), "3mo"),
        ("1_year", (today - timedelta(days=380)).isoformat(), "1y"),
    ]

    summaries = []
    for sym in SYMBOLS:
        for wname, efrom, yperiod in windows:
            aligned, summary = eodhd_shadow.compare(
                sym, window=wname, eodhd_from=efrom, yahoo_period=yperiod, eodhd_client=client)
            if not aligned.empty:
                aligned.to_csv(OUT / f"{sym}_{wname}.csv", index=False, encoding="utf-8-sig")
            summaries.append(summary)
        # max-history comparison for COMI only
        if sym == "COMI":
            aligned, summary = eodhd_shadow.compare(
                sym, window="max", eodhd_from=None, yahoo_period="max", eodhd_client=client)
            if not aligned.empty:
                aligned.to_csv(OUT / f"{sym}_max.csv", index=False, encoding="utf-8-sig")
            summaries.append(summary)

    import csv
    fields = ["symbol", "window", "eodhd_symbol", "yahoo_symbol", "eodhd_rows", "yahoo_rows",
              "common_sessions", "eodhd_latest", "yahoo_latest", "max_close_abs_diff",
              "mean_close_pct_diff", "max_close_pct_diff", "max_adj_close_abs_diff",
              "max_ohlc_abs_diff", "max_volume_abs_diff", "mean_volume_pct_diff",
              "max_adj_ratio_gap", "only_in_eodhd", "only_in_yahoo", "eodhd_error"]
    with (OUT / "shadow_summary.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for s in summaries:
            row = dict(s)
            row["only_in_eodhd"] = ";".join(s.get("only_in_eodhd", []))
            row["only_in_yahoo"] = ";".join(s.get("only_in_yahoo", []))
            w.writerow(row)

    report = {
        "provider_mode": current_mode(),
        "active_historical_provider": active_historical_provider(),
        "token_status": client.token_status(),
        "api_live_calls": client.stats.live_calls,
        "api_cache_hits": client.stats.cache_hits,
        "symbols": SYMBOLS,
        "summaries": summaries,
    }
    (OUT / "shadow_summary.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps({"mode": report["provider_mode"],
                      "active_historical": report["active_historical_provider"],
                      "live_calls": client.stats.live_calls,
                      "cache_hits": client.stats.cache_hits,
                      "comparisons": len(summaries)}, ensure_ascii=False))
    for s in summaries:
        print(f"  {s['symbol']:5} {s['window']:12} common={s['common_sessions']:4} "
              f"maxCloseDiff={s['max_close_abs_diff']:.4f} meanClosePct={s['mean_close_pct_diff']:.4f} "
              f"maxVolDiff={s['max_volume_abs_diff']:.0f} eodhdLatest={s['eodhd_latest']} "
              f"yahooLatest={s['yahoo_latest']} +E={len(s['only_in_eodhd'])} +Y={len(s['only_in_yahoo'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
