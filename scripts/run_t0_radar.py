r"""Build tomorrow's T+0 radar from the session that just closed, and grade the earlier ones.

    venv\Scripts\python.exe scripts\run_t0_radar.py
    venv\Scripts\python.exe scripts\run_t0_radar.py --show 15

Run it after the close, once the MubasherTrade PRO download has been imported --
``run_daily_update.py`` calls it as one of its steps. It reads the record, writes
the list to ``reports/t0_radar/``, adds it to ``data/t0_radar_forward.db``, and
grades every earlier list whose next session has now closed.

Re-running it on the same session writes a fresh CSV and nothing else: the
forward record keeps the first list it was given for a session. It places no
order and changes no rule.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

STATUS_DIRECTORY = PROJECT_ROOT / "data" / "automation_status"


def run(database=None):
    from core.environment import load_project_environment

    load_project_environment()

    from t0_radar import service

    return service.run(database=database)


def show(result, count):
    """The list as a reader wants it in a terminal: ranked, with its reasons."""

    from t0_radar import radar

    frame = radar.as_frame(result).head(count)
    print(f"\nT+0 radar for the session after {result.session_date}"
          f"  ({len(result.candidates)} candidates)")
    if not result.provenance.get("t0_enforced"):
        print("  (T+0 eligibility is not filtered: check it before a same-session trade)")
    for _, row in frame.iterrows():
        print(f"\n{int(row['rank']):>2}. {row['symbol']:<6} forecast {row['forecast_pct']:4.1f}% "
              f"({row['forecast_low_pct']:.1f}-{row['forecast_high_pct']:.1f})  "
              f"close {row['close']:,.2f}  {row.get('name') or ''}")
        for sentence in radar.split_sentences(row.get("reasons")):
            print(f"      + {sentence}")
        for sentence in radar.split_sentences(row.get("cautions")):
            print(f"      ! {sentence}")
        print(f"      = {row.get('context')}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", default=None)
    parser.add_argument("--show", type=int, default=0,
                        help="also print the first N names with their reasons")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    result = None
    try:
        result, summary = run(database=args.database)
    except Exception as error:                              # noqa: BLE001 - recorded
        summary = {"status": "FAILED", "error": f"{type(error).__name__}: {error}"}

    STATUS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).astimezone().date().isoformat()
    status_path = STATUS_DIRECTORY / f"t0_radar_{day}.json"
    status_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    print(f"\nstatus written to {status_path}")
    if result is not None and args.show:
        show(result, args.show)
    return 0 if summary["status"] in {"OK", "NO_SESSION"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
