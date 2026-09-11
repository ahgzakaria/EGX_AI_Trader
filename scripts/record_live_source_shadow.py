r"""Record what the live rules see on EODHD and on Mubasher, session by session.

The live scanner reads EODHD for 217 of the 230 active symbols. Before it can be
switched to MubasherTrade PRO's record (``live_history_source``), the two have to
be seen agreeing in practice -- on sessions as they arrive, with the download
done or not -- and not only in a historical comparison. This runs the live
rules on both and records where they agree and where they do not.

    venv\Scripts\python.exe scripts\record_live_source_shadow.py
    venv\Scripts\python.exe scripts\record_live_source_shadow.py --backfill 10

Both histories come from ``core.research_router.get_current_research_history``
with the live source scoped to each record, so the comparison exercises exactly
the path the switch would put into use, cleaning and freshness gates included.

The rules are the ones the pages show: CONFIRMED_VOLUME_BREAKOUT firing
(``strategy_momentum_breakout.scan``), the pre-breakout list at its default reach
(``strategy_momentum_breakout.watch``), and Swing Breakout over its liquid
universe (``services.swing_breakout``). A rule is judged only on symbols with a
bar on the session in that record, so a record that is behind shows up as a
missing name rather than as yesterday's signal.

``--backfill N`` also records the N sessions before the latest by cutting both
histories at each date. Those rows are labelled ``backfill``: they show whether
the rules agree on the data, not whether the data would have been there on
time -- EODHD's publishing lag and a missed download are visible only going
forward.

Re-running a session replaces its rows. It writes only
``data/research/live_source_shadow.db``, and places no order.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd                                                  # noqa: E402

DATABASE = PROJECT_ROOT / "data" / "research" / "live_source_shadow.db"
SOURCES = ("eodhd", "mubasher")
RULES = ("confirmed_breakout", "breakout_watch", "swing_breakout")
#: Shared sessions over which a symbol's closes are compared on each run.
AGREEMENT_SESSIONS = 20

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    session_date TEXT NOT NULL,
    mode TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    symbols INTEGER NOT NULL,
    eodhd_readable INTEGER NOT NULL,
    mubasher_readable INTEGER NOT NULL,
    eodhd_on_session INTEGER NOT NULL,
    mubasher_on_session INTEGER NOT NULL,
    PRIMARY KEY (session_date, mode)
);
CREATE TABLE IF NOT EXISTS symbols (
    session_date TEXT NOT NULL,
    mode TEXT NOT NULL,
    symbol TEXT NOT NULL,
    eodhd_last TEXT,
    mubasher_last TEXT,
    eodhd_close REAL,
    mubasher_close REAL,
    close_diff_percent REAL,
    closes_within_1pct REAL,
    mubasher_close_confirmed INTEGER,
    eodhd_error TEXT,
    mubasher_error TEXT,
    PRIMARY KEY (session_date, mode, symbol)
);
CREATE TABLE IF NOT EXISTS signals (
    session_date TEXT NOT NULL,
    mode TEXT NOT NULL,
    rule TEXT NOT NULL,
    symbol TEXT NOT NULL,
    on_eodhd INTEGER NOT NULL,
    on_mubasher INTEGER NOT NULL,
    PRIMARY KEY (session_date, mode, rule, symbol)
);
"""


def load_histories(source, symbols=None):
    """``(histories, errors)`` for one record, through the live router."""
    from core.research_router import live_source
    from strategy_momentum_breakout.scan import _histories

    errors = {}
    with live_source(source):
        histories = _histories(symbols, on_error=lambda s, r: errors.setdefault(s, r))
    return histories, errors


def cut(histories, session):
    """Each history up to ``session``, keeping only symbols with a bar on it."""
    ceiling = pd.Timestamp(session)
    out = {}
    for symbol, frame in histories.items():
        part = frame[frame.index <= ceiling]
        if len(part) and pd.Timestamp(part.index[-1]) == ceiling:
            part = part.copy()
            part.attrs = dict(frame.attrs)
            out[symbol] = part
    return out


def rule_symbols(histories):
    """``{rule: set of symbols}`` the live rules name on these histories' last bar."""
    from services import swing_breakout
    from strategy_momentum_breakout.scan import scan
    from strategy_momentum_breakout.watch import watch

    if not histories:
        return {rule: set() for rule in RULES}
    return {
        "confirmed_breakout": {s.symbol for s in scan(histories=histories).signals},
        "breakout_watch": {c.symbol for c in watch(histories=histories).candidates},
        "swing_breakout": {c.symbol for c in swing_breakout.scan(
            swing_breakout.most_traded(histories)).candidates},
    }


def _last(frame):
    return None if frame is None or not len(frame) else str(pd.Timestamp(frame.index[-1]).date())


def _close_on(frame, day):
    if frame is None or day not in frame.index:
        return None
    return float(frame.at[day, "Close"])


def _confirmed_on(frame, day):
    """1, 0, or None when the frame holds no bar for the day."""
    if frame is None or day not in frame.index:
        return None
    dates = (frame.attrs.get("market_data") or {}).get("recent_unconfirmed_close_dates") or ()
    return 0 if day.date().isoformat() in dates else 1


def compare_symbols(eodhd, mubasher, errors, session, symbols):
    """One row per symbol: what each record holds for the session, and how close."""
    day = pd.Timestamp(session)
    rows = []
    for symbol in symbols:
        e, m = eodhd.get(symbol), mubasher.get(symbol)
        row = {
            "symbol": symbol, "eodhd_last": _last(e), "mubasher_last": _last(m),
            "eodhd_close": _close_on(e, day), "mubasher_close": _close_on(m, day),
            "close_diff_percent": None, "closes_within_1pct": None,
            "mubasher_close_confirmed": _confirmed_on(m, day),
            "eodhd_error": errors.get("eodhd", {}).get(symbol),
            "mubasher_error": errors.get("mubasher", {}).get(symbol),
        }
        if row["eodhd_close"] and row["mubasher_close"]:
            row["close_diff_percent"] = round(
                (row["mubasher_close"] / row["eodhd_close"] - 1) * 100, 4)
        if e is not None and m is not None:
            shared = e.index.intersection(m.index)
            shared = shared[shared <= day][-AGREEMENT_SESSIONS:]
            if len(shared):
                within = ((m.loc[shared, "Close"] / e.loc[shared, "Close"] - 1).abs()
                          <= 0.01).mean() * 100
                row["closes_within_1pct"] = round(float(within), 1)
        rows.append(row)
    return rows


def record(connection, session, mode, symbols, readable, eodhd, mubasher, errors,
           rules=None):
    """Write one session's comparison, replacing any earlier rows for it.

    ``rules`` is injectable so a test can pin the bookkeeping without running
    the strategies on synthetic data.
    """
    rules = rules or rule_symbols
    on_e, on_m = rules(eodhd), rules(mubasher)
    rows = compare_symbols(eodhd, mubasher, errors, session, symbols)
    day = str(pd.Timestamp(session).date())
    summary = {}
    with connection:
        for table in ("runs", "symbols", "signals"):
            connection.execute(f"DELETE FROM {table} WHERE session_date=? AND mode=?",
                               (day, mode))
        connection.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (day, mode, datetime.now(timezone.utc).isoformat(), len(symbols),
             readable["eodhd"], readable["mubasher"], len(eodhd), len(mubasher)))
        connection.executemany(
            "INSERT INTO symbols VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(day, mode, r["symbol"], r["eodhd_last"], r["mubasher_last"],
              r["eodhd_close"], r["mubasher_close"], r["close_diff_percent"],
              r["closes_within_1pct"], r["mubasher_close_confirmed"],
              r["eodhd_error"], r["mubasher_error"]) for r in rows])
        for rule in RULES:
            union = sorted(on_e.get(rule, set()) | on_m.get(rule, set()))
            connection.executemany(
                "INSERT INTO signals VALUES (?, ?, ?, ?, ?, ?)",
                [(day, mode, rule, symbol, int(symbol in on_e.get(rule, set())),
                  int(symbol in on_m.get(rule, set()))) for symbol in union])
            both = len(on_e.get(rule, set()) & on_m.get(rule, set()))
            summary[rule] = (both, len(on_e.get(rule, set())) - both,
                             len(on_m.get(rule, set())) - both)
    return summary


def sessions_to_record(expected, histories, backfill):
    """The expected session first, then up to ``backfill`` sessions before it."""
    counts = Counter()
    for frame in histories.values():
        counts.update(pd.DatetimeIndex(frame.index).normalize())
    floor = max(1, len(histories) // 2)
    traded = sorted((d for d, n in counts.items() if n >= floor), reverse=True)
    if expected is not None:
        ceiling = pd.Timestamp(expected)
        traded = [d for d in traded if d <= ceiling]
        head = ceiling
    else:
        head = traded[0] if traded else None
    if head is None:
        return []
    earlier = [d for d in traded if d < head][:max(0, int(backfill))]
    return [head] + earlier


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backfill", type=int, default=0,
                        help="also record this many earlier sessions, labelled backfill")
    parser.add_argument("--limit", type=int, default=None,
                        help="only the first N active symbols, for a quick check")
    parser.add_argument("--database", type=Path, default=DATABASE)
    args = parser.parse_args(argv)

    from core.environment import load_project_environment

    load_project_environment()
    from core.research_router import _expected_completed_session
    from core.universe import active_symbols

    symbols = sorted(active_symbols())
    if args.limit:
        symbols = symbols[:args.limit]
    expected = _expected_completed_session()

    histories, errors = {}, {}
    for source in SOURCES:
        histories[source], errors[source] = load_histories(source, symbols)
        print(f"{source:<9} readable {len(histories[source])} of {len(symbols)}"
              + (f"; refused {len(errors[source])}, e.g. "
                 f"{next(iter(errors[source].items()))}" if errors[source] else ""))
    readable = {source: len(histories[source]) for source in SOURCES}

    sessions = sessions_to_record(expected, histories["mubasher"] or histories["eodhd"],
                                  args.backfill)
    if not sessions:
        print("no session to record")
        return 1

    args.database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(args.database)
    connection.executescript(SCHEMA)
    for index, session in enumerate(sessions):
        mode = "daily" if index == 0 else "backfill"
        eodhd = cut(histories["eodhd"], session)
        mubasher = cut(histories["mubasher"], session)
        summary = record(connection, session, mode, symbols, readable, eodhd, mubasher, errors)
        parts = "  ".join(f"{rule} both {b} / EODHD only {e} / Mubasher only {m}"
                          for rule, (b, e, m) in summary.items())
        print(f"{session.date()} [{mode:<8}] on session: EODHD {len(eodhd)}, "
              f"Mubasher {len(mubasher)} | {parts}")
    connection.close()
    print(f"wrote {args.database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
