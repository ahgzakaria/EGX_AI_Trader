r"""Freeze MubasherTrade PRO's Export History CSVs as the backtest's daily record.

    venv\Scripts\python.exe scripts\freeze_mubasher_export.py --source E:\EGX_Symbols

In order, and what each step refuses:

1. Every ``*.csv`` in ``--source`` must carry the terminal's exact header,
   parse completely, have no duplicate session, and name its own ticker in the
   ``Symbol`` column. One bad file stops the freeze.
2. Every file is checked against the terminal's ``history.db``: close, high,
   low, volume and turnover must be identical on every shared session. A single
   difference stops the freeze -- a copy that disagrees with its own source is
   not a record of it.
3. Files are copied byte for byte into ``export/`` and hashed.
4. Active symbols the export does not contain, but ``history.db`` carries
   under another ticker by ISIN, are written to ``history_db/`` -- only when
   ``history.db`` ends on the export's own last session, so both halves are
   one record of one day.
5. ``_manifest.json`` is written last, and the store is built beside the
   destination and moved into place only when complete.

It refuses to run over an existing store unless ``--replace`` is given: a
frozen record that can be re-frozen by accident is the defect this replaces.
No network call is made.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np                                                  # noqa: E402
import pandas as pd                                                 # noqa: E402

from core import frozen_mubasher_store as store                     # noqa: E402

DEFAULT_DEST = PROJECT_ROOT / store.FROZEN_DIR

#: Relative tolerance per checked column. Prices and volume are stored exactly
#: in both; turnover is a float the exporter prints with fewer digits.
TOLERANCE = {"Close": 1e-9, "High": 1e-9, "Low": 1e-9, "Volume": 1e-9, "Turnover": 1e-6}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, type=Path,
                        help="folder holding the terminal's exported CSVs")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST,
                        help="where the frozen store goes")
    parser.add_argument("--replace", action="store_true",
                        help="replace an existing frozen store")
    return parser.parse_args(argv)


def read_checked(path: Path) -> pd.DataFrame:
    """The export as a frame, or ``ValueError`` naming what is wrong with it."""
    with open(path, encoding="utf-8-sig") as handle:
        header = handle.readline().strip()
    if header != store.EXPORT_HEADER:
        raise ValueError(f"{path.name}: unexpected header {header!r}")
    tickers = set(pd.read_csv(path, encoding="utf-8-sig", usecols=["Symbol"])["Symbol"]
                  .astype(str).str.strip().str.upper())
    if tickers != {path.stem.upper()}:
        raise ValueError(f"{path.name}: Symbol column holds {sorted(tickers)[:3]}")
    frame = store.read_export(path)
    if frame.index.isna().any():
        raise ValueError(f"{path.name}: {int(frame.index.isna().sum())} unparseable dates")
    if frame.index.duplicated().any():
        raise ValueError(f"{path.name}: {int(frame.index.duplicated().sum())} repeated sessions")
    unparsed = int(frame.isna().sum().sum())
    if unparsed:
        raise ValueError(f"{path.name}: {unparsed} unparseable values")
    return frame


def history_frame(connection, table: str) -> pd.DataFrame:
    rows = pd.read_sql(f'SELECT DATE, HIG, LOW, CLS, VOL, TOVR FROM "{table}"', connection)
    frame = pd.DataFrame({
        "High": pd.to_numeric(rows["HIG"], errors="coerce").to_numpy(),
        "Low": pd.to_numeric(rows["LOW"], errors="coerce").to_numpy(),
        "Close": pd.to_numeric(rows["CLS"], errors="coerce").to_numpy(),
        "Volume": pd.to_numeric(rows["VOL"], errors="coerce").to_numpy(),
        "Turnover": pd.to_numeric(rows["TOVR"], errors="coerce").to_numpy(),
    }, index=pd.DatetimeIndex(pd.to_datetime(rows["DATE"].astype(str), format="%Y%m%d",
                                             errors="coerce"), name="Date"))
    frame = frame[frame.index.notna()]
    return frame[~frame.index.duplicated(keep="last")].sort_index()


def verify_against_history(export: pd.DataFrame, history: pd.DataFrame):
    """(shared sessions, {column: sessions that differ}) -- empty dict means identical."""
    shared = export.index.intersection(history.index)
    differing = {}
    for column, tolerance in TOLERANCE.items():
        same = np.isclose(export.loc[shared, column].to_numpy(float),
                          history.loc[shared, column].to_numpy(float),
                          rtol=tolerance, atol=1e-9)
        if not same.all():
            differing[column] = int((~same).sum())
    return len(shared), differing


def write_history_db_copy(history: pd.DataFrame, path: Path) -> None:
    """Write a history.db frame in ``store.HISTORY_DB_HEADER`` layout.

    Every value is written as a plain Python float. The first version wrote
    ``repr`` of the NumPy scalar, which under NumPy 2 is ``np.float64(12.5)``:
    every number read back as NaN, and the three symbols frozen that way served
    no bars at all.
    """
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(store.HISTORY_DB_HEADER + "\n")
        for date, row in history.iterrows():
            values = ",".join(repr(float(row[column]))
                              for column in ("High", "Low", "Close", "Volume", "Turnover"))
            handle.write(f"{date.date()},{values}\n")


def round_trips(history: pd.DataFrame, path: Path) -> bool:
    """Whether the written file reads back to exactly the frame it was written from."""
    back = store.read_history_db_copy(path)
    columns = ["High", "Low", "Close", "Volume", "Turnover"]
    return (len(back) == len(history)
            and back.index.equals(history.index)
            and np.array_equal(back[columns].to_numpy(float), history[columns].to_numpy(float)))


def _span(frame):
    return (str(frame.index.min().date()), str(frame.index.max().date())) if len(frame) else (None, None)


def main(argv=None) -> int:
    args = parse_args(argv)
    source, dest = Path(args.source), Path(args.dest)
    if (dest / store.MANIFEST_NAME).exists() and not args.replace:
        print(f"refused: a frozen store already exists at {dest}. "
              f"Pass --replace only if re-freezing is deliberate.")
        return 2
    files = sorted(source.glob("*.csv"))
    if not files:
        print(f"refused: no CSV files in {source}")
        return 2

    exports = {}
    for path in files:
        try:
            exports[path.stem.upper()] = (path, read_checked(path))
        except (ValueError, KeyError) as error:
            print(f"refused: {error}")
            return 2
    print(f"{len(exports)} export files read and checked")

    from core.universe import load_universe
    from scripts.research.mubasher_vs_eodhd import mubasher_isin_map
    from sector_flow.mubasher_local import HISTORY_RELATIVE, find_root

    terminal = find_root()
    if terminal is None or not (terminal / HISTORY_RELATIVE).is_file():
        print("refused: MubasherTrade PRO's history.db is not on this machine, so the "
              "export cannot be verified against its source")
        return 2
    history_path = terminal / HISTORY_RELATIVE
    connection = sqlite3.connect(f"file:{history_path.as_posix()}?mode=ro&immutable=1",
                                 uri=True)
    tables = {name[1:].upper(): name for (name,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'") if name.startswith("_")}

    verified, history_last = {}, Counter()
    for ticker, (path, frame) in exports.items():
        if ticker not in tables:
            print(f"refused: {path.name} has no history.db table to verify against")
            return 2
        history = history_frame(connection, tables[ticker])
        shared, differing = verify_against_history(frame, history)
        if differing or not shared:
            print(f"refused: {path.name} differs from history.db on {differing or 'every session'}"
                  f" ({shared} shared sessions)")
            return 2
        verified[ticker] = shared
        history_last[str(history.index.max().date())] += 1
    export_last = Counter(_span(frame)[1] for _, frame in exports.values()).most_common(1)[0][0]
    history_modal = history_last.most_common(1)[0][0]
    print(f"all {len(verified)} files identical to history.db on "
          f"{sum(verified.values()):,} shared sessions; export ends {export_last}, "
          f"history.db ends {history_modal}")

    building = dest.with_name(dest.name + ".partial")
    if building.exists():
        shutil.rmtree(building)
    (building / "export").mkdir(parents=True)
    records = {}
    for ticker, (path, frame) in exports.items():
        target = building / "export" / path.name
        shutil.copyfile(path, target)
        first, last = _span(frame)
        records[ticker] = {
            "symbol": ticker, "file": f"export/{path.name}", "format": store.EXPORT_FORMAT,
            "sha256": store.sha256_file(target), "source_sha256": store.sha256_file(path),
            "rows": int(len(frame)), "first_session": first, "last_session": last,
            "source": f"MubasherTrade PRO Export History ({source})",
            "mubasher_ticker": ticker, "verified_sessions_against_history_db": verified[ticker],
        }
        if records[ticker]["sha256"] != records[ticker]["source_sha256"]:
            print(f"refused: {path.name} changed while being copied")
            return 2

    universe = {r.canonical_symbol: r for r in load_universe()}
    active = {s for s, r in universe.items() if r.is_active}
    by_isin = mubasher_isin_map(terminal)
    added, skipped = [], []
    for symbol in sorted(active - set(exports)):
        isin = str(getattr(universe[symbol], "isin", "") or "").strip()
        ticker = by_isin.get(isin)
        if not ticker or ticker not in tables:
            skipped.append((symbol, "no Mubasher table by ISIN"))
            continue
        if history_modal != export_last:
            skipped.append((symbol, f"history.db ends {history_modal}, export {export_last}"))
            continue
        history = history_frame(connection, tables[ticker])
        history = history[history["Close"] > 0]
        (building / "history_db").mkdir(exist_ok=True)
        target = building / "history_db" / f"{symbol}.csv"
        write_history_db_copy(history, target)
        if not round_trips(history, target):
            print(f"refused: history_db/{symbol}.csv does not read back to _{ticker}")
            return 2
        first, last = _span(history)
        records[symbol] = {
            "symbol": symbol, "file": f"history_db/{symbol}.csv",
            "format": store.HISTORY_DB_FORMAT, "sha256": store.sha256_file(target),
            "rows": int(len(history)), "first_session": first, "last_session": last,
            "source": f"MubasherTrade PRO history.db table _{ticker}, resolved by ISIN {isin}",
            "mubasher_ticker": ticker,
        }
        added.append(f"{symbol}<-{ticker}")
    connection.close()

    manifest = {
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "provider": store.PROVIDER,
        "source_directory": str(source),
        "export_last_session": export_last,
        "history_db_last_session": history_modal,
        "network_used": False,
        "open_policy": store.OPEN_POLICY,
        "price_adjustment": store.PRICE_ADJUSTMENT,
        "note": ("Export columns Open (a copy of High) and Prev. Closed (-1) are "
                 "placeholders and are not served. Re-freeze only deliberately."),
        "symbol_count": len(records),
        "symbols": records,
    }
    (building / store.MANIFEST_NAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if dest.exists():
        shutil.rmtree(dest)
    building.rename(dest)

    print(f"frozen {len(records)} symbols into {dest}")
    print(f"  from the export: {len(exports)}")
    print(f"  added from history.db by ISIN: {added or 'none'}")
    if skipped:
        print(f"  active symbols not frozen: {skipped}")
    missing_active = sorted(active - set(records))
    print(f"  active symbols covered: {len(active) - len(missing_active)} of {len(active)}"
          + (f"; missing {missing_active}" if missing_active else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
