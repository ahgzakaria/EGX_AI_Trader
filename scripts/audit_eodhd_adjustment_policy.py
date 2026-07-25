"""Adjusted-price policy audit: EODHD vs Yahoo close/adjusted (Part 4).

Documents the frozen canonical behavior and compares, for COMI + corporate-action
examples, EODHD close/adjusted_close vs Yahoo Close/Adj Close. Read-only; changes no
behavior and never rewrites Close↔Adj Close.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd                                         # noqa: E402

from core.environment import load_project_environment       # noqa: E402
from providers.eodhd_client import EODHDClient              # noqa: E402
from providers.yahoo_provider import YahooProvider          # noqa: E402

SYMBOLS = ["COMI", "SWDY", "FWRY", "TMGH", "EAST"]          # incl. dividend/split names
OUT = PROJECT_ROOT / "reports" / "eodhd"
_ABS = 1e-9


def _eodhd(base, client):
    raw = client.eod(f"{base}.EGX", period="d", order="a")
    rows = [r for r in raw if isinstance(r, dict) and r.get("close")]
    if not rows:
        return None
    df = pd.DataFrame([{"Date": pd.to_datetime(r["date"]).date(),
                        "close": float(r["close"]),
                        "adj": float(r.get("adjusted_close", r["close"]))} for r in rows])
    return df.sort_values("Date").reset_index(drop=True)


def _yahoo(base):
    f = YahooProvider().load_history(f"{base}.CA", period="max", interval="1d")
    if f is None or f.empty:
        return None
    d = f.reset_index()
    dc = "Date" if "Date" in d.columns else d.columns[0]
    return pd.DataFrame({"Date": pd.to_datetime(d[dc]).dt.date,
                         "Close": pd.to_numeric(d["Close"], errors="coerce"),
                         "Adj": pd.to_numeric(d.get("Adj Close", d["Close"]), errors="coerce")
                         }).dropna().sort_values("Date").reset_index(drop=True)


def main():
    load_project_environment()
    OUT.mkdir(parents=True, exist_ok=True)
    client = EODHDClient(max_live_calls=20)
    rows = []
    for base in SYMBOLS:
        e, y = _eodhd(base, client), _yahoo(base)
        if e is None or y is None or e.empty or y.empty:
            rows.append({"symbol": base, "note": "data unavailable"})
            continue
        common = sorted(set(e["Date"]) & set(y["Date"]))
        if not common:
            rows.append({"symbol": base, "note": "no common dates"})
            continue
        em, ym = e.set_index("Date"), y.set_index("Date")
        first, last = common[0], common[-1]
        ef, el, yf, yl = em.loc[first], em.loc[last], ym.loc[first], ym.loc[last]
        first_ratio = _r(ef["close"], yf["Close"])         # split boundary marker
        last_ratio = _r(el["close"], yl["Close"])
        e_div = _r(el["close"], el["adj"])                 # EODHD close/adj (split+div)
        y_div = _r(yl["Close"], yl["Adj"])                 # Yahoo close/adj (dividends)
        split_detected = abs(first_ratio - last_ratio) / max(last_ratio, _ABS) > 0.20
        rows.append({
            "symbol": base, "eodhd_symbol": f"{base}.EGX", "first_common": first.isoformat(),
            "last_common": last.isoformat(),
            "eodhd_close_last": round(el["close"], 4), "eodhd_adjclose_last": round(el["adj"], 4),
            "yahoo_close_last": round(yl["Close"], 4), "yahoo_adjclose_last": round(yl["Adj"], 4),
            "eodhd_vs_yahoo_close_last": round(last_ratio, 4),
            "eodhd_vs_yahoo_close_first": round(first_ratio, 4),
            "eodhd_close_adj_ratio": round(e_div, 4), "yahoo_close_adj_ratio": round(y_div, 4),
            "split_detected_in_history": split_detected,
            "interpretation": ("Yahoo raw Close is split-adjusted; EODHD close is fully "
                               "unadjusted — diverge across splits, match recently"
                               if split_detected else
                               "no split in overlap; recent raw closes align"),
            "migration_risk": ("HIGH for lookback windows spanning the split (raw-close "
                               "discontinuity) — recent forward paper unaffected"
                               if split_detected else "LOW for recent windows"),
        })
    fields = ["symbol", "eodhd_symbol", "first_common", "last_common", "eodhd_close_last",
              "eodhd_adjclose_last", "yahoo_close_last", "yahoo_adjclose_last",
              "eodhd_vs_yahoo_close_last", "eodhd_vs_yahoo_close_first", "eodhd_close_adj_ratio",
              "yahoo_close_adj_ratio", "split_detected_in_history", "interpretation",
              "migration_risk", "note"]
    with (OUT / "adjustment_policy_audit.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        print(f"  {r.get('symbol'):5} lastRatio={r.get('eodhd_vs_yahoo_close_last')} "
              f"firstRatio={r.get('eodhd_vs_yahoo_close_first')} split={r.get('split_detected_in_history')} "
              f"eodhdCloseAdj={r.get('eodhd_close_adj_ratio')} yahooCloseAdj={r.get('yahoo_close_adj_ratio')}")
    print(f"live={client.stats.live_calls} cache={client.stats.cache_hits}")
    return 0


def _r(a, b):
    return a / b if abs(b) > _ABS else 1.0


if __name__ == "__main__":
    raise SystemExit(main())
