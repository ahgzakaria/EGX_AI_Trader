"""Bonus-vs-split reconciliation + event volume validation (Parts 3-4).

    python -m scripts.audit_eodhd_corp_action_reconciliation

For each split event of the residual/corp-action symbols, measures the actual EODHD raw
price discontinuity at the ex-date and compares it to the declared split ratio, plus the
volume behavior, to classify the action (true split vs bonus/capital-increase/provider
difference) and the correct volume rule per action type. Evidence-based; no universal
rule is assumed. No provider change.
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
from providers.eodhd_adjustment import parse_split_ratio    # noqa: E402
from providers.eodhd_client import EODHDClient              # noqa: E402
from providers.yahoo_provider import YahooProvider          # noqa: E402

SYMBOLS = ["COMI", "EAST", "SKPC", "EFIH", "SWDY", "FWRY", "ABUK", "TMGH", "HRHO"]
OUT = PROJECT_ROOT / "reports" / "eodhd"


def _eodhd_daily(base, client):
    raw = client.eod(f"{base}.EGX", order="a")
    return pd.DataFrame([{"Date": pd.to_datetime(r["date"]).date(), "close": float(r["close"]),
                          "volume": float(r.get("volume") or 0)}
                         for r in raw if r.get("close")]).set_index("Date")


def _yahoo_daily(base):
    f = YahooProvider().load_history(f"{base}.CA", period="max", interval="1d")
    if f is None or f.empty:
        return None
    d = f.reset_index()
    dc = "Date" if "Date" in d.columns else d.columns[0]
    return pd.DataFrame({"Date": pd.to_datetime(d[dc]).dt.date,
                         "close": pd.to_numeric(d["Close"], errors="coerce"),
                         "volume": pd.to_numeric(d.get("Volume"), errors="coerce").fillna(0)
                         }).dropna(subset=["close"]).set_index("Date")


def _around(df, ex_date, col):
    if df is None:
        return (None, None)
    before = df[df.index < ex_date]
    after = df[df.index >= ex_date]
    b = before[col].iloc[-1] if not before.empty else None
    a = after[col].iloc[0] if not after.empty else None
    return (b, a)


def _classify_action(ratio, eodhd_price_drop, yahoo_price_drop):
    """Classify from the ratio shape + whether the raw price actually dropped by it."""
    clean = ratio in (2.0, 1.5, 1.25, 3.0, 4.0) or abs(round(ratio * 2) - ratio * 2) < 1e-6
    # did EODHD raw price drop by ~the ratio? (a genuine split divides price by ratio)
    price_matches = eodhd_price_drop is not None and abs(eodhd_price_drop - ratio) / ratio <= 0.15
    if price_matches and yahoo_price_drop is not None and abs(yahoo_price_drop - ratio) / ratio <= 0.20:
        return "SPLIT", "MULTIPLY_BY_FACTOR"
    if price_matches:
        return "SPLIT_PROVIDER_DIFF", "EVENT_SPECIFIC"   # EODHD price drops, Yahoo differs
    if not price_matches:
        # ratio present but price didn't drop proportionally → bonus/capital classification
        return ("STOCK_DIVIDEND_BONUS" if clean else "CAPITAL_INCREASE_OR_BONUS",
                "KEEP_RAW" if not clean else "EVENT_SPECIFIC")
    return "PROVIDER_CLASSIFICATION_DIFFERENCE", "UNRESOLVED"


def main():
    load_project_environment()
    OUT.mkdir(parents=True, exist_ok=True)
    client = EODHDClient(max_live_calls=60)
    recon, vol = [], []
    for base in SYMBOLS:
        e = _eodhd_daily(base, client)
        y = _yahoo_daily(base)
        splits = client.get_json(f"splits/{base}.EGX", cache_ttl_seconds=7 * 86400)
        for s in (splits or []):
            try:
                ratio = parse_split_ratio(s.get("split"))
            except Exception:
                continue
            ex = pd.to_datetime(s.get("date")).date()
            eb, ea = _around(e, ex, "close")
            yb, ya = _around(y, ex, "close")
            e_drop = (eb / ea) if (eb and ea and ea > 0) else None
            y_drop = (yb / ya) if (yb and ya and ya > 0) else None
            action, vol_rule = _classify_action(ratio, e_drop, y_drop)
            recon.append({
                "symbol": base, "effective_date": ex.isoformat(), "eodhd_action_type": "SPLIT",
                "eodhd_ratio": round(ratio, 5), "raw_split_string": s.get("split"),
                "eodhd_price_drop_factor": round(e_drop, 4) if e_drop else None,
                "yahoo_price_drop_factor": round(y_drop, 4) if y_drop else None,
                "classified_action": action, "volume_rule": vol_rule,
                "evidence": f"declared {ratio:.3f}; EODHD raw price ×{e_drop:.3f}" if e_drop
                            else "declared ratio; insufficient price evidence"})
            # volume behavior at the event
            evb, eva = _around(e, ex, "volume")
            yvb, yva = _around(y, ex, "volume")
            vol.append({
                "symbol": base, "effective_date": ex.isoformat(), "ratio": round(ratio, 4),
                "eodhd_vol_before": evb, "eodhd_vol_after": eva,
                "yahoo_vol_before": yvb, "yahoo_vol_after": yva,
                "eodhd_vol_jump": round(eva / evb, 3) if (evb and eva and evb > 0) else None,
                "yahoo_vol_jump": round(yva / yvb, 3) if (yvb and yva and yvb > 0) else None,
                "action_type": recon[-1]["classified_action"], "volume_policy": vol_rule})
    _w(OUT / "corporate_action_reconciliation.csv",
       ["symbol", "effective_date", "eodhd_action_type", "eodhd_ratio", "raw_split_string",
        "eodhd_price_drop_factor", "yahoo_price_drop_factor", "classified_action",
        "volume_rule", "evidence"], recon)
    _w(OUT / "volume_adjustment_event_validation.csv",
       ["symbol", "effective_date", "ratio", "eodhd_vol_before", "eodhd_vol_after",
        "yahoo_vol_before", "yahoo_vol_after", "eodhd_vol_jump", "yahoo_vol_jump",
        "action_type", "volume_policy"], vol)
    from collections import Counter
    print(json.dumps({"actions": len(recon),
                      "action_classes": dict(Counter(r["classified_action"] for r in recon)),
                      "volume_rules": dict(Counter(r["volume_rule"] for r in recon)),
                      "live": client.stats.live_calls}, ensure_ascii=False))
    return 0


def _w(path, fields, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
