r"""What switching sector flow to measured turnover actually changed.

    venv\Scripts\python.exe scripts\research\compare_measured_turnover.py \
        --before data\sector_flow.before_measured.db

Two histories built the same way apart from where turnover came from: an
estimate of ``(High + Low + Close) / 3 x Volume``, or the figure the exchange
reported. Reports only; it writes no store and changes no strategy.

The point is not that the estimate was imprecise. It is within 0.37% at the
median across 746,250 sessions. The point is which sessions it was wrong on,
and by how much, because a sector's share is a ratio and one badly measured
symbol moves every other sector in the same session.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import pandas as pd  # noqa: E402

from sector_flow import HISTORY_TABLE  # noqa: E402


def read(database):
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        frame = pd.read_sql(
            f"SELECT SessionDate, Sector, Turnover, TurnoverShare, RVOL "
            f"FROM {HISTORY_TABLE}", connection)
    frame["SessionDate"] = frame["SessionDate"].astype(str).str[:10]
    return frame


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", default="data/sector_flow.before_measured.db")
    parser.add_argument("--after", default="data/sector_flow.db")
    parser.add_argument("--since", default="2020-01-01")
    parser.add_argument("--top", type=int, default=8)
    args = parser.parse_args(argv)

    before, after = read(args.before), read(args.after)
    joined = before.merge(after, on=["SessionDate", "Sector"],
                          suffixes=("_before", "_after"))
    joined = joined[joined["SessionDate"] >= args.since]
    if joined.empty:
        print("No overlapping sessions to compare.")
        return 1

    joined["share_shift"] = (joined["TurnoverShare_after"]
                             - joined["TurnoverShare_before"]).abs() * 100

    per_session = joined.groupby("SessionDate")["share_shift"].max()
    print(f"sessions compared          {len(per_session):,}")
    print(f"  median largest shift     {per_session.median():7.3f} pp")
    print(f"  p95                      {per_session.quantile(0.95):7.3f} pp")
    print(f"  p99                      {per_session.quantile(0.99):7.3f} pp")
    print(f"  worst                    {per_session.max():7.3f} pp")
    print(f"  sessions moving > 1 pp   {int((per_session > 1).sum()):,} "
          f"({(per_session > 1).mean() * 100:.1f}%)")
    print(f"  sessions moving > 10 pp  {int((per_session > 10).sum()):,}")
    print()

    # A ranking that changed is worth more than a share that moved: the page
    # ranks sectors, and a reader acts on the order rather than the decimals.
    def top_sector(frame, column):
        ordered = frame.sort_values(column, ascending=False)
        return ordered.groupby("SessionDate").head(1).set_index("SessionDate")["Sector"]

    lead_before = top_sector(joined, "TurnoverShare_before")
    lead_after = top_sector(joined, "TurnoverShare_after")
    changed = lead_before[lead_before != lead_after.reindex(lead_before.index)]
    print(f"sessions whose top sector changed  {len(changed):,} "
          f"({len(changed) / len(per_session) * 100:.1f}%)")
    print()

    print(f"largest {args.top} moves:")
    worst = joined.nlargest(args.top, "share_shift")
    for _, row in worst.iterrows():
        print(f"  {row['SessionDate']}  {row['Sector'][:38]:38s} "
              f"{row['TurnoverShare_before'] * 100:6.2f}% -> "
              f"{row['TurnoverShare_after'] * 100:6.2f}%  "
              f"({row['share_shift']:+6.2f} pp)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
