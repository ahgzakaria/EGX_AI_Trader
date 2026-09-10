"""Local-seed + Mubasher daily tail for EODHD-unsupported symbols.

Builds current-research daily history for symbols EODHD does not cover, from two
explicitly-labelled parts:

  1. an immutable **frozen Yahoo seed** (local cache, no network) as a historical
     bootstrap, and
  2. completed sessions appended from the measured store, which is filled from
     MubasherTrade PRO's own databases by
     ``scripts/import_mubasher_local.py``.

The tail used to come from the Rubix daily bridge, built out of minute events
this project captured itself during the session. It does not any more, for a
reason the feed cannot fix: **the Rubix price feed never sends an auction
price.** On 2026-09-08 all 220 auction-window rows for COMI carried one
identical price, and only 29.5% of 6,067 bridge bars ever had a confirmed
official close. An EGX daily candle closes on the price the 14:25 auction
crossed at; a bar built from the continuous session is a 14:18 price wearing a
14:30 label, and across 1,791 bars holding both they agree in 23.1% of sessions
with a worst case of 11.24%.

Mubasher's own daily record carries that auction price, is what the terminal's
export was written from, and reaches back to 2003. The cost of the swap is
latency and nothing else: the bridge finalized itself minutes after the close,
while this tail advances when the import is run.

Guarantees:
  * a tail contribution is claimed ONLY when at least one real bar is appended;
  * bars are appended strictly AFTER the seed's last session, sorted, de-duplicated;
  * a bar that CONFLICTS with an existing seed bar is never silently overwritten —
    it is counted and surfaced;
  * both parts keep their provenance;
  * no Yahoo network call is ever made; the seed is frozen.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

FROZEN_YAHOO_SEED = "FROZEN_YAHOO_SEED"

#: The daily tail's one supplier: the measured store, filled from
#: MubasherTrade PRO's ``history.db`` and its live minute store.
#:
#: Sole rather than second. It used to fill only what the Rubix bridge left
#: empty, on the reasoning that a close this project built end to end was more
#: accountable than one it merely read. That reasoning did not survive
#: measurement: the close this project built was the last continuous trade,
#: because the feed never sent an auction, and the one it merely reads is the
#: exchange's own.
MUBASHER_DAILY_TAIL = "MUBASHER_DAILY_TAIL"
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


def append_bridge_bars(frame, symbol, *, not_after=None, tail_rows=None):
    """Append measured Mubasher sessions after ``frame``'s last date.

    Shared by both history paths: the frozen-seed path for EODHD-unsupported
    symbols, and the EODHD path, which needs the same tail because EODHD
    publishes a completed session a day — sometimes two — late, and a history
    that stops before the last completed session is refused as stale by the
    daily guard.

    Guarantees, all of which the callers depend on:

    * **append only.** A date already in ``frame`` is never overwritten. If the
      tail disagrees with an existing bar it is counted as a conflict and
      surfaced; the existing bar wins. The provider that owns a session keeps
      it.
    * **no store, no change.** ``frame`` is returned untouched, so an absent or
      unreadable measured store can never degrade an otherwise good history.
    * **never past ``not_after``.** The tail fills the gap up to the session the
      daily guard expects; it must not run ahead of it. The store holds a
      session as soon as the import is run, while the guard only advances its
      expected session after a settlement grace, so between the two the store
      legitimately holds a session the guard still considers unpublished.
      Appending it made the guard reject 186 of 241 symbols with
      SKIPPED_FUTURE_DAILY_DATE — symbols that had been fine before the tail
      existed. Filling a gap is the job; getting ahead is a regression.

    ``tail_rows`` is injectable because without it a unit test reads the
    production store and appends real market data to its fixture.

    Returns ``(frame, provenance)``. ``frame`` is a copy whenever anything was
    appended, and the original object otherwise.
    """

    base = _base(symbol)
    provenance = {
        "bridge_provider": MUBASHER_DAILY_TAIL,
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

    last = pd.Timestamp(frame.index[-1])
    lookup = _measured_rows_after if tail_rows is None else tail_rows
    rows = sorted(lookup(base, last.date().isoformat()),
                  key=lambda row: row["session_date"])
    # Every appended session comes from this one source now, so the field that
    # used to name the second supplier names all of them.
    provenance["bridge_supplements"] = tuple(
        row["session_date"].date().isoformat() for row in rows)
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


def build_local_daily_history(symbol, *, period="10y", interval="1d",
                              not_after=None):
    """Return (frame, provenance) for an EODHD-unsupported symbol.

    ``frame`` is the canonical OHLCV contract (DatetimeIndex named 'Date'); it is
    ``None`` when no seed exists. ``provenance`` always describes what was found.
    """
    base = _base(symbol)
    prov = {
        "symbol": base, "seed_provider": None, "seed_first_session": None,
        "seed_latest_session": None, "seed_rows": 0,
        "bridge_provider": MUBASHER_DAILY_TAIL, "bridge_available_sessions": 0,
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

    frame, bridge = append_bridge_bars(frame, base, not_after=not_after)
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
        "provider": "local_plus_mubasher", "seed_provider": FROZEN_YAHOO_SEED,
        "bridge_provider": MUBASHER_DAILY_TAIL,
        "generated_at": datetime.now(timezone.utc).isoformat(), **prov,
    }
    return frame, prov


def _confirmed(bar):
    """True unless the measured store says this close is not the auction."""
    flag = bar.get("CloseConfirmed")
    if flag is None or pd.isna(flag):
        return True
    return bool(int(flag))


def _measured_rows_after(base, after_date):
    """Measured-store bars strictly after ``after_date``, in the tail's shape.

    Split-adjusted and not dividend-adjusted, which is why the series name says
    RAW_TAIL: a corporate action inside the appended window would break it, and
    `bridge_tail_blocked` is what guards that.
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
            # the auction price. One exception: a session the store rebuilt
            # from minute bars that stopped before the cross carries
            # CloseConfirmed 0, and that is a last price rather than a close.
            # Rows imported before that column existed are confirmed, which is
            # what the default preserves.
            "_official_close": _confirmed(bar),
        })
    return rows
