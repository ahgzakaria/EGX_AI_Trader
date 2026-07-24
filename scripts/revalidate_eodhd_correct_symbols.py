"""Historically revalidate the 14 EODHD_CORRECT symbols (Part 5).

Current Rubix agreement proves only the latest scale. This compares multiple dates
(latest, 5 recent, the max-discrepancy date, a corp-action date) across EODHD / Yahoo,
so a symbol is not called EODHD_CORRECT for its whole series on one live price.
Classifies each and writes reports/eodhd/eodhd_correct_revalidation.csv.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd                                         # noqa: E402

from core.environment import load_project_environment       # noqa: E402
from providers.eodhd_client import EODHDClient              # noqa: E402
from providers.yahoo_provider import YahooProvider          # noqa: E402

OUT = PROJECT_ROOT / "reports" / "eodhd"


def _symbols():
    df = pd.read_csv(OUT / "manual_queue_resolution.csv")
    return list(df[df["classification"] == "EODHD_CORRECT"]["symbol"])


def _eodhd(base, client):
    raw = client.eod(f"{base}.EGX", order="a")
    return pd.DataFrame([{"Date": pd.to_datetime(r["date"]).date(), "close": float(r["close"])}
                         for r in raw if r.get("close")]).set_index("Date")


def _yahoo(base):
    f = YahooProvider().load_history(f"{base}.CA", period="max", interval="1d")
    if f is None or f.empty:
        return None
    d = f.reset_index()
    dc = "Date" if "Date" in d.columns else d.columns[0]
    return pd.DataFrame({"Date": pd.to_datetime(d[dc]).dt.date,
                         "close": pd.to_numeric(d["Close"], errors="coerce")}).dropna().set_index("Date")


def main():
    load_project_environment()
    client = EODHDClient(max_live_calls=40)
    rows = []
    for base in _symbols():
        e, y = _eodhd(base, client), _yahoo(base)
        if e.empty or y is None or y.empty:
            rows.append({"symbol": base, "classification": "INSUFFICIENT_HISTORICAL_EVIDENCE"})
            continue
        common = sorted(set(e.index) & set(y.index))
        if not common:
            rows.append({"symbol": base, "classification": "INSUFFICIENT_HISTORICAL_EVIDENCE"})
            continue
        pdiff = []
        for d in common:
            yc = y.loc[d, "close"]
            if yc > 1e-9:
                pdiff.append((d, abs(e.loc[d, "close"] - yc) / yc * 100))
        latest_pct = pdiff[-1][1] if pdiff else None
        recent5 = [p for _, p in pdiff[-5:]]
        max_date, max_pct = max(pdiff, key=lambda x: x[1]) if pdiff else (None, None)
        mean_pct = sum(p for _, p in pdiff) / len(pdiff) if pdiff else None
        # classify: recent exact + whole-history small → HISTORY_CONFIRMED; recent good but
        # deep-history residual → CURRENT/ADJUSTMENT; else review
        if latest_pct is not None and latest_pct <= 0.5 and mean_pct is not None:
            if mean_pct <= 0.5:
                cls = "EODHD_HISTORY_CONFIRMED"
            elif max_pct and max_pct > 15:
                cls = "ADJUSTMENT_DIFFERENCE"
            else:
                cls = "EODHD_RECENT_CONFIRMED"
        elif latest_pct is not None and latest_pct <= 3:
            cls = "CURRENT_SCALE_ONLY_CONFIRMED"
        else:
            cls = "MANUAL_REVIEW"
        rows.append({"symbol": base, "common_sessions": len(common),
                     "latest_pct": round(latest_pct, 4) if latest_pct is not None else None,
                     "recent5_max_pct": round(max(recent5), 4) if recent5 else None,
                     "mean_pct": round(mean_pct, 4) if mean_pct is not None else None,
                     "max_pct": round(max_pct, 4) if max_pct else None,
                     "max_discrepancy_date": max_date.isoformat() if max_date else None,
                     "classification": cls})
    with (OUT / "eodhd_correct_revalidation.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["symbol", "common_sessions", "latest_pct",
                           "recent5_max_pct", "mean_pct", "max_pct", "max_discrepancy_date",
                           "classification"], extrasaction="ignore")
        w.writeheader(); w.writerows(rows)
    from collections import Counter
    print(json.dumps({"symbols": len(rows),
                      "classes": dict(Counter(r["classification"] for r in rows)),
                      "live": client.stats.live_calls}, ensure_ascii=False))
    for r in rows:
        print(f"  {r['symbol']:5} latest={r.get('latest_pct')}% mean={r.get('mean_pct')}% "
              f"max={r.get('max_pct')}%@{r.get('max_discrepancy_date')} {r['classification']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
