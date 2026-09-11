r"""Read the live-source shadow: do the live rules agree on EODHD and on Mubasher?

    venv\Scripts\python.exe scripts\report_live_source_shadow.py

Reads what ``scripts/record_live_source_shadow.py`` recorded. It prints counts
and agreement, and no verdict: whether they are good enough to switch
``live_history_source`` is the owner's decision, and the numbers are here so it
is made on what happened rather than on what was expected.

Daily and backfill rows are reported apart. Only daily rows say anything about
whether Mubasher's record was there on time.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd                                                  # noqa: E402

from scripts.record_live_source_shadow import DATABASE, RULES        # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", type=Path, default=DATABASE)
    args = parser.parse_args(argv)
    if not args.database.exists():
        print(f"nothing recorded yet: {args.database} does not exist")
        return 1

    with sqlite3.connect(f"file:{args.database.as_posix()}?mode=ro", uri=True) as connection:
        runs = pd.read_sql("SELECT * FROM runs ORDER BY session_date", connection)
        symbols = pd.read_sql("SELECT * FROM symbols", connection)
        signals = pd.read_sql("SELECT * FROM signals", connection)

    for mode in ("daily", "backfill"):
        part = runs[runs["mode"] == mode]
        if part.empty:
            print(f"\n{mode.upper()}: none recorded")
            continue
        print(f"\n{mode.upper()}: {len(part)} sessions, "
              f"{part['session_date'].min()} .. {part['session_date'].max()}")
        print(f"   {'session':<12}{'symbols':>8}{'EODHD on it':>13}{'Mubasher on it':>16}"
              f"{'closes >1% apart':>18}{'unconfirmed':>13}{'median 20-day agreement':>25}")
        for run in part.itertuples():
            rows = symbols[(symbols["session_date"] == run.session_date)
                           & (symbols["mode"] == mode)]
            apart = int((rows["close_diff_percent"].abs() > 1).sum())
            unconfirmed = int((rows["mubasher_close_confirmed"] == 0).sum())
            agreement = rows["closes_within_1pct"].median()
            print(f"   {run.session_date:<12}{run.symbols:>8}{run.eodhd_on_session:>13}"
                  f"{run.mubasher_on_session:>16}{apart:>18}{unconfirmed:>13}"
                  f"{(f'{agreement:.1f}%' if pd.notna(agreement) else 'n/a'):>25}")

        rule_rows = signals[signals["mode"] == mode]
        print(f"   {'rule':<22}{'both':>7}{'EODHD only':>12}{'Mubasher only':>15}{'agreement':>11}")
        for rule in RULES:
            chosen = rule_rows[rule_rows["rule"] == rule]
            both = int(((chosen["on_eodhd"] == 1) & (chosen["on_mubasher"] == 1)).sum())
            e_only = int(((chosen["on_eodhd"] == 1) & (chosen["on_mubasher"] == 0)).sum())
            m_only = int(((chosen["on_eodhd"] == 0) & (chosen["on_mubasher"] == 1)).sum())
            union = both + e_only + m_only
            share = f"{both / union * 100:.1f}%" if union else "n/a"
            print(f"   {rule:<22}{both:>7}{e_only:>12}{m_only:>15}{share:>11}")
        apart = rule_rows[rule_rows["on_eodhd"] != rule_rows["on_mubasher"]]
        if len(apart):
            print("   latest disagreements:")
            for row in apart.sort_values("session_date").tail(10).itertuples():
                side = "EODHD only" if row.on_eodhd else "Mubasher only"
                print(f"     {row.session_date}  {row.rule:<20} {row.symbol:<8} {side}")

    daily = runs[runs["mode"] == "daily"]
    if len(daily):
        behind = daily[daily["mubasher_on_session"] < daily["eodhd_on_session"]]
        print(f"\nDaily sessions where Mubasher held fewer symbols on the session than "
              f"EODHD: {len(behind)} of {len(daily)}"
              + (f" ({', '.join(behind['session_date'])})" if len(behind) else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
