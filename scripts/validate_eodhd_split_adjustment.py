"""Validate EODHD split-adjusted series vs Yahoo (Part 4).

    python -m scripts.validate_eodhd_split_adjustment

Builds EODHD RAW / SPLIT_ADJUSTED / adjusted_close and compares to Yahoo Close /
Adj Close / Volume for corporate-action symbols. The principal target is: does the
CALCULATED EODHD split-adjusted Close reproduce the project's current Yahoo Close?
Writes split_adjustment_validation.csv, volume_adjustment_validation.csv,
corporate_action_discrepancies.csv. No provider change; no Close↔Adj swap.
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd                                         # noqa: E402

from core.environment import load_project_environment       # noqa: E402
from providers.eodhd_adjustment import adjust               # noqa: E402
from providers.eodhd_client import EODHDClient              # noqa: E402
from providers.yahoo_provider import YahooProvider          # noqa: E402

SYMBOLS = ["COMI", "SWDY", "EAST", "FWRY", "TMGH", "ORAS",
           "HRHO", "ETEL", "ABUK", "SKPC", "EFIH"]          # pilot + corp-action names
OUT = PROJECT_ROOT / "reports" / "eodhd"
_ABS = 1e-9


def _eodhd(base, client):
    raw = client.eod(f"{base}.EGX", order="a")
    rows = [r for r in raw if isinstance(r, dict) and r.get("close")]
    if not rows:
        return None, None
    frame = pd.DataFrame([{"Date": pd.to_datetime(r["date"]).date(), "Open": r["open"],
                           "High": r["high"], "Low": r["low"], "Close": r["close"],
                           "Volume": r.get("volume", 0)} for r in rows])
    tr = {pd.to_datetime(r["date"]).date(): r.get("adjusted_close") for r in rows}
    splits = client.get_json(f"splits/{base}.EGX", cache_ttl_seconds=7 * 86400)
    return adjust(frame, splits, total_return_close=tr), splits


def _yahoo(base):
    f = YahooProvider().load_history(f"{base}.CA", period="max", interval="1d")
    if f is None or f.empty:
        return None
    d = f.reset_index()
    dc = "Date" if "Date" in d.columns else d.columns[0]
    return pd.DataFrame({"Date": pd.to_datetime(d[dc]).dt.date,
                         "Close": pd.to_numeric(d["Close"], errors="coerce"),
                         "Adj": pd.to_numeric(d.get("Adj Close", d["Close"]), errors="coerce"),
                         "Vol": pd.to_numeric(d.get("Volume"), errors="coerce").fillna(0)
                         }).dropna(subset=["Close"]).set_index("Date")


def main():
    load_project_environment()
    client = EODHDClient(max_live_calls=60)
    price_rows, vol_rows, disc_rows = [], [], []
    for base in SYMBOLS:
        res, splits = _eodhd(base, client)
        y = _yahoo(base)
        if res is None or not res.ok or y is None:
            price_rows.append({"symbol": base, "note": "data unavailable"})
            continue
        a = res.frame.set_index("Date")
        common = sorted(set(a.index) & set(y.index))
        if not common:
            continue
        raw_pct, sadj_pct, adjc_vs_yadj, vol_pct = [], [], [], []
        for d in common:
            yc = y.loc[d, "Close"]
            if yc > _ABS:
                raw_pct.append(abs(a.loc[d, "Raw Close"] - yc) / yc * 100)
                sadj_pct.append(abs(a.loc[d, "Close"] - yc) / yc * 100)
            ya = y.loc[d, "Adj"]
            tr = a.loc[d, "Total Return Adj Close"]
            if ya and ya > _ABS and pd.notna(tr):
                adjc_vs_yadj.append(abs(tr - ya) / ya * 100)
            yv = y.loc[d, "Vol"]
            if yv > 0:
                vol_pct.append(abs(a.loc[d, "Volume"] - yv) / yv * 100)
        n_splits = res.provenance.get("splits_applied", 0)
        price_rows.append({
            "symbol": base, "common_sessions": len(common), "splits": n_splits,
            "raw_vs_yahoo_mean_pct": _m(raw_pct), "raw_vs_yahoo_max_pct": _mx(raw_pct),
            "splitadj_vs_yahoo_mean_pct": _m(sadj_pct), "splitadj_vs_yahoo_max_pct": _mx(sadj_pct),
            "eodhd_adjclose_vs_yahoo_adj_mean_pct": _m(adjc_vs_yadj),
            "recent_splitadj_vs_yahoo_pct": round(abs(a.loc[common[-1], "Close"]
                                                      - y.loc[common[-1], "Close"])
                                                  / max(y.loc[common[-1], "Close"], _ABS) * 100, 5),
            "verdict": ("REPRODUCES_RECENT_EXACTLY" if abs(a.loc[common[-1], "Close"]
                        - y.loc[common[-1], "Close"]) < 0.01 else "RECENT_DIFF")})
        vol_rows.append({"symbol": base, "splitadj_vol_vs_yahoo_mean_pct": _m(vol_pct),
                         "splitadj_vol_vs_yahoo_max_pct": _mx(vol_pct), "common": len(common)})
        # discrepancy: where split-adjusted still differs materially from Yahoo (deep history)
        if _m(sadj_pct) > 1.0:
            disc_rows.append({
                "symbol": base, "splits": n_splits,
                "splitadj_vs_yahoo_mean_pct": _m(sadj_pct),
                "eodhd_cum_factor": round(res.provenance.get("max_split_factor", 1), 3),
                "suspected_cause": "EODHD vs Yahoo disagree on which capital actions are "
                                   "splits vs bonus/dividend — deep-history residual",
                "recent_match": price_rows[-1]["verdict"]})
    _w(OUT / "split_adjustment_validation.csv",
       ["symbol", "common_sessions", "splits", "raw_vs_yahoo_mean_pct", "raw_vs_yahoo_max_pct",
        "splitadj_vs_yahoo_mean_pct", "splitadj_vs_yahoo_max_pct",
        "eodhd_adjclose_vs_yahoo_adj_mean_pct", "recent_splitadj_vs_yahoo_pct", "verdict", "note"],
       price_rows)
    _w(OUT / "volume_adjustment_validation.csv",
       ["symbol", "splitadj_vol_vs_yahoo_mean_pct", "splitadj_vol_vs_yahoo_max_pct", "common"], vol_rows)
    _w(OUT / "corporate_action_discrepancies.csv",
       ["symbol", "splits", "splitadj_vs_yahoo_mean_pct", "eodhd_cum_factor", "suspected_cause",
        "recent_match"], disc_rows)
    print(json.dumps({"symbols": len(price_rows), "discrepancies": len(disc_rows),
                      "live": client.stats.live_calls, "cache": client.stats.cache_hits}))
    for r in price_rows:
        if "verdict" in r:
            print(f"  {r['symbol']:5} splits={r['splits']} rawMean={r['raw_vs_yahoo_mean_pct']} "
                  f"splitAdjMean={r['splitadj_vs_yahoo_mean_pct']} recent={r['recent_splitadj_vs_yahoo_pct']}% "
                  f"{r['verdict']}")
    return 0


def _m(xs):
    return round(statistics.mean(xs), 4) if xs else 0.0


def _mx(xs):
    return round(max(xs), 4) if xs else 0.0


def _w(path, fields, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
