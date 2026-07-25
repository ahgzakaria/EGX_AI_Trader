"""Phase 10/11 — first real validation + freshness reports for the daily bridge.

Compares locally-finalized Rubix bars against any available Yahoo bar (external
tiers stay disabled until audited), and reports per-symbol history freshness with
its source. Read-only / records-only; never changes a strategy parameter.

    python scripts/run_daily_bridge_validation.py [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import sys
import warnings

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from core.daily_bridge.normalized_cache import NormalizedDailyCache  # noqa: E402
from core.daily_bridge.reconcile import reconcile_sessions  # noqa: E402
from core.egx_session import cairo_now  # noqa: E402
from core.symbols import load_symbols  # noqa: E402
from providers.local_cache_provider import LocalCacheProvider  # noqa: E402
from scalping_expected_range.config import ExpectedRangeConfig  # noqa: E402
from scalping_expected_range.historical_selector import HistoricalSelector  # noqa: E402

REPORT_DIR = Path("reports/daily_bridge")


def _yahoo_bars(symbol, session_date, cache, period):
    try:
        frame = cache.load_cached("yahoo", symbol, period, "1d", allow_expired=True)
    except Exception:
        return None
    sub = frame[frame.index.map(lambda t: t.date().isoformat()) == session_date]
    if sub.empty:
        return None
    r = sub.iloc[-1]
    return {"canonical_symbol": symbol, "session_date": session_date,
            "Open": float(r["Open"]), "High": float(r["High"]), "Low": float(r["Low"]),
            "Close": float(r["Close"]), "Volume": float(r["Volume"])}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-07-22")
    args = ap.parse_args(argv)
    cfg = ExpectedRangeConfig.load()

    from config.settings_manager import settings
    market = settings.get("market_data")
    period = settings.get("data").get("history_period", "10y")
    cache = LocalCacheProvider(market.get("cache_path", "data/market_data_cache.sqlite"),
                               source_provider="yahoo")
    ndc = NormalizedDailyCache(cfg.normalized_daily_cache_path)

    rubix_bars = [b for b in ndc.all_active(args.date) if b.get("source_type") == "RUBIX_DERIVED"]
    symbols = [b["canonical_symbol"] for b in rubix_bars]
    external = [b for s in symbols for b in [_yahoo_bars(s, args.date, cache, period)] if b]
    recon = reconcile_sessions(rubix_bars, external)
    _write(REPORT_DIR / "provider_reconciliation.csv",
           ["canonical_symbol", "session_date", "result", "open_diff_pct", "high_diff_pct",
            "low_diff_pct", "close_diff_pct", "volume_ratio", "detail"],
           [r.as_row() for r in recon])
    from collections import Counter
    recon_summary = dict(Counter(r.result for r in recon))

    # symbol-level history freshness (Phase 11), as of the NEXT session's snapshot.
    import datetime as _dt
    next_day = (_dt.date.fromisoformat(args.date) + _dt.timedelta(days=1))
    now = cairo_now().replace(year=next_day.year, month=next_day.month, day=next_day.day,
                              hour=9, minute=45)
    sel = HistoricalSelector(config=cfg, now=now)
    rows = []
    for s in load_symbols("data/symbols.csv"):
        a = sel.analyze(s)
        p = a.provenance
        rows.append({"canonical_symbol": s, "as_of_snapshot": next_day.isoformat(),
                     "latest_completed_session": p.latest_completed_session,
                     "expected_latest_session": p.expected_latest_session,
                     "data_status": p.data_status, "freshness_status": p.freshness_status,
                     "historical_provider": p.historical_provider,
                     "rubix_overlay_sessions": p.rubix_overlay_sessions,
                     "data_age_sessions": p.data_age_sessions})
    _write(REPORT_DIR / "symbol_history_freshness.csv",
           ["canonical_symbol", "as_of_snapshot", "latest_completed_session",
            "expected_latest_session", "data_status", "freshness_status",
            "historical_provider", "rubix_overlay_sessions", "data_age_sessions"], rows)

    current = sum(1 for r in rows if r["data_status"] == "OK")
    with_overlay = sum(1 for r in rows if r["rubix_overlay_sessions"])
    out = {"session_date": args.date, "rubix_final_bars": len(rubix_bars),
           "yahoo_overlap_bars": len(external), "reconciliation": recon_summary,
           "next_snapshot": next_day.isoformat(),
           "symbols_current_next_snapshot": current,
           "symbols_using_rubix_overlay": with_overlay,
           "reports": ["reports/daily_bridge/provider_reconciliation.csv",
                       "reports/daily_bridge/symbol_history_freshness.csv"]}
    print(json.dumps(out, indent=2, default=str))
    return out


def _write(path, fields, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


if __name__ == "__main__":
    main()
