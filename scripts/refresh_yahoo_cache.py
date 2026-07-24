"""Daily incremental refresh of the Yahoo daily-history local cache.

Why this exists: the Swing/Daily route reads the local cache with
allow_expired=True and only downloads on a full cache miss, so the cache never
advances on its own. This script is the missing "refresh" step — run it daily
(via Windows Task Scheduler) so the latest completed Yahoo session flows into
the cache the Swing/Daily dashboard reads.

It is incremental and safe:
  * downloads only a short recent window per symbol and appends new dates onto
    the existing cached 10-year history (never re-downloads 10y every day),
  * never overwrites/repairs existing candles — new dates are added, existing
    dates keep their cached values (Yahoo is canonical for a date it already
    provided),
  * isolates per-symbol failures so one bad symbol never stops the batch,
  * never touches the Rubix DB, strategy, indicators, or any trading logic.

Run:  python D:\\EGX_AI_Trader\\scripts\\refresh_yahoo_cache.py
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
import sys
import time
from pathlib import Path

# Make the script runnable from any working directory (Task Scheduler safe).
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import pandas as pd  # noqa: E402

from config.settings_manager import settings  # noqa: E402
from core.symbols import load_symbols  # noqa: E402
from providers.base_provider import ProviderError  # noqa: E402
from providers.local_cache_provider import LocalCacheProvider  # noqa: E402
from providers.yahoo_provider import YahooProvider  # noqa: E402

RECENT_WINDOW = "6mo"  # short download that comfortably overlaps the cached tail
REQUEST_PAUSE_SECONDS = 0.25  # be polite to Yahoo across 265 symbols
LOG_DIR = ROOT / "logs"


def _log(handle, message):
    line = f"{datetime.now(timezone.utc).astimezone().isoformat()}  {message}"
    print(line)
    handle.write(line + "\n")
    handle.flush()


def refresh(symbols_source="data/symbols.csv"):
    settings.reload()
    data_cfg = settings.get("data")
    period = data_cfg.get("history_period", "10y")   # cache key must match the swing route
    interval = data_cfg.get("interval", "1d")
    market_cfg = settings.get("market_data")
    cache = LocalCacheProvider(
        market_cfg.get("cache_path", "data/market_data_cache.sqlite"),
        source_provider="yahoo",
    )
    yahoo = YahooProvider()
    symbols = load_symbols(symbols_source)

    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / "yahoo_cache_refresh.log"
    updated, unchanged, failed = 0, 0, 0
    latest_dates = []

    with log_path.open("a", encoding="utf-8") as handle:
        _log(handle, f"=== Yahoo cache refresh start · {len(symbols)} symbols · "
                     f"period={period} interval={interval} ===")
        for symbol in symbols:
            try:
                recent = yahoo.load_history(symbol, RECENT_WINDOW, interval)
            except ProviderError as error:
                failed += 1
                _log(handle, f"FAIL {symbol}: {type(error).__name__}")
                continue
            except Exception as error:  # defensive: never let one symbol abort the job
                failed += 1
                _log(handle, f"FAIL {symbol}: {type(error).__name__}: {str(error)[:80]}")
                continue

            try:
                existing = cache.load_cached("yahoo", symbol, period, interval, allow_expired=True)
            except ProviderError:
                existing = None

            if existing is None or existing.empty:
                combined = recent
            else:
                combined = pd.concat([existing, recent])
                # Existing cached dates are canonical; new dates are appended.
                combined = combined[~combined.index.duplicated(keep="first")].sort_index()

            new_latest = pd.Timestamp(combined.index.max()).date()
            old_latest = (pd.Timestamp(existing.index.max()).date()
                          if existing is not None and not existing.empty else None)

            combined.attrs["market_data"] = {
                "provider": "yahoo",
                "received_timestamp": datetime.now(timezone.utc).astimezone().isoformat(),
            }
            cache.store("yahoo", symbol, period, interval, combined)

            latest_dates.append(new_latest)
            if old_latest is None or new_latest > old_latest:
                updated += 1
            else:
                unchanged += 1
            time.sleep(REQUEST_PAUSE_SECONDS)

        newest = max(latest_dates) if latest_dates else None
        _log(handle, f"=== Done · advanced={updated} unchanged={unchanged} "
                     f"failed={failed} · newest cached session={newest} ===")

    return {"updated": updated, "unchanged": unchanged, "failed": failed,
            "newest_session": str(newest) if newest else None,
            "log": str(log_path)}


if __name__ == "__main__":
    import json

    print(json.dumps(refresh(), indent=2, default=str))
