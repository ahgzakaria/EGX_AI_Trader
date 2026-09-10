r"""Refresh the measured store from MubasherTrade PRO's own databases.

    venv\Scripts\python.exe scripts\import_mubasher_local.py
    venv\Scripts\python.exe scripts\import_mubasher_local.py --dry-run

This replaces the manual export. The old path was: open the terminal, open
Export History, type 240 symbols into a one-line search box, save 240 CSV files,
then run ``import_measured_turnover``. The store therefore froze on the day that
was last done, which is why it sat on 2026-09-07 while sector flow ran on an
estimate for every session after it.

The terminal already holds the same data locally and keeps it current itself.
This reads it: the full daily history from ``history.db`` -- identical to the
export it was written from, 714,443 of 714,443 sessions -- and the sessions
after it rebuilt from the live minute store, which is the only source on this
machine that carries today.

It writes ``data/measured_turnover.db`` and nothing else. Rebuild sector flow
afterwards with ``scripts\refresh_sector_flow.py`` if you want the page to move.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from sector_flow.measured_turnover import DEFAULT_DATABASE  # noqa: E402
from sector_flow import mubasher_local  # noqa: E402


def _describe(base):
    """Print what the terminal is holding, before anything is written."""

    history = base / mubasher_local.HISTORY_RELATIVE
    intraday = base / mubasher_local.INTRADAY_RELATIVE
    print(f"  root      {base}")
    for label, path in (("history ", history), ("intraday", intraday)):
        if not path.exists():
            print(f"  {label}  MISSING  {path}")
            continue
        import datetime as dt
        stamp = dt.datetime.fromtimestamp(path.stat().st_mtime)
        print(f"  {label}  {path.stat().st_size / 1e6:8.1f} MB   "
              f"last written {stamp:%Y-%m-%d %H:%M}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=None,
                        help="UserData\\<account> folder; found automatically by default")
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--symbols", default=None,
                        help="comma-separated; default is the universe plus "
                             "whatever the store already holds")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be read and write nothing")
    args = parser.parse_args(argv)

    base = mubasher_local.find_root(args.root)
    if base is None:
        print("MubasherTrade PRO data not found. Pass --root with the "
              "UserData\\<account> folder.", file=sys.stderr)
        return 2

    print("=== Mubasher local import ===")
    _describe(base)
    symbols = ([s.strip() for s in args.symbols.split(",") if s.strip()]
               if args.symbols else None)

    if args.dry_run:
        scope = ({s.split(".")[0].upper() for s in symbols} if symbols
                 else mubasher_local.default_scope(args.database))
        history = mubasher_local.read_history(base, symbols=scope)
        intraday = mubasher_local.read_intraday(base, symbols=scope)
        print(f"\n  scope             {len(scope)} symbols")
        if history.empty:
            print("  history.db        no rows")
        else:
            print(f"  history.db        {len(history):,} rows, "
                  f"{history['ticker'].nunique()} symbols, "
                  f"{history['session_date'].min()} .. {history['session_date'].max()}")
        if intraday.empty:
            print("  intraday          no rows")
        else:
            known = set(zip(history["ticker"], history["session_date"])) if not history.empty else set()
            fresh = intraday[[pair not in known for pair
                              in zip(intraday["ticker"], intraday["session_date"])]]
            print(f"  intraday          {len(intraday):,} rows, "
                  f"{intraday['session_date'].min()} .. {intraday['session_date'].max()}")
            print(f"  would append      {len(fresh):,} rows on "
                  f"{sorted(set(fresh['session_date']))}")
            if len(fresh):
                print(f"  of those, closes not confirmed by an auction: "
                      f"{int((fresh['close_confirmed'] == 0).sum()):,}")
        print("\n--dry-run: nothing written.")
        return 0

    metadata = mubasher_local.import_local(base, database=args.database,
                                           symbols=symbols)
    print()
    print(json.dumps(metadata, indent=2, sort_keys=True))
    print()
    print(f"Wrote {metadata['rows']:,} rows for {metadata['symbols']} symbols "
          f"to {args.database}")
    print(f"  history covers to {metadata['history_last_session']}, "
          f"minute store added {metadata['intraday_rows_appended']:,} rows on "
          f"{metadata['intraday_sessions_appended']}")
    if metadata["unconfirmed_close_rows"]:
        print(f"  {metadata['unconfirmed_close_rows']:,} of those rows close on a "
              f"last trade rather than an auction price (close_confirmed = 0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
