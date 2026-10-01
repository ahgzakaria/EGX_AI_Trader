"""The T+0 radar's forward record: what it forecast, and what those names did next.

The radar forecasts one thing -- the size of each listed name's next-session
range -- and ranks by it. This is where that forecast is kept or lost. Each run
writes down every candidate with its rank and its forecast band before the
session it is about; a later run grades the ones whose next session has closed.

Graded against the session that follows, from the same record the list was
built on:

* the range, high to low, over the radar session's close -- the forecast itself,
  scored by how often it lands inside the band and how far off the middle was;
* the best and worst price from the true open, which only the terminal's minute
  store carries (the daily record's open is the previous close), so a session
  older than the minute store's window is graded without them rather than with
  an open that never traded;
* close to close and open to close, shown and never scored: the list makes no
  direction claim.

Each rule version keeps its own record. A list made under one rule is never
pooled with another's, so changing the rule starts a new record rather than
quietly blending two.

Nothing recorded can be rewritten. The triggers refuse an edit to a pick or a
run, and a pick once graded stays graded -- a record that can be tidied after a
bad week is not a record.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import statistics

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STORE = PROJECT_ROOT / "data" / "t0_radar_forward.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    session_date TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    symbols_considered INTEGER,
    candidates INTEGER,
    t0_enforced INTEGER,
    t0_eligible_count INTEGER,
    cost_measured_through TEXT,
    funnel_json TEXT,
    PRIMARY KEY (session_date, rule_version)
);
CREATE TABLE IF NOT EXISTS picks (
    session_date TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    symbol TEXT NOT NULL,
    rank INTEGER NOT NULL,
    score REAL NOT NULL,
    t0_status TEXT,
    close REAL NOT NULL,
    recent_range_pct REAL,
    median_range_pct REAL,
    round_trip_pct REAL,
    forecast_low_pct REAL,
    forecast_pct REAL,
    forecast_high_pct REAL,
    relative_turnover REAL,
    graded_at TEXT,
    next_session TEXT,
    next_traded INTEGER,
    next_open REAL,
    open_source TEXT,
    next_high REAL,
    next_low REAL,
    next_close REAL,
    next_range_pct REAL,
    next_change_pct REAL,
    next_open_to_close_pct REAL,
    next_best_from_open_pct REAL,
    next_worst_from_open_pct REAL,
    PRIMARY KEY (session_date, rule_version, symbol)
);
CREATE TRIGGER IF NOT EXISTS runs_are_final BEFORE UPDATE ON runs
BEGIN SELECT RAISE(ABORT, 'a recorded radar run is final'); END;
CREATE TRIGGER IF NOT EXISTS runs_are_kept BEFORE DELETE ON runs
BEGIN SELECT RAISE(ABORT, 'the radar record is append-only'); END;
CREATE TRIGGER IF NOT EXISTS picks_are_kept BEFORE DELETE ON picks
BEGIN SELECT RAISE(ABORT, 'the radar record is append-only'); END;
CREATE TRIGGER IF NOT EXISTS picks_are_final BEFORE UPDATE ON picks
WHEN OLD.graded_at IS NOT NULL
  OR NEW.session_date IS NOT OLD.session_date OR NEW.symbol IS NOT OLD.symbol
  OR NEW.rule_version IS NOT OLD.rule_version
  OR NEW.rank IS NOT OLD.rank OR NEW.score IS NOT OLD.score
  OR NEW.close IS NOT OLD.close OR NEW.round_trip_pct IS NOT OLD.round_trip_pct
  OR NEW.forecast_pct IS NOT OLD.forecast_pct
BEGIN SELECT RAISE(ABORT, 'a recorded pick is final'); END;
"""

#: The ranks the page shows by default, and the group the claim is about.
TOP = 10

#: Recorded from each candidate row, in the order of the INSERT below.
PICK_FIELDS = ("recent_range_pct", "median_range_pct", "round_trip_pct",
               "forecast_low_pct", "forecast_pct", "forecast_high_pct",
               "relative_turnover")


def _float(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value == value else None              # NaN is not a number to keep


def _current_rule():
    from t0_radar.radar import RULE_VERSION

    return RULE_VERSION


class RadarStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else STORE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def _connect(self):
        return sqlite3.connect(self.path, timeout=15)

    # -- writing ---------------------------------------------------------------

    def record(self, result, now=None):
        """Write one run's list. A session already recorded under this rule is left as is."""

        if not result.session_date:
            return {"session": None, "written": 0, "already_recorded": False}
        now = now or datetime.now(timezone.utc).isoformat()
        provenance = result.provenance or {}
        rule = provenance.get("rule_version") or _current_rule()
        with self._connect() as connection:
            held = connection.execute(
                "SELECT 1 FROM runs WHERE session_date = ? AND rule_version = ?",
                (result.session_date, rule)).fetchone()
            if held:
                return {"session": result.session_date, "written": 0,
                        "already_recorded": True}
            connection.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (result.session_date, rule, now, provenance.get("symbols_considered"),
                 int(len(result.candidates)), int(bool(provenance.get("t0_enforced"))),
                 provenance.get("t0_eligible_count"),
                 provenance.get("cost_measured_through"), json.dumps(result.funnel or {})))
            for _, row in result.candidates.iterrows():
                connection.execute(
                    "INSERT INTO picks (session_date, rule_version, symbol, rank, score, "
                    "t0_status, close, recent_range_pct, median_range_pct, round_trip_pct, "
                    "forecast_low_pct, forecast_pct, forecast_high_pct, relative_turnover) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (result.session_date, rule, str(row["symbol"]), int(row["rank"]),
                     float(row["score"]), row.get("t0_status"), float(row["close"]),
                     *(_float(row.get(f)) for f in PICK_FIELDS)))
        return {"session": result.session_date, "written": int(len(result.candidates)),
                "already_recorded": False}

    def pending_sessions(self):
        with self._connect() as connection:
            return [r[0] for r in connection.execute(
                "SELECT DISTINCT session_date FROM picks WHERE graded_at IS NULL "
                "ORDER BY session_date")]

    def grade(self, sessions, bar_of, opens_of, now=None):
        """Grade every pick whose next session is in ``sessions``.

        ``bar_of(symbol, day)`` returns that day's ``High/Low/Close`` or None when
        the symbol did not trade; ``opens_of(day)`` returns ``{symbol: open}``
        from the minute store, empty when it no longer holds the day.
        """

        now = now or datetime.now(timezone.utc).isoformat()
        days = sorted(str(d)[:10] for d in sessions)
        graded = 0
        with self._connect() as connection:
            for session in self.pending_sessions():
                later = [d for d in days if d > session]
                if not later:
                    continue
                following = later[0]
                opens = opens_of(following) or {}
                picks = connection.execute(
                    "SELECT rule_version, symbol, close FROM picks WHERE session_date = ? "
                    "AND graded_at IS NULL", (session,)).fetchall()
                for rule, symbol, close in picks:
                    outcome = grade_one(close, bar_of(symbol, following), opens.get(symbol))
                    connection.execute(
                        "UPDATE picks SET graded_at = ?, next_session = ?, next_traded = ?, "
                        "next_open = ?, open_source = ?, next_high = ?, next_low = ?, "
                        "next_close = ?, next_range_pct = ?, next_change_pct = ?, "
                        "next_open_to_close_pct = ?, next_best_from_open_pct = ?, "
                        "next_worst_from_open_pct = ? WHERE session_date = ? "
                        "AND rule_version = ? AND symbol = ? AND graded_at IS NULL",
                        (now, following, outcome["next_traded"], outcome["next_open"],
                         outcome["open_source"], outcome["next_high"], outcome["next_low"],
                         outcome["next_close"], outcome["next_range_pct"],
                         outcome["next_change_pct"], outcome["next_open_to_close_pct"],
                         outcome["next_best_from_open_pct"],
                         outcome["next_worst_from_open_pct"], session, rule, symbol))
                    graded += 1
        return graded

    # -- reading ---------------------------------------------------------------

    def run_info(self, session, rule_version=None):
        """What was recorded about one session's run, with its funnel decoded."""

        rule = rule_version or _current_rule()
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM runs WHERE session_date = ? AND rule_version = ?",
                (session, rule)).fetchone()
        if row is None:
            return {}
        info = dict(row)
        info["funnel"] = json.loads(info.pop("funnel_json") or "{}")
        return info

    def report(self, top=TOP, rule_version=None):
        """The forecast, scored for one rule: the first ``top`` ranks and the rest."""

        rule = rule_version or _current_rule()
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = [dict(r) for r in connection.execute(
                "SELECT * FROM picks WHERE rule_version = ? AND graded_at IS NOT NULL "
                "AND next_traded = 1", (rule,))]
            recorded = connection.execute(
                "SELECT COUNT(*) FROM runs WHERE rule_version = ?", (rule,)).fetchone()[0]
        return {"rule_version": rule, "sessions_recorded": recorded,
                "sessions_graded": len({r["session_date"] for r in rows}),
                "top": summarize([r for r in rows if r["rank"] <= top]),
                "rest": summarize([r for r in rows if r["rank"] > top]),
                "top_n": top}


def grade_one(close, bar, true_open):
    """One pick's next session, in percent. Missing inputs stay missing."""

    empty = {"next_traded": 0, "next_open": None, "open_source": None, "next_high": None,
             "next_low": None, "next_close": None, "next_range_pct": None,
             "next_change_pct": None, "next_open_to_close_pct": None,
             "next_best_from_open_pct": None, "next_worst_from_open_pct": None}
    if not bar or not close:
        return empty
    high, low, last = (_float(bar.get(k)) for k in ("High", "Low", "Close"))
    if not high or not low or not last:
        return empty
    outcome = dict(empty, next_traded=1, next_high=high, next_low=low, next_close=last,
                   next_range_pct=(high - low) / close * 100,
                   next_change_pct=(last / close - 1) * 100)
    true_open = _float(true_open)
    if true_open and true_open > 0:
        outcome.update(next_open=true_open, open_source="mubasher_minute",
                       next_open_to_close_pct=(last / true_open - 1) * 100,
                       next_best_from_open_pct=(high / true_open - 1) * 100,
                       next_worst_from_open_pct=(low / true_open - 1) * 100)
    return outcome


def summarize(rows):
    """How graded picks did against their own forecast, and what the price did."""

    if not rows:
        return {"picks": 0}

    def values(key):
        return [r[key] for r in rows if r.get(key) is not None]

    def share(predicate, subset):
        return (sum(1 for r in subset if predicate(r)) / len(subset)) if subset else None

    banded = [r for r in rows if r.get("forecast_low_pct") is not None
              and r.get("next_range_pct") is not None]
    costed = [r for r in rows if r.get("round_trip_pct") and r.get("next_range_pct") is not None]
    ratios = [r["next_range_pct"] / r["forecast_pct"] for r in banded if r.get("forecast_pct")]
    ranges, drift, change = (values("next_range_pct"), values("next_open_to_close_pct"),
                             values("next_change_pct"))
    return {
        "picks": len(rows),
        "range_median": statistics.median(ranges) if ranges else None,
        "inside_band_share": share(
            lambda r: r["forecast_low_pct"] <= r["next_range_pct"] <= r["forecast_high_pct"],
            banded),
        "actual_over_forecast_median": statistics.median(ratios) if ratios else None,
        "over_5x_cost_share": share(lambda r: r["next_range_pct"] >= 5 * r["round_trip_pct"],
                                    costed),
        "closed_up_share": share(lambda r: r["next_change_pct"] > 0,
                                 [r for r in rows if r.get("next_change_pct") is not None]),
        "change_mean": statistics.mean(change) if change else None,
        "open_to_close_mean": statistics.mean(drift) if drift else None,
    }


# --- the record the grading reads -------------------------------------------


def store_sessions(database=None, quorum=50):
    """The market's sessions as the measured store holds them."""

    from sector_flow import measured_turnover

    database = database or measured_turnover.DEFAULT_DATABASE
    with sqlite3.connect(f"file:{Path(database).as_posix()}?mode=ro", uri=True) as db:
        return [r[0] for r in db.execute(
            f"SELECT session_date FROM {measured_turnover.TABLE} GROUP BY session_date "
            f"HAVING COUNT(*) >= ? ORDER BY session_date", (quorum,))]


def measured_bar_reader():
    """``bar_of(symbol, day)`` over the measured store, one read per symbol."""

    from sector_flow import measured_turnover

    cache = {}

    def bar_of(symbol, day):
        if symbol not in cache:
            cache[symbol] = measured_turnover.frame_for(f"{symbol}.CA")
        frame = cache[symbol]
        if frame is None:
            return None
        rows = frame[frame.index.normalize() == pd.Timestamp(day)]
        if not len(rows):
            return None
        last = rows.iloc[-1]
        return {"High": last.get("High"), "Low": last.get("Low"), "Close": last.get("Close")}

    return bar_of


def minute_opens(day):
    from sector_flow import mubasher_local

    if day not in mubasher_local.available_sessions():
        return {}
    return mubasher_local.session_opens(day)


def grade_pending(store):
    return store.grade(store_sessions(), measured_bar_reader(), minute_opens)
