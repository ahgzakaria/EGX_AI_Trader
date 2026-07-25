"""Genuine frozen-engine backtest: Yahoo vs EODHD-via-contract-adapter (Part 2).

    python -m scripts.audit_eodhd_real_backtest

Runs the REAL, unmodified BacktestEngine twice per symbol/window — once on the current
Yahoo history, once on the EODHD split-adjusted history passed through the exact
load_history contract adapter — and compares signals and trades. The engine is never
modified to make EODHD pass. Audit only; no provider switch, no baseline change.
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

import backtesting.engine as eng                            # noqa: E402
from core.data_provider import load_history, provider_purpose  # noqa: E402
from core.environment import load_project_environment       # noqa: E402
from core.history_frame_adapter import to_load_history_frame  # noqa: E402
from providers.eodhd_adjustment import adjust                # noqa: E402
from providers.eodhd_client import EODHDClient               # noqa: E402

SYMBOLS = ["SWDY", "FWRY", "TMGH", "ABUK", "COMI", "EAST", "SKPC", "EFIH", "ORAS", "ETEL"]
NO_CORP_ACTION = "ETEL"
WINDOWS = [("1y", "2025-07-01", "2026-07-21"), ("3y", "2023-07-01", "2026-07-21"),
           ("full", "2010-01-01", "2026-07-21")]
OUT = PROJECT_ROOT / "reports" / "eodhd"
_client = EODHDClient(max_live_calls=60)


def _yahoo_frame(base):
    with provider_purpose("backtest"):
        return load_history(f"{base}.CA", purpose="backtest")


def _eodhd_frame(base):
    raw = _client.eod(f"{base}.EGX", order="a")
    rows = [r for r in raw if isinstance(r, dict) and r.get("close")]
    if not rows:
        return None
    frame = pd.DataFrame([{"Date": pd.to_datetime(r["date"]).date(), "Open": r["open"],
                           "High": r["high"], "Low": r["low"], "Close": r["close"],
                           "Volume": r.get("volume", 0)} for r in rows])
    adj = adjust(frame, _client.get_json(f"splits/{base}.EGX", cache_ttl_seconds=7 * 86400)).frame
    return to_load_history_frame(adj, symbol=f"{base}.CA")


def _run(base, frame, start, end):
    orig = eng.load_history
    eng.load_history = lambda sym, purpose=None: frame.copy()
    try:
        e = eng.BacktestEngine(f"{base}.CA")
        e.load()
        trades = e.run(start_date=pd.Timestamp(start), end_date=pd.Timestamp(end))
    finally:
        eng.load_history = orig
    return [_trade_tuple(t) for t in (trades or [])]


def _trade_tuple(t):
    d = t.__dict__
    return {"entry": str(d.get("entry_date"))[:10], "exit": str(d.get("exit_date"))[:10],
            "signal": str(d.get("signal_date"))[:10], "result": str(d.get("result")),
            "exit_reason": str(d.get("exit_reason")),
            "profit_pct": round(float(d.get("profit_percent") or 0), 3),
            "entry_price": round(float(d.get("entry_price") or 0), 3)}


def _classify(y, e, near_split):
    if y == e:
        return "IDENTICAL"
    if len(y) != len(e):
        if abs(len(y) - len(e)) <= 1:
            return "CORPORATE_ACTION_DIFFERENCE" if near_split else "BAR_COVERAGE_DIFFERENCE"
        return "MATERIAL_BACKTEST_CHANGE"
    sig_diff = sum(1 for a, b in zip(y, e) if a["signal"] != b["signal"] or a["entry"] != b["entry"])
    if sig_diff == 0:
        # same signals/entries/exits → only price rounding
        prof_diff = max((abs(a["profit_pct"] - b["profit_pct"]) for a, b in zip(y, e)), default=0)
        return "ROUNDING_ONLY" if prof_diff <= 0.5 else "CORPORATE_ACTION_DIFFERENCE"
    if sig_diff <= 1 and near_split:
        return "CORPORATE_ACTION_DIFFERENCE"
    return "MATERIAL_SIGNAL_CHANGE"


def main():
    load_project_environment()
    OUT.mkdir(parents=True, exist_ok=True)
    rows, diffs = [], []
    for base in SYMBOLS:
        try:
            yf = _yahoo_frame(base)
            ef = _eodhd_frame(base)
        except Exception as error:
            rows.append({"symbol": base, "note": f"data error: {type(error).__name__}"})
            continue
        if ef is None or yf is None or yf.empty:
            rows.append({"symbol": base, "note": "history unavailable"})
            continue
        splits = _client.get_json(f"splits/{base}.EGX", cache_ttl_seconds=7 * 86400)
        split_dates = {str(s.get("date"))[:10] for s in splits} if isinstance(splits, list) else set()
        for wname, start, end in WINDOWS:
            near_split = any(start <= sd <= end for sd in split_dates)
            try:
                yt = _run(base, yf, start, end)
                et = _run(base, ef, start, end)
            except Exception as error:
                rows.append({"symbol": base, "window": wname,
                             "classification": "ENGINE_CONTRACT_FAILURE",
                             "note": f"{type(error).__name__}: {str(error)[:80]}"})
                continue
            cls = _classify(yt, et, near_split)
            rows.append({
                "symbol": base, "window": wname, "yahoo_bars": int(((yf.index >= pd.Timestamp(start))
                             & (yf.index <= pd.Timestamp(end))).sum()),
                "eodhd_bars": int(((ef.index >= pd.Timestamp(start))
                             & (ef.index <= pd.Timestamp(end))).sum()),
                "yahoo_trades": len(yt), "eodhd_trades": len(et),
                "yahoo_net_pct": round(sum(t["profit_pct"] for t in yt), 3),
                "eodhd_net_pct": round(sum(t["profit_pct"] for t in et), 3),
                "splits_in_window": sum(1 for sd in split_dates if start <= sd <= end),
                "classification": cls})
            for i in range(max(len(yt), len(et))):
                a = yt[i] if i < len(yt) else None
                b = et[i] if i < len(et) else None
                if a != b:
                    diffs.append({"symbol": base, "window": wname, "index": i,
                                  "yahoo_trade": json.dumps(a), "eodhd_trade": json.dumps(b)})
    _w(OUT / "real_backtest_summary.csv",
       ["symbol", "window", "yahoo_bars", "eodhd_bars", "yahoo_trades", "eodhd_trades",
        "yahoo_net_pct", "eodhd_net_pct", "splits_in_window", "classification", "note"], rows)
    _w(OUT / "real_backtest_trade_differences.csv",
       ["symbol", "window", "index", "yahoo_trade", "eodhd_trade"], diffs)
    from collections import Counter
    print(json.dumps({"runs": len([r for r in rows if r.get("classification")]),
                      "classes": dict(Counter(r.get("classification") for r in rows if r.get("classification"))),
                      "trade_diffs": len(diffs), "live": _client.stats.live_calls}))
    for r in rows:
        if r.get("classification"):
            print(f"  {r['symbol']:5} {r['window']:5} Ybars={r.get('yahoo_bars')} Ebars={r.get('eodhd_bars')} "
                  f"Ytr={r.get('yahoo_trades')} Etr={r.get('eodhd_trades')} "
                  f"splits={r.get('splits_in_window')} {r['classification']}")
    return 0


def _w(path, fields, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
