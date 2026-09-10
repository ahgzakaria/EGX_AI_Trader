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

#: The second supplier of the same tail, used only where the first has nothing.
#:
#: On 2026-09-07 the collector stopped eight minutes into the session, no Rubix
#: bar could be built from 29 captured minutes, and every symbol fell back to
#: plain EODHD: `eodhd_plus_rubix` went from 187 of 222 symbols the session
#: before to zero, and the history stopped a day short of the last completed
#: session. The tail is not a nicety; on a normal day most symbols depend on it.
#:
#: Deliberately second. Rubix's close is built from minutes this project
#: captured itself; the export's is the exchange's official auction close.
#: Measured on 2026-09-06, where both exist for 7 comparable symbols, volume
#: agrees exactly (ratio 1.0000, all within 5%) and the closes differ by 0.15%
#: at the median and 0.91% at worst. Neither is wrong -- they are different
#: measurements -- so the one this project can account for end to end goes
#: first, and this fills only what it leaves empty.
MUBASHER_EXPORT_BRIDGE = "MUBASHER_EXPORT_BRIDGE"
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
    """Return ``(close, official)`` -- the auction price when it exists.

    The fallback to the continuous close is not a rounding difference. Across
    1,791 bars holding both, they are identical in only 23.1% of sessions; the
    median gap is 0.2451%, 9.72% differ by more than one percent, and the worst
    is 11.24%. The closing auction moves the price, which is what it is for.

    In 10.11% of those sessions the official close also lands outside the
    continuous session's own high and low, so a bar missing its auction can
    understate the day's range as well as misstate its close.

    Returning the flag rather than the price alone is the point: an EGX daily
    candle closes after the 14:25 auction, and a caller taking a continuous
    close as if it were that is measuring 14:18 and calling it 14:30.
    """

    auction = bar.get("official_close")
    if auction not in (None, "", "None"):
        try:
            return float(auction), True
        except (TypeError, ValueError):
            pass
    for field in ("continuous_close", "auction_last"):
        val = bar.get(field)
        if val not in (None, "", "None"):
            try:
                return float(val), False
            except (TypeError, ValueError):
                continue
    return None, False


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
        close, official = _bridge_close(bar)
        try:
            row = {"session_date": pd.Timestamp(str(bar.get("session_date"))[:10]),
                   "Open": float(bar["open"]), "High": float(bar["high"]),
                   "Low": float(bar["low"]), "Close": close, "Adj Close": close,
                   "Volume": float(bar.get("volume") or 0.0),
                   # Not a column: carried alongside so the provenance can name
                   # the sessions whose close is not the auction's.
                   "_official_close": official}
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


def append_bridge_bars(frame, symbol, *, cache=None, not_after=None,
                       export_rows=None):
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
        "bridge_supplements": (),
        "bridge_unconfirmed_close_count": 0,
        "bridge_unconfirmed_close_dates": (),
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

    # Only the sessions Rubix left empty, and named when it happens.
    #
    # Reached only past the `cache is None` return above, so "no cache, no
    # change" still holds exactly: an absent or broken bridge cache degrades to
    # no tail rather than quietly to a different source. The case this exists
    # for is a cache that works and has no bar for today, which is what
    # 2026-09-07 was.
    #
    # Injectable, because without it a unit test with a stub cache still read
    # the production store and appended real market data to its fixture.
    lookup = _export_rows_after if export_rows is None else export_rows
    supplied = {row["session_date"] for row in rows}
    extra = [row for row in lookup(base, last.date().isoformat())
             if row["session_date"] not in supplied]
    if extra:
        rows = sorted(rows + extra, key=lambda row: row["session_date"])
        provenance["bridge_supplements"] = tuple(
            row["session_date"].date().isoformat() for row in extra)
        provenance["bridge_provider"] = (
            f"{RUBIX_DAILY_BRIDGE}+{MUBASHER_EXPORT_BRIDGE}")
    if not_after is not None:
        ceiling = pd.Timestamp(not_after)
        withheld = [r for r in rows if r["session_date"] > ceiling]
        rows = [r for r in rows if r["session_date"] <= ceiling]
        provenance["bridge_sessions_withheld_ahead"] = len(withheld)
    provenance["bridge_available_sessions"] = len(rows)
    if not rows:
        return frame, provenance

    appended, duplicate, conflict = 0, 0, 0
    dates, unconfirmed = [], []
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
        if not row.get("_official_close", True):
            unconfirmed.append(day)
        appended += 1

    # Named, not filtered. A session whose auction never arrived is still the
    # only record of that day, and dropping it would leave the history short of
    # the last completed session -- the exact staleness this tail exists to
    # fix. The caller decides whether a close it cannot confirm is good enough
    # for what it is about to do.
    provenance.update(bridge_sessions_appended=appended,
                      duplicate_count=duplicate, conflict_count=conflict,
                      bridge_unconfirmed_close_count=len(unconfirmed),
                      bridge_unconfirmed_close_dates=tuple(
                          d.date().isoformat() for d in sorted(unconfirmed)))
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


def _confirmed(bar):
    """True unless the measured store says this close is not the auction."""
    flag = bar.get("CloseConfirmed")
    if flag is None or pd.isna(flag):
        return True
    return bool(int(flag))


def _export_rows_after(base, after_date):
    """Measured-export bars strictly after ``after_date``, in the tail's shape.

    Split-adjusted and not dividend-adjusted, which is the same basis the Rubix
    tail is on and the reason the existing series name already says RAW_TAIL:
    a corporate action inside the appended window would break either of them,
    and `bridge_tail_blocked` is what guards that for both.
    """

    try:
        from sector_flow.measured_turnover import frame_for
    except Exception:
        return []

    frame = frame_for(f"{base}.CA")
    if frame is None or frame.empty:
        return []

    ceiling = pd.Timestamp(after_date)
    rows = []
    for stamp, bar in frame[frame.index > ceiling].iterrows():
        close = bar.get("Close")
        if close is None or pd.isna(close):
            continue
        rows.append({
            "session_date": pd.Timestamp(stamp),
            "Open": bar.get("Open"), "High": bar.get("High"),
            "Low": bar.get("Low"), "Close": close,
            # No dividend adjustment is available for this source, so the
            # adjusted column must not claim one. The tail is raw either way.
            "Adj Close": close,
            "Volume": bar.get("Volume"),
            # The measured store's close is the exchange's official close --
            # the auction price -- which is precisely the field a Rubix bar is
            # missing when its auction never arrived. One exception: a session
            # the store rebuilt from minute bars that stopped before the cross
            # carries CloseConfirmed 0, and it is a last price rather than a
            # close. Sessions from the export predate that column and are
            # confirmed, which is what the default preserves.
            "_official_close": _confirmed(bar),
        })
    return rows
