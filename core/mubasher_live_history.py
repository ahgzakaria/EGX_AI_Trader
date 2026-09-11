"""The live daily history, read from MubasherTrade PRO's own record.

The live scanner reads EODHD's split-adjusted history for 217 of the 230 active
symbols, with Mubasher's measured sessions appended after EODHD's last date.
Measured against a third source, Mubasher's record is the better-supported of
the two (docs/audits/providers/MUBASHER_VS_EODHD_DAILY_HISTORY.md), and it is
the one without a subscription. This module serves the whole history from it,
in the frame the router already serves, so the router can be pointed at it with
one setting (``live_history_source``) once a shadow period has shown the two
agreeing in practice.

It reads ``data/measured_turnover.db``, which ``scripts/import_mubasher_local.py``
rebuilds from ``history.db`` and the live minute store on every run. A rebuild
re-reads the terminal's back-adjusted history whole, so a split applied since
the last run reaches every past bar and not only the new ones -- which is why
nothing here appends to a stored series.

What it states rather than hides:

* ``Open`` is the previous session's close. The record's daily rows carry no
  traded open (the terminal's ``OP`` is the previous close); the minute-built
  sessions do, but one convention for every bar is the honest one, and the
  rules the scanner runs do not read the open.
* A session rebuilt from minute bars that stopped before the closing auction has
  a last price, not an official close. It is kept -- dropping it would leave the
  series short of the session just closed -- and counted in the provenance.
* Nothing past ``not_after`` is served. The daily guard expects a session only
  after its settlement grace, and running ahead of it is a regression the
  EODHD tail path already paid for once.
"""

from __future__ import annotations

import pandas as pd

LIVE_SOURCE = "mubasher"
PROVIDER = "mubasher_live"
SERIES = "MUBASHER_SPLIT_ADJUSTED"
PRICE_POLICY = "MUBASHER_SPLIT_ADJUSTED_NOT_DIVIDEND_ADJUSTED"
OPEN_POLICY = "PREVIOUS_CLOSE_NOT_A_TRADED_OPEN"

READY = "MUBASHER_LIVE_READY"
STALE = "MUBASHER_LIVE_STALE"
INSUFFICIENT = "DATA_INSUFFICIENT"
UNAVAILABLE = "DATA_UNAVAILABLE"

CONTRACT_COLUMNS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]

#: How many of the most recent unconfirmed-close dates the provenance lists.
#: The count covers the whole series; the dates are for reading, not auditing.
RECENT_UNCONFIRMED = 20


def _base(symbol) -> str:
    return str(symbol).strip().upper().split(".")[0]


def mubasher_live_history(symbol, *, min_bars=250, not_after=None, database=None):
    """``(frame, status, provenance)`` for one symbol from the measured store.

    ``frame`` is ``None`` when the store holds nothing for the symbol. ``status``
    is ``READY``, ``STALE`` (the store ends before ``not_after``),
    ``INSUFFICIENT`` (fewer than ``min_bars`` sessions) or ``UNAVAILABLE``. The
    caller decides what each means; this only reports it.
    """
    from sector_flow import measured_turnover

    base = _base(symbol)
    database = database or measured_turnover.DEFAULT_DATABASE
    provenance = {
        "provider": PROVIDER, "effective_provider": PROVIDER, "symbol": base,
        "store": str(database), "open_policy": OPEN_POLICY,
        "price_adjustment": PRICE_POLICY,
    }
    raw = measured_turnover.frame_for(f"{base}.CA", database=database)
    if raw is None or raw.empty:
        provenance["status"] = UNAVAILABLE
        return None, UNAVAILABLE, provenance

    raw = raw[pd.to_numeric(raw["Close"], errors="coerce") > 0]
    raw = raw[~raw.index.duplicated(keep="last")].sort_index()
    withheld = 0
    if not_after is not None:
        ceiling = pd.Timestamp(not_after)
        withheld = int((raw.index > ceiling).sum())
        raw = raw[raw.index <= ceiling]
    if raw.empty:
        provenance.update(status=UNAVAILABLE, sessions_withheld_ahead=withheld)
        return None, UNAVAILABLE, provenance

    close = pd.to_numeric(raw["Close"], errors="coerce")
    frame = pd.DataFrame({
        "Open": close.shift(1).to_numpy(),
        "High": pd.to_numeric(raw["High"], errors="coerce").to_numpy(),
        "Low": pd.to_numeric(raw["Low"], errors="coerce").to_numpy(),
        "Close": close.to_numpy(),
        "Adj Close": close.to_numpy(),
        "Volume": pd.to_numeric(raw["Volume"], errors="coerce").to_numpy(),
    }, index=pd.DatetimeIndex(raw.index, name="Date"))

    flags = (pd.to_numeric(raw["CloseConfirmed"], errors="coerce")
             if "CloseConfirmed" in raw.columns else pd.Series(1, index=raw.index))
    # A row imported before the flag existed came from history.db, whose close
    # is the auction's, so a missing flag reads as confirmed.
    flags = flags.fillna(1).astype(int)
    unconfirmed = [pd.Timestamp(day) for day, flag in flags.items() if flag == 0]
    effective = frame.index[-1].date()
    provenance.update(
        rows=int(len(frame)),
        first_session=frame.index[0].date().isoformat(),
        effective_latest_session=effective.isoformat(),
        sessions_withheld_ahead=withheld,
        unconfirmed_close_count=len(unconfirmed),
        recent_unconfirmed_close_dates=tuple(
            day.date().isoformat() for day in unconfirmed[-RECENT_UNCONFIRMED:]),
        last_close_confirmed=bool(flags.iloc[-1]),
    )

    if len(frame) < max(1, int(min_bars)):
        status = INSUFFICIENT
    elif not_after is not None and effective < pd.Timestamp(not_after).date():
        status = STALE
    else:
        status = READY
    provenance["status"] = status
    frame.attrs["market_data"] = dict(provenance)
    return frame, status, provenance
