"""Full-universe EODHD × Yahoo shadow audit for VERIFIED mappings (Parts 3,5,6,7).

    python -m scripts.audit_eodhd_full_universe [--limit N]

Compares each verified-mapped symbol (EODHD vs Yahoo) over ~1 year, holiday-aware,
categorizes agreement, flags price-scale anomalies (cross-checked vs Rubix/local), and
writes the coverage + manual-review CSVs. Both providers are disk-cached so the run is
resumable and reruns cost ~no API. Read-only; no provider switch, no strategy change.
"""

from __future__ import annotations

import csv
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd                                         # noqa: E402

from core.environment import load_project_environment       # noqa: E402
from core.egx_calendar import effective_holidays            # noqa: E402
from core.egx_session import is_regular_trading_day         # noqa: E402
from providers.eodhd_client import EODHDClient              # noqa: E402
from providers.eodhd_historical_provider import EODHDHistoricalProvider  # noqa: E402
from providers.eodhd_reconciliation import (                # noqa: E402
    categorize_agreement, is_scale_anomaly, scale_bucket)
from providers.yahoo_provider import YahooProvider          # noqa: E402

OUT = PROJECT_ROOT / "reports" / "eodhd"
YCACHE = PROJECT_ROOT / "data" / "yahoo_cache"
_ABS = 1e-9


def _mapped_symbols():
    df = pd.read_csv(PROJECT_ROOT / "reports" / "eodhd_symbol_mapping.csv")
    return [str(s) for s in df[df["mapping_status"] == "VERIFIED_EXACT"]["internal_symbol"]]


def _yahoo_cached(base, period="1y"):
    YCACHE.mkdir(parents=True, exist_ok=True)
    fp = YCACHE / f"{base}_{period}.csv"
    if fp.is_file() and (pd.Timestamp.now() - pd.Timestamp(fp.stat().st_mtime, unit="s")).days < 2:
        try:
            df = pd.read_csv(fp, parse_dates=["Date"])
            df["Date"] = df["Date"].dt.date
            return df
        except Exception:
            pass
    try:
        raw = YahooProvider().load_history(f"{base}.CA", period=period, interval="1d")
    except Exception:
        return None
    if raw is None or raw.empty:
        pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"]).to_csv(fp, index=False)
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"])
    d = raw.reset_index()
    dc = "Date" if "Date" in d.columns else d.columns[0]
    out = pd.DataFrame({
        "Date": pd.to_datetime(d[dc]).dt.date,
        "Open": pd.to_numeric(d.get("Open"), errors="coerce"),
        "High": pd.to_numeric(d.get("High"), errors="coerce"),
        "Low": pd.to_numeric(d.get("Low"), errors="coerce"),
        "Close": pd.to_numeric(d.get("Close"), errors="coerce"),
        "Adj Close": pd.to_numeric(d.get("Adj Close", d.get("Close")), errors="coerce"),
        "Volume": pd.to_numeric(d.get("Volume"), errors="coerce").fillna(0),
    }).dropna(subset=["Open", "High", "Low", "Close"]).drop_duplicates("Date").sort_values("Date")
    out.to_csv(fp, index=False)
    return out.reset_index(drop=True)


def _rubix_close(base):
    # Rubix stores bare tickers (no .CA suffix); a 0.0 last_price is an invalid/illiquid
    # quote and must not be treated as a real close.
    try:
        c = sqlite3.connect(f"file:{PROJECT_ROOT / 'data' / 'rubix_live_market.db'}?mode=ro", uri=True)
        for tk in (base, f"{base}.CA"):
            row = c.execute("SELECT last_price, substr(market_timestamp,1,10) FROM quotes "
                            "WHERE ticker=? ORDER BY id DESC LIMIT 1", (tk,)).fetchone()
            if row and row[0] and float(row[0]) > 0:
                return (float(row[0]), str(row[1]))
        return (None, None)
    except Exception:
        return (None, None)


def _metrics(base, e, y, holidays):
    recent_cut = date.today() - timedelta(days=45)
    ed, yd = (set(e["Date"]) if not e.empty else set()), (set(y["Date"]) if not y.empty else set())
    common = sorted(ed & yd)
    em, ym = (e.set_index("Date"), y.set_index("Date")) if common else (e, y)
    close_ratios, pct, adjgap, volpct, ohlc_abs = [], [], [], [], []
    for dd in common:
        er, yr = em.loc[dd], ym.loc[dd]
        if yr["Close"] > _ABS:
            close_ratios.append(er["Close"] / yr["Close"])
            if dd >= recent_cut:
                pct.append(abs(er["Close"] - yr["Close"]) / abs(yr["Close"]) * 100)
                ohlc_abs.append(max(abs(er[c] - yr[c]) for c in ("Open", "High", "Low", "Close")))
                adjgap.append(abs(_r(er["Adj Close"], er["Close"]) - _r(yr["Adj Close"], yr["Close"])))
                if yr["Volume"] > 0:
                    volpct.append(abs(er["Volume"] - yr["Volume"]) / yr["Volume"] * 100)
    scale = _median(close_ratios)
    # holiday-aware coverage gap in the overlap window
    only_e = [d for d in (ed - yd) if is_regular_trading_day(d, holidays)]
    only_y = [d for d in (yd - ed) if is_regular_trading_day(d, holidays)]
    return {
        "symbol": base, "eodhd_symbol": f"{base}.EGX", "yahoo_symbol": f"{base}.CA",
        "eodhd_rows": int(len(e)), "yahoo_rows": int(len(y)), "common_sessions": len(common),
        "eodhd_first": e["Date"].min().isoformat() if not e.empty else None,
        "yahoo_first": y["Date"].min().isoformat() if not y.empty else None,
        "eodhd_latest": e["Date"].max().isoformat() if not e.empty else None,
        "yahoo_latest": y["Date"].max().isoformat() if not y.empty else None,
        "only_in_eodhd_trading_days": len(only_e), "only_in_yahoo_trading_days": len(only_y),
        "close_scale_ratio": round(scale, 4) if scale else None,
        "scale_bucket": scale_bucket(scale),
        "mean_close_pct_diff": round(_avg(pct), 4), "max_close_pct_diff": round(_max(pct), 4),
        "max_ohlc_abs_diff": round(_max(ohlc_abs), 4),
        "max_adj_ratio_gap": round(_max(adjgap), 4), "mean_volume_pct_diff": round(_avg(volpct), 2),
    }


def main(argv=None):
    load_project_environment()
    OUT.mkdir(parents=True, exist_ok=True)
    limit = None
    if argv and "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    holidays = effective_holidays()
    symbols = _mapped_symbols()
    if limit:
        symbols = symbols[:limit]
    client = EODHDClient(max_live_calls=400)
    prov = EODHDHistoricalProvider(client)
    efrom = (date.today() - timedelta(days=380)).isoformat()

    results, anomalies, done = [], [], 0
    for base in symbols:
        e_res = prov.load_history(base, from_date=efrom)
        y = _yahoo_cached(base, "1y")
        if y is None:
            y = pd.DataFrame(columns=e_res.frame.columns)
        m = _metrics(base, e_res.frame, y, holidays)
        m["eodhd_error"] = e_res.error
        m["zero_invalid_dropped"] = (e_res.quality.get("dropped_zero_or_negative", 0)
                                     + e_res.quality.get("dropped_non_numeric", 0))
        m["category"] = categorize_agreement(m)
        results.append(m)
        if is_scale_anomaly(m["close_scale_ratio"]):
            rub_close, rub_date = _rubix_close(base)
            e_last = e_res.frame.iloc[-1] if not e_res.frame.empty else {}
            y_last = y.iloc[-1] if not y.empty else {}
            e_close = e_last.get("Close") if hasattr(e_last, "get") else None
            e_to_rubix = round(e_close / rub_close, 4) if (rub_close and e_close) else None
            # Adjudicate with the live Rubix close where available.
            cause, verified, status, resolution = (
                "scale/currency/listing or stale-provider — see ratios", "unresolved",
                "NEEDS_MANUAL_REVIEW", "")
            if e_to_rubix is not None and abs(e_to_rubix - 1) <= 0.05:
                cause = "Yahoo series stale/mis-scaled; EODHD matches the live Rubix close"
                verified, status = "rubix_confirms_eodhd", "RESOLVED_EODHD_CORRECT"
                resolution = "use EODHD for this symbol; Yahoo .CA line is unreliable"
            elif rub_close is None:
                resolution = "no valid Rubix price to adjudicate (illiquid/zero) — hold for review"
            anomalies.append({
                "symbol": base, "eodhd_symbol": f"{base}.EGX",
                "session_date": m["eodhd_latest"], "eodhd_close": e_close,
                "yahoo_close": y_last.get("Close") if hasattr(y_last, "get") else None,
                "rubix_or_bridge_close": rub_close, "rubix_date": rub_date, "local_close": "",
                "eodhd_to_yahoo_ratio": m["close_scale_ratio"], "eodhd_to_rubix_ratio": e_to_rubix,
                "suspected_cause": cause, "verified_source": verified,
                "status": status, "resolution": resolution})
        done += 1
        if done % 25 == 0:
            print(f"...{done}/{len(symbols)} live={client.stats.live_calls} cache={client.stats.cache_hits}",
                  flush=True)

    _write(OUT / "full_universe_symbol_results.csv", list(results[0].keys()) if results else [], results)
    _write(OUT / "price_scale_anomalies.csv",
           ["symbol", "eodhd_symbol", "session_date", "eodhd_close", "yahoo_close",
            "rubix_or_bridge_close", "rubix_date", "local_close", "eodhd_to_yahoo_ratio",
            "eodhd_to_rubix_ratio", "suspected_cause", "verified_source", "status", "resolution"],
           anomalies)
    mrq = [r for r in results if r["category"] in
           ("PRICE_SCALE_ANOMALY", "DATA_UNAVAILABLE", "MANUAL_REVIEW", "ADJUSTMENT_CONVENTION_DIFFERENCE")]
    _write(OUT / "manual_review_queue.csv", list(results[0].keys()) if results else [], mrq)

    from collections import Counter
    cats = Counter(r["category"] for r in results)
    summary = {
        "verified_mapped_audited": len(results), "categories": dict(cats),
        "scale_anomalies": len(anomalies),
        "clean_match": cats.get("CLEAN_MATCH", 0),
        "eodhd_fresher_or_equal": sum(1 for r in results
                                      if r["eodhd_latest"] and r["yahoo_latest"]
                                      and r["eodhd_latest"] >= r["yahoo_latest"]),
        "api_live_calls": client.stats.live_calls, "api_cache_hits": client.stats.cache_hits,
    }
    _write(OUT / "full_universe_shadow_summary.csv", list(summary.keys()),
           [{k: (json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v)
             for k, v in summary.items()}])
    (OUT / "full_universe_shadow_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def _r(a, b):
    return a / b if abs(b) > _ABS else 1.0


def _median(xs):
    xs = sorted(x for x in xs if x)
    return xs[len(xs) // 2] if xs else None


def _avg(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _max(xs):
    return max(xs) if xs else 0.0


def _write(path, fields, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
