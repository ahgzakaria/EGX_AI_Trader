r"""Everything that has to happen after a session, in one run.

Double-click ``RUN_DAILY.bat`` in the project root. This is what it calls.

The order of the day is now:

  1. open MubasherTrade PRO and download history  (you, by hand)
  2. run this                                     (one click)

Step 1 is yours because the terminal will not export on a schedule. Step 2 is
this, in the order the inputs require:

  1. say what MubasherTrade PRO is actually holding
  2. read its databases into the measured store
  3. rebuild the sector liquidity history from it
  4. check that the daily candle reached the last completed session
  5. record and grade the gap forward test, which used to run on a clock
  6. record the live-source shadow: the live rules on EODHD and on Mubasher
  7. check that the stores which record themselves have not stalled

Step 1 is not decoration. The download is manual, so the likeliest reason a run
produces nothing is that it did not happen -- and an import that reads a file
from three days ago succeeds, writes the same rows, and reports success.

Nothing here is destructive and nothing places an order. A step that fails does
not stop the ones that do not depend on it: a sector history that could not
rebuild is worth knowing about separately from an import that did not happen.

The exit code is 0 only when the last completed session is present in the
store. That is the one thing worth failing on: everything downstream reads it,
and a run that looks like it worked while the candle stayed a day behind is the
failure this replaces. Step 6 is reported rather than failed on, because those
stores are written by other tasks -- but a recorder that stops says nothing on
its own, and the forward tests it feeds take a month to be worth reading.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

RULE = "=" * 72

#: Appended to on every run, and written here rather than by the batch file
#: that calls it. Windows PowerShell 5.1's Tee-Object has no -Encoding switch
#: and writes UTF-16, so the first version of this log came out as "A L L
#: G O O D" one space-separated character at a time.
LOG_PATH = Path("logs/daily_update.log")

_LOG = {"handle": None}


def _open_log():
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LOG["handle"] = LOG_PATH.open("a", encoding="utf-8")
    except OSError:
        _LOG["handle"] = None            # a run that cannot log still runs


def _say(text=""):
    print(text, flush=True)
    handle = _LOG["handle"]
    if handle is not None:
        try:
            print(text, file=handle, flush=True)
        except (OSError, ValueError):
            _LOG["handle"] = None


def _step(number, total, title):
    _say()
    _say(RULE)
    _say(f"  STEP {number}/{total}   {title}")
    _say(RULE)


def _expected_session():
    """The last completed EGX session, or ``None`` when the calendar cannot say."""
    try:
        from core.research_router import _expected_completed_session

        return _expected_completed_session()
    except Exception as error:                          # noqa: BLE001
        _say(f"  ! could not resolve the expected session: {error}")
        return None


# --------------------------------------------------------------------------- #
# 1. is the download actually there?
# --------------------------------------------------------------------------- #

def check_sources(expected):
    """Report what the terminal is holding, before anything is imported.

    The download is a manual act, so the most likely reason a run produces
    nothing is that it did not happen. Saying that plainly is worth more than
    any of the steps below: an import that reads a file from three days ago
    succeeds, writes the same rows, and reports success.
    """

    from sector_flow import mubasher_local as local

    base = local.find_root()
    if base is None:
        _say("  MubasherTrade PRO data not found on this machine.")
        _say("  Nothing can be imported. Is the terminal installed under this user?")
        return None, False

    _say(f"  terminal data   {base}")
    history = base / local.HISTORY_RELATIVE
    intraday = base / local.INTRADAY_RELATIVE

    history_date = intraday_date = None
    if history.exists():
        stamp = _dt.datetime.fromtimestamp(history.stat().st_mtime)
        history_date = _max_history_date(history)
        _say(f"  history.db      through {history_date or '?'}   "
             f"(file written {stamp:%Y-%m-%d %H:%M})")
    else:
        _say("  history.db      MISSING")

    if intraday.exists():
        stamp = _dt.datetime.fromtimestamp(intraday.stat().st_mtime)
        intraday_date = _max_intraday_date(intraday)
        _say(f"  minute store    through {intraday_date or '?'}   "
             f"(file written {stamp:%Y-%m-%d %H:%M})")
    else:
        _say("  minute store    MISSING")

    if expected is None:
        _say("  ? the exchange calendar could not say which session should be here.")
        return base, True

    target = expected.isoformat()
    have = max([d for d in (history_date, intraday_date) if d], default=None)
    if have and have >= target:
        _say(f"  OK              {target} is present and will be imported.")
        return base, True

    _say()
    _say(f"  !! Neither file reaches {target}, the last completed session.")
    if history_date and history_date < target:
        _say(f"     history.db stops at {history_date}. The download in MubasherTrade PRO")
        _say("     is what advances it, and it looks like it has not been done for")
        _say("     this session yet.")
    _say("     Continuing anyway -- what is there will still be imported -- but")
    _say("     the candle will stay behind until the download is run.")
    return base, False


def _max_history_date(path):
    try:
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True) as c:
            row = c.execute('SELECT MAX(DATE) FROM "_COMI"').fetchone()
    except sqlite3.Error:
        return None
    raw = str(row[0]) if row and row[0] else ""
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) == 8 else None


def _max_intraday_date(path):
    try:
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as c:
            row = c.execute("SELECT MAX(INTRADAYDATE) FROM INTRADAY_MASTER").fetchone()
    except sqlite3.Error:
        return None
    raw = str(row[0]) if row and row[0] else ""
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) == 8 else None


# --------------------------------------------------------------------------- #
# 2. import
# --------------------------------------------------------------------------- #

def run_import(base):
    from sector_flow import mubasher_local as local

    started = time.time()
    metadata = local.import_local(base)
    _say(f"  {metadata['rows']:,} rows for {metadata['symbols']} symbols, "
         f"{metadata['first_session']} .. {metadata['last_session']}")
    _say(f"  daily record reaches {metadata['history_last_session']}; "
         f"the minute store added {metadata['intraday_rows_appended']:,} rows "
         f"on {metadata['intraday_sessions_appended'] or 'no further session'}")
    if metadata["unconfirmed_close_rows"]:
        _say(f"  {metadata['unconfirmed_close_rows']:,} of those close on a last trade "
             f"rather than an auction price")
    _say(f"  done in {time.time() - started:.1f}s")
    return metadata


# --------------------------------------------------------------------------- #
# 3. sector flow
# --------------------------------------------------------------------------- #

def run_sector_flow():
    """Rebuild the sector history so the page reads the measured turnover.

    Forced, because the store it reads has just been replaced. Left to its own
    "is a session missing?" check it would find none and skip, and the sessions
    it already holds would keep the estimate they were built from.
    """

    import subprocess

    started = time.time()
    completed = subprocess.run(
        [sys.executable, "scripts/refresh_sector_flow.py", "--force"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    # The script's own progress lines, not the JSON block it ends with. The
    # tail of that block is "unavailable_symbols": 0 and a brace, which says
    # nothing about whether the rebuild worked.
    lines = [line.strip() for line in (completed.stdout or "").splitlines()
             if line.strip()]
    spoken = [line for line in lines
              if " OK -- " in line or "rebuilding" in line or "sessions behind" in line]
    for line in (spoken or lines[-4:]):
        _say(f"  {line}")
    if completed.returncode != 0:
        _say(f"  ! refresh_sector_flow exited {completed.returncode}")
        for line in (completed.stderr or "").splitlines()[-8:]:
            _say(f"    {line}")
    else:
        _say(f"  done in {time.time() - started:.1f}s")
    return completed.returncode == 0


# --------------------------------------------------------------------------- #
# 4. did the candle actually land?
# --------------------------------------------------------------------------- #

def check_candle(expected):
    """How much of the tradeable universe reaches the last completed session.

    Read from the store rather than through the router, on purpose: this is
    asking whether the data arrived, and routing 229 symbols to answer it would
    take minutes and blame the wrong layer when it failed.
    """

    from sector_flow.measured_turnover import DEFAULT_DATABASE, TABLE

    try:
        from core.symbols import load_tradeable_symbols

        universe = {str(s).split(".")[0].upper() for s in load_tradeable_symbols()}
    except Exception:                                   # noqa: BLE001
        universe = set()

    if expected is None:
        _say("  the calendar could not name a session to check against.")
        return False

    target = expected.isoformat()
    with sqlite3.connect(f"file:{DEFAULT_DATABASE}?mode=ro", uri=True) as connection:
        present = {str(r[0]).split(".")[0].upper() for r in connection.execute(
            f"SELECT ticker FROM {TABLE} WHERE session_date=?", (target,))}
        newest = connection.execute(
            f"SELECT MAX(session_date) FROM {TABLE}").fetchone()[0]

    _say(f"  store's newest session   {newest}")
    _say(f"  last completed session   {target}")
    if not universe:
        _say(f"  symbols with that session: {len(present)}")
        return newest == target

    covered = universe & present
    _say(f"  tradeable symbols with it: {len(covered)} of {len(universe)}")
    missing = sorted(universe - present)
    if missing:
        shown = ", ".join(missing[:12])
        more = f" (+{len(missing) - 12} more)" if len(missing) > 12 else ""
        _say(f"  without it: {shown}{more}")
        _say("  A symbol that did not trade has no row, so some of these are")
        _say("  expected. A long list on a normal session is not.")
    return newest == target


# --------------------------------------------------------------------------- #
# 4. the forward test that used to run itself
# --------------------------------------------------------------------------- #

def run_gap_forward():
    """Record today's gap prediction and grade the one today settles.

    This had its own scheduled task at 14:40, reading the Rubix feed. It has
    been failing since the feed stopped, and it reads Mubasher's minute store
    now -- so it belongs to the click rather than to a clock. The minute store
    keeps a rolling fourteen sessions, which is ample for "record today, grade
    yesterday", and both halves are idempotent, so running it twice changes
    nothing.
    """

    import subprocess

    completed = subprocess.run(
        [sys.executable, "scripts/research/record_gap_forward.py", "daily"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in (completed.stdout or "").splitlines():
        if line.strip():
            _say(f"  {line.rstrip()}")
    if completed.returncode != 0:
        for line in (completed.stderr or "").splitlines()[-6:]:
            if line.strip():
                _say(f"  ! {line.rstrip()}")
    return completed.returncode == 0


# --------------------------------------------------------------------------- #
# 5. is anything that records itself quietly stalled?
# --------------------------------------------------------------------------- #

def run_live_source_shadow():
    """Run the live rules on EODHD and on Mubasher and record where they agree.

    It belongs to the click rather than to a clock for the same reason the
    import does: the session it records is only in Mubasher's record once the
    download has been done. The recorder is idempotent per session.
    """

    import subprocess

    completed = subprocess.run(
        [sys.executable, "scripts/record_live_source_shadow.py"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in (completed.stdout or "").splitlines():
        if line.strip():
            _say(f"  {line.rstrip()}")
    if completed.returncode != 0:
        for line in (completed.stderr or "").splitlines()[-6:]:
            if line.strip():
                _say(f"  ! {line.rstrip()}")
    return completed.returncode == 0


#: The stores that are supposed to gain a row every session, and where each
#: keeps the session it is up to. They are written by their own scheduled
#: tasks, not by this run -- which is exactly why they are checked here. A
#: recorder that stops says nothing, and the forward tests it feeds are the
#: kind that take a month to be worth reading, so a fortnight of silence is
#: half the experiment.
RECORDERS = (
    ("gap forward",       "data/research/gap_forward.db", "gap_predictions", "session"),
    ("confirmed breakout", "data/confirmed_breakout_forward.db", "sessions", "session_date"),
    ("live sessions",     "data/forward_testing.db", "live_sessions", "session_date"),
    ("live source shadow", "data/research/live_source_shadow.db", "runs", "session_date"),
)


def check_recorders(expected):
    """Report the last session each self-recording store reached."""

    target = expected.isoformat() if expected else None
    stalled = []
    for label, path, table, column in RECORDERS:
        if not Path(path).exists():
            _say(f"  {label:<20} store not present")
            continue
        try:
            with sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True) as c:
                newest = c.execute(f'SELECT MAX("{column}") FROM "{table}"').fetchone()[0]
        except sqlite3.Error as error:
            _say(f"  {label:<20} unreadable ({error})")
            stalled.append(label)
            continue
        newest = str(newest)[:10] if newest else None
        mark = "" if (target and newest and newest >= target) else "   <-- behind"
        if mark:
            stalled.append(label)
        _say(f"  {label:<20} through {newest or 'nothing recorded'}{mark}")

    if stalled:
        _say()
        _say("  These are written by their own scheduled tasks, not by this run.")
        _say("  One session behind right after the close is normal -- some only")
        _say("  record once the next session opens. Several days behind is not.")
    return stalled


# --------------------------------------------------------------------------- #

def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-sector-flow", action="store_true",
                        help="import only; leave the sector history alone")
    args = parser.parse_args(argv)

    total = 6 if args.skip_sector_flow else 7
    _open_log()
    _say()
    _say(RULE)
    _say(f"  EGX daily update   {_dt.datetime.now():%Y-%m-%d %H:%M}")
    _say(RULE)

    expected = _expected_session()
    failures = []

    _step(1, total, "what MubasherTrade PRO is holding")
    base, fresh = check_sources(expected)
    if base is None:
        _say()
        _say("  Stopping: there is nothing to read.")
        return 2
    if not fresh:
        failures.append("the terminal's data does not reach the last completed session")

    _step(2, total, "import into the measured store")
    try:
        run_import(base)
    except Exception as error:                          # noqa: BLE001
        _say(f"  ! import failed: {type(error).__name__}: {error}")
        _say("  The store was left as it was.")
        failures.append("import failed")

    step = 3
    if not args.skip_sector_flow:
        _step(step, total, "rebuild the sector liquidity history")
        if not run_sector_flow():
            failures.append("sector flow did not rebuild")
        step += 1

    _step(step, total, "is the daily candle current?")
    current = check_candle(expected)
    if not current:
        failures.append("the candle is not at the last completed session")
    step += 1

    _step(step, total, "record and grade the gap forward test")
    if not run_gap_forward():
        failures.append("the gap forward test did not record")
    step += 1

    _step(step, total, "record the live-source shadow: EODHD against Mubasher")
    if not run_live_source_shadow():
        failures.append("the live-source shadow did not record")
    step += 1

    _step(step, total, "are the forward-test recorders still recording?")
    stalled = check_recorders(expected)

    _say()
    _say(RULE)
    if not failures:
        rebuilt = "" if args.skip_sector_flow else " and the sector page is rebuilt"
        _say(f"  ALL GOOD -- the candle is current{rebuilt}.")
        # Named, not failed on. These stores are written by other tasks, and a
        # run that did its own job perfectly should not report a red result
        # for somebody else's -- but it must not stay silent either.
        if stalled:
            _say(f"  Worth a look: {', '.join(stalled)} behind the last session.")
        _say(RULE)
        return 0
    _say("  FINISHED WITH PROBLEMS:")
    for item in failures:
        _say(f"    - {item}")
    if not fresh:
        _say()
        _say("  Most likely: the download in MubasherTrade PRO has not been run")
        _say("  for this session. Do that, then run this again.")
    _say(RULE)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
