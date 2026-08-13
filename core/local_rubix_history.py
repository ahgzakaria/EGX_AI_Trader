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


def default_bridge_cache():
    """The configured normalized daily cache, or ``None`` when unavailable.

    Never raises: a missing cache must degrade to "no Rubix tail", not to a
    symbol that cannot be analysed at all.
    """

    try:
        from core.daily_bridge.normalized_cache import NormalizedDailyCache
        from scalping_expected_range.config import ExpectedRangeConfig

        return NormalizedDailyCache(
            ExpectedRangeConfig.load().normalized_daily_cache_path
        )
    except Exception:
        return None


def append_bridge_bars(frame, symbol, *, cache=None, not_after=None):
    """Append REAL Rubix bridge sessions after ``frame``'s last date.

    Shared by both history paths: the frozen-seed path for EODHD-unsupported
    symbols, and the EODHD path, which needs the same tail because EODHD
    publishes a completed session a day — sometimes two — late, and a history
    that stops before the last completed session is refused as stale by the
    daily guard.

    Guarantees, all of which the callers depend on:

    * **append only.** A date already in ``frame`` is never overwritten. If the
      bridge disagrees with an existing bar it is counted as a conflict and
      surfaced; the existing bar wins. The provider that owns a session keeps
      it.
    * **FINAL only.** ``FINAL`` / ``FINAL_CONTINUOUS`` bars, nothing partial.
    * **no cache, no change.** ``frame`` is returned untouched, so an absent or
      broken cache can never degrade an otherwise good history.
    * **never past ``not_after``.** The tail fills the gap up to the session the
      daily guard expects; it must not run ahead of it. Rubix finalizes a
      session as soon as the auction closes, while the guard only advances its
      expected session after a settlement grace, so for those two hours Rubix
      legitimately holds a session the guard still considers unpublished.
      Appending it made the guard reject 186 of 241 symbols with
      SKIPPED_FUTURE_DAILY_DATE — symbols that had been fine before the tail
      existed. Filling a gap is the job; getting ahead is a regression.

    Returns ``(frame, provenance)``. ``frame`` is a copy whenever anything was
    appended, and the original object otherwise.
    """

    base = _base(symbol)
    provenance = {
        "bridge_provider": RUBIX_DAILY_BRIDGE,
        "bridge_available_sessions": 0,
        "bridge_sessions_appended": 0,
        "bridge_first_session": None,
        "bridge_latest_session": None,
        "bridge_dates": (),
        "conflict_count": 0,
        "duplicate_count": 0,
    }
    if frame is None or len(frame) == 0:
        return frame, provenance

    if cache is None:
        cache = default_bridge_cache()
    if cache is None:
        return frame, provenance

    last = pd.Timestamp(frame.index[-1])
    rows = _bridge_rows_after(base, last.date().isoformat(), cache)
    if not_after is not None:
        ceiling = pd.Timestamp(not_after)
        withheld = [r for r in rows if r["session_date"] > ceiling]
        rows = [r for r in rows if r["session_date"] <= ceiling]
        provenance["bridge_sessions_withheld_ahead"] = len(withheld)
    provenance["bridge_available_sessions"] = len(rows)
    if not rows:
        return frame, provenance

    appended, duplicate, conflict = 0, 0, 0
    dates = []
    updated = frame.copy()
    for row in rows:
        day = row["session_date"]
        if day in updated.index:
            existing = {c: updated.at[day, c] for c in CONTRACT_COLUMNS
                        if c in updated.columns}
            if _bar_conflicts(existing, row):
                conflict += 1
            else:
                duplicate += 1
            continue
        updated.loc[day] = {c: row[c] for c in CONTRACT_COLUMNS}
        dates.append(day)
        appended += 1

    provenance.update(bridge_sessions_appended=appended,
                      duplicate_count=duplicate, conflict_count=conflict)
    if not appended:
        return frame, provenance

    updated = updated.sort_index()
    updated = updated[~updated.index.duplicated(keep="last")]
    provenance.update(
        bridge_first_session=min(dates).date().isoformat(),
        bridge_latest_session=max(dates).date().isoformat(),
        bridge_dates=tuple(d.date().isoformat() for d in sorted(dates)),
    )
    return updated, provenance


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
                              bridge_cache=None, not_after=None):
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

    frame, bridge = append_bridge_bars(
        frame, base, cache=bridge_cache, not_after=not_after)
    appended = bridge["bridge_sessions_appended"]
    prov.update(
        bridge_available_sessions=bridge["bridge_available_sessions"],
        bridge_first_session=bridge["bridge_first_session"],
        bridge_latest_session=bridge["bridge_latest_session"],
    )
    duplicate, conflict = bridge["duplicate_count"], bridge["conflict_count"]

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
