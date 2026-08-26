"""Resolve the 15 MANUAL_REVIEW symbols + MEGM with date-aligned evidence (Part 6).

    python -m scripts.resolve_eodhd_manual_queue

Compares same-date EODHD / Yahoo / Rubix closes (never an old daily close vs an
unlabeled live quote — the Rubix quote's own session date is required). Classifies each
symbol and writes reports/eodhd/manual_queue_resolution.csv. No provider change.
"""

from __future__ import annotations

import csv
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd                                         # noqa: E402

from core.environment import load_project_environment       # noqa: E402
from providers.eodhd_client import EODHDClient              # noqa: E402
from providers.eodhd_historical_provider import EODHDHistoricalProvider  # noqa: E402

OUT = PROJECT_ROOT / "reports" / "eodhd"
YCACHE = PROJECT_ROOT / "data" / "yahoo_cache"
_TOL = 0.03      # 3% → "agree"


def _queue():
    df = pd.read_csv(OUT / "full_universe_symbol_results.csv", keep_default_na=False)
    return list(df[df["category"].isin(["MANUAL_REVIEW", "PRICE_SCALE_ANOMALY"])]["symbol"])


def _yahoo_last(base):
    fp = YCACHE / f"{base}_1y.csv"
    if not fp.is_file():
        return (None, None)
    try:
        df = pd.read_csv(fp)
        if df.empty:
            return (None, None)
        row = df.sort_values("Date").iloc[-1]
        return (float(row["Close"]), str(row["Date"])[:10])
    except Exception:
        return (None, None)


def _rubix_last(base):
    # Rubix quote WITH its explicit session date (market_timestamp); 0.0 is invalid.
    try:
        c = sqlite3.connect(f"file:{PROJECT_ROOT / 'data' / 'rubix_live_market.db'}?mode=ro", uri=True)
        row = c.execute("SELECT last_price, substr(market_timestamp,1,10) FROM quotes "
                        "WHERE ticker=? ORDER BY id DESC LIMIT 1", (base,)).fetchone()
        if row and row[0] and float(row[0]) > 0:
            return (float(row[0]), row[1])
        return (None, None)
    except Exception:
        return (None, None)


def _agree(a, b):
    return a and b and abs(a - b) / max(abs(b), 1e-9) <= _TOL


def classify(e, ed, y, yd, r, rd):
    # date-aligned: only compare closes whose session dates match
    same = {d for d in (ed, yd, rd) if d}
    if r and ed and rd == ed:                    # EODHD and Rubix on the same date
        if _agree(e, r) and not _agree(y or 0, r):
            return "EODHD_CORRECT", "STALE_YAHOO" if y else "YAHOO_MISSING"
        if _agree(y or 0, r) and not _agree(e, r):
            return "YAHOO_CORRECT", "STALE_EODHD"
        if _agree(e, r) and _agree(y or 0, r):
            return "EODHD_CORRECT", "all_agree"
    if r is None:
        return "SYMBOL_NOT_LIQUID", "no valid Rubix price to adjudicate"
    if e and y and ed == yd and not _agree(e, y):
        return "PROVIDERS_DIFFER_BY_ADJUSTMENT", "same date, differ >3% (dividend/adjust?)"
    return "INSUFFICIENT_EVIDENCE", "dates not aligned or ambiguous"


def main():
    load_project_environment()
    client = EODHDClient(max_live_calls=60)
    prov = EODHDHistoricalProvider(client)
    rows = []
    for base in _queue():
        res = prov.load_history(base, from_date=(pd.Timestamp.now() - pd.Timedelta(days=40)).date().isoformat())
        e, ed = (float(res.frame.iloc[-1]["Close"]), res.frame.iloc[-1]["Date"].isoformat()) \
            if res.ok else (None, None)
        y, yd = _yahoo_last(base)
        r, rd = _rubix_last(base)
        cls, reason = classify(e, ed, y, yd, r, rd)
        rows.append({
            "symbol": base, "eodhd_close": e, "eodhd_date": ed, "yahoo_close": y,
            "yahoo_date": yd, "rubix_close": r, "rubix_date": rd,
            "eodhd_yahoo_ratio": round(e / y, 4) if (e and y) else None,
            "eodhd_rubix_ratio": round(e / r, 4) if (e and r) else None,
            "yahoo_rubix_ratio": round(y / r, 4) if (y and r) else None,
            "classification": cls, "evidence": reason})
    fields = ["symbol", "eodhd_close", "eodhd_date", "yahoo_close", "yahoo_date", "rubix_close",
              "rubix_date", "eodhd_yahoo_ratio", "eodhd_rubix_ratio", "yahoo_rubix_ratio",
              "classification", "evidence"]
    with (OUT / "manual_queue_resolution.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)
    from collections import Counter
    print(json.dumps({"resolved": len(rows),
                      "classes": dict(Counter(r["classification"] for r in rows)),
                      "live": client.stats.live_calls, "cache": client.stats.cache_hits},
                     ensure_ascii=False))
    for r in rows:
        print(f"  {r['symbol']:5} {r['classification']:32} E={r['eodhd_close']} "
              f"Y={r['yahoo_close']} R={r['rubix_close']} E/R={r['eodhd_rubix_ratio']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
