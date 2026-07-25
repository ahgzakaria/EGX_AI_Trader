"""Real Local-seed + Rubix-Daily-Bridge history for EODHD-unsupported symbols.

Builds current-research daily history for symbols EODHD does not cover, from two
explicitly-labelled parts:

  1. an immutable **frozen Yahoo seed** (local cache, no network) as a historical
     bootstrap, and
  2. **real** completed sessions appended from the Rubix Daily Bridge
     (``FINAL`` / ``FINAL_CONTINUOUS`` bars only).

This module fixes the earlier defect where zero bridge bars were ever appended (the
appender read a non-existent ``close`` key while the normalized bar exposes
``official_close`` / ``continuous_close``) and then still reported the symbol READY.

Guarantees:
  * a Rubix contribution is claimed ONLY when at least one real bridge bar is appended;
  * bridge bars are appended strictly AFTER the seed's last session, sorted, de-duplicated;
  * a bridge bar that CONFLICTS with an existing seed bar is never silently overwritten —
    it is counted and surfaced;
  * both parts keep their provenance;
  * no Yahoo network call is ever made; the seed is frozen.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from core.daily_bridge.schema import FINAL, FINAL_CONTINUOUS, RUBIX_DERIVED

FROZEN_YAHOO_SEED = "FROZEN_YAHOO_SEED"
RUBIX_DAILY_BRIDGE = "RUBIX_DAILY_BRIDGE"
CONTRACT_COLUMNS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]

_PRICE_TOL = 1e-4    # relative tolerance when checking a bridge bar against a seed bar


def _base(symbol):
    return str(symbol).strip().upper().split(".")[0]


def _load_seed(symbol, *, period, interval):
    """Immutable FROZEN Yahoo seed. Reads the frozen snapshot store only — never the
    live ``market_data_cache.sqlite`` that a Yahoo refresh could advance. No network."""
    from core.frozen_seed_store import load_frozen

    frame = load_frozen(symbol)
    if frame is None or getattr(frame, "empty", True):
        return None, None
    return frame.copy(), "FROZEN_YAHOO_SEED"


def _bridge_close(bar):
    """Official close for the engine: auction clearing price when present, else continuous."""
    for field in ("official_close", "continuous_close", "auction_last"):
        val = bar.get(field)
        if val not in (None, "", "None"):
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return None


def _bridge_rows_after(base, after_date, cache):
    """Real FINAL / FINAL_CONTINUOUS Rubix bridge bars for ``base`` strictly after date."""
    try:
        rows = cache.final_bars_after(after_date, source_type=RUBIX_DERIVED)
    except Exception:
        return []
    out = []
    for bar in rows or []:
        if _base(bar.get("canonical_symbol") or bar.get("symbol") or "") != base:
            continue
        if str(bar.get("finalization_status")) != FINAL:
            continue
        if str(bar.get("continuous_bar_status")) != FINAL_CONTINUOUS:
            continue
        close = _bridge_close(bar)
        try:
            row = {"session_date": pd.Timestamp(str(bar.get("session_date"))[:10]),
                   "Open": float(bar["open"]), "High": float(bar["high"]),
                   "Low": float(bar["low"]), "Close": close, "Adj Close": close,
                   "Volume": float(bar.get("volume") or 0.0)}
        except (KeyError, TypeError, ValueError):
            continue
        if close is None or any(pd.isna(row[c]) for c in ("Open", "High", "Low")):
            continue
        out.append(row)
    out.sort(key=lambda r: r["session_date"])
    return out


def _bar_conflicts(existing, incoming):
    """True when a bridge bar materially disagrees with an existing seed bar."""
    for col in ("Open", "High", "Low", "Close"):
        a, b = existing.get(col), incoming.get(col)
        if a is None or b is None or pd.isna(a) or pd.isna(b):
            continue
        if abs(float(a) - float(b)) > _PRICE_TOL * max(1.0, abs(float(a))):
            return True
    return False


def build_local_rubix_history(symbol, *, period="10y", interval="1d",
                              bridge_cache=None):
    """Return (frame, provenance) for an EODHD-unsupported symbol.

    ``frame`` is the canonical OHLCV contract (DatetimeIndex named 'Date'); it is
    ``None`` when no seed exists. ``provenance`` always describes what was found.
    """
    base = _base(symbol)
    prov = {
        "symbol": base, "seed_provider": None, "seed_first_session": None,
        "seed_latest_session": None, "seed_rows": 0,
        "bridge_provider": RUBIX_DAILY_BRIDGE, "bridge_available_sessions": 0,
        "bridge_sessions_appended": 0, "bridge_first_session": None,
        "bridge_latest_session": None, "effective_latest_session": None,
        "conflict_count": 0, "duplicate_count": 0,
        "yahoo_seed_present": False, "yahoo_network_used": False,
    }

    seed, _key = _load_seed(base, period=period, interval=interval)
    if seed is None:
        prov["status"] = "DATA_UNAVAILABLE"
        return None, prov

    frame = seed.copy()
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.index)).tz_localize(None)
    frame.index.name = "Date"
    for col in CONTRACT_COLUMNS:
        if col not in frame.columns:
            frame[col] = frame["Close"] if col == "Adj Close" and "Close" in frame else pd.NA
    frame = frame[CONTRACT_COLUMNS].sort_index()

    seed_latest = frame.index[-1]
    prov.update(seed_provider=FROZEN_YAHOO_SEED, yahoo_seed_present=True,
                seed_first_session=frame.index[0].date().isoformat(),
                seed_latest_session=seed_latest.date().isoformat(),
                seed_rows=int(len(frame)))

    if bridge_cache is None:
        try:
            from core.daily_bridge.normalized_cache import NormalizedDailyCache
            from scalping_expected_range.config import ExpectedRangeConfig
            bridge_cache = NormalizedDailyCache(
                ExpectedRangeConfig.load().normalized_daily_cache_path)
        except Exception:
            bridge_cache = None

    appended = duplicate = conflict = 0
    bridge_dates = []
    if bridge_cache is not None:
        rows = _bridge_rows_after(base, seed_latest.date().isoformat(), bridge_cache)
        prov["bridge_available_sessions"] = len(rows)
        for row in rows:
            d = row["session_date"]
            if d in frame.index:
                existing = {c: frame.at[d, c] for c in CONTRACT_COLUMNS}
                if _bar_conflicts(existing, row):
                    conflict += 1              # never silently overwrite
                else:
                    duplicate += 1             # already present, identical enough
                continue
            frame.loc[d] = {c: row[c] for c in CONTRACT_COLUMNS}
            bridge_dates.append(d)
            appended += 1

    if appended:
        frame = frame.sort_index()
        prov.update(bridge_first_session=min(bridge_dates).date().isoformat(),
                    bridge_latest_session=max(bridge_dates).date().isoformat())

    frame = frame[~frame.index.duplicated(keep="last")]
    for col in CONTRACT_COLUMNS:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")

    prov.update(bridge_sessions_appended=appended, duplicate_count=duplicate,
                conflict_count=conflict,
                effective_latest_session=frame.index[-1].date().isoformat())
    frame.attrs["market_data"] = {
        "provider": "local_plus_rubix", "seed_provider": FROZEN_YAHOO_SEED,
        "bridge_provider": RUBIX_DAILY_BRIDGE,
        "generated_at": datetime.now(timezone.utc).isoformat(), **prov,
    }
    return frame, prov
