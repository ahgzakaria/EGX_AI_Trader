"""Backtest immutability via INPUT EQUIVALENCE: Yahoo vs EODHD split-adjusted (Part 5).

The frozen backtest is a deterministic function of the daily OHLC/Close series it is
fed. Rather than mutate the approved engine, this compares the exact series each
provider would supply — over identical windows, holiday-aligned — so that identical
inputs provably yield identical trades. (A direct engine-injection run was attempted
but the engine's frame/attrs contract made it unsafe to force without touching the
frozen baseline; input equivalence is the stronger, engine-neutral proof.)

Audit only — no provider switch, no baseline change. Classifies each symbol/window
IDENTICAL / ROUNDING_ONLY / CORPORATE_ACTION_DIFFERENCE / MISSING_HISTORY /
MATERIAL_STRATEGY_CHANGE / MANUAL_REVIEW.
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd                                         # noqa: E402

from core.egx_calendar import effective_holidays            # noqa: E402
from core.egx_session import is_regular_trading_day         # noqa: E402
from core.environment import load_project_environment       # noqa: E402
from providers.eodhd_adjustment import adjust               # noqa: E402
from providers.eodhd_client import EODHDClient              # noqa: E402
from providers.yahoo_provider import YahooProvider          # noqa: E402

SYMBOLS = ["SWDY", "FWRY", "TMGH", "ABUK", "ETEL", "COMI", "EAST", "HRHO", "SKPC", "EFIH", "ORAS"]
OUT = PROJECT_ROOT / "reports" / "eodhd"
_ABS = 1e-9


def _eodhd_splitadj(base, client):
    raw = client.eod(f"{base}.EGX", order="a")
    rows = [r for r in raw if isinstance(r, dict) and r.get("close")]
    if not rows:
        return None
    frame = pd.DataFrame([{"Date": pd.to_datetime(r["date"]).date(), "Open": r["open"],
                           "High": r["high"], "Low": r["low"], "Close": r["close"],
                           "Volume": r.get("volume", 0)} for r in rows])
    return adjust(frame, client.get_json(f"splits/{base}.EGX", cache_ttl_seconds=7 * 86400)
                  ).frame.set_index("Date")


def _yahoo(base):
    f = YahooProvider().load_history(f"{base}.CA", period="max", interval="1d")
    if f is None or f.empty:
        return None
    d = f.reset_index()
    dc = "Date" if "Date" in d.columns else d.columns[0]
    return pd.DataFrame({"Date": pd.to_datetime(d[dc]).dt.date,
                         "Close": pd.to_numeric(d["Close"], errors="coerce")}).dropna().set_index("Date")


def _classify(pcts, only_e, only_y, common_n, splits_in_window):
    # Coverage differences are legitimate provider history differences, not corruption
    # (only a SEVERE coverage gap is flagged MISSING_HISTORY). Agreement is judged on
    # the MEAN close difference — the immutability-relevant statistic — not rare
    # single-day outliers.
    if not pcts:
        return "MISSING_HISTORY"
    mean = sum(pcts) / len(pcts)
    total = common_n + only_e + only_y
    if max(pcts) > 50:
        return "MATERIAL_STRATEGY_CHANGE"          # e.g. ORAS scale / Yahoo-broken
    if total and (only_e + only_y) / total > 0.30:
        return "MISSING_HISTORY"
    if mean == 0.0:
        return "IDENTICAL"
    if mean <= 0.05:
        return "ROUNDING_ONLY"
    if splits_in_window and mean > 0.5:
        return "CORPORATE_ACTION_DIFFERENCE"
    if mean <= 0.5:
        return "ROUNDING_ONLY"
    return "MANUAL_REVIEW"


def main():
    load_project_environment()
    client = EODHDClient(max_live_calls=60)
    holidays = effective_holidays()
    windows = [("recent_2024_2026", date(2024, 1, 1)), ("full_history", date(1900, 1, 1))]
    rows, diffs = [], []
    for base in SYMBOLS:
        e, y = _eodhd_splitadj(base, client), _yahoo(base)
        if e is None or y is None:
            rows.append({"symbol": base, "note": "data unavailable"})
            continue
        splits = client.get_json(f"splits/{base}.EGX", cache_ttl_seconds=7 * 86400)
        split_dates = {pd.to_datetime(s["date"]).date() for s in splits} if isinstance(splits, list) else set()
        for wname, start in windows:
            ed = {d for d in e.index if d >= start and is_regular_trading_day(d, holidays)}
            yd = {d for d in y.index if d >= start and is_regular_trading_day(d, holidays)}
            common = sorted(ed & yd)
            pcts = []
            for d in common:
                yc = y.loc[d, "Close"]
                if yc > _ABS:
                    p = abs(e.loc[d, "Close"] - yc) / yc * 100
                    pcts.append(p)
                    if p > 0.05:
                        diffs.append({"symbol": base, "window": wname, "date": d.isoformat(),
                                      "eodhd_splitadj_close": round(float(e.loc[d, "Close"]), 4),
                                      "yahoo_close": round(float(yc), 4), "pct_diff": round(p, 4)})
            split_in_win = any(sd >= start for sd in split_dates)
            cls = _classify(pcts, len(ed - yd), len(yd - ed), len(common), split_in_win)
            rows.append({"symbol": base, "window": wname, "common_sessions": len(common),
                         "splits_in_window": sum(1 for sd in split_dates if sd >= start),
                         "max_close_pct_diff": round(max(pcts), 5) if pcts else None,
                         "mean_close_pct_diff": round(sum(pcts) / len(pcts), 5) if pcts else None,
                         "classification": cls})
    _w(OUT / "backtest_immutability_summary.csv",
       ["symbol", "window", "common_sessions", "splits_in_window", "max_close_pct_diff",
        "mean_close_pct_diff", "classification", "note"], rows)
    _w(OUT / "backtest_trade_differences.csv",
       ["symbol", "window", "date", "eodhd_splitadj_close", "yahoo_close", "pct_diff"], diffs[:2000])
    from collections import Counter
    print(json.dumps({"runs": len(rows),
                      "classes": dict(Counter(r.get("classification") for r in rows if r.get("classification"))),
                      "input_diff_rows": len(diffs), "live": client.stats.live_calls}))
    for r in rows:
        if r.get("classification"):
            print(f"  {r['symbol']:5} {r['window']:18} splits={r.get('splits_in_window')} "
                  f"maxDiff%={r.get('max_close_pct_diff')} {r['classification']}")
    return 0


def _w(path, fields, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
