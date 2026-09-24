"""A daily record of what Swing Breakout and Breakout Watch said, and what followed.

Confirmed Breakout has kept one since 2026-08-26, and the Daily Dashboard's
decisions are in `data/forward_testing.db`. These two pages rendered and
forgot: a candidate named on a Tuesday left no trace by Wednesday, so neither
rule could be scored on its live record the way the daily strategy was. A rule
nobody can score later is a rule that can only ever be argued about.

**Nothing here re-derives either rule.** `services.swing_breakout.scan` and
`strategy_momentum_breakout.watch.watch` are imported and called, exactly as
their pages call them, and their output is written down.

## What is resolved, and how

* **A swing candidate** is entered at the **next session's close** after the
  one it was named on, and held `SwingConfig.measured_holding_days` (20)
  sessions. That is the executable reading of the study, which held twenty
  days from the breakout, and it is what the Confirmed Breakout forward test
  does, so the two records answer the same question. Costs are the measured
  round trip for that symbol (`core.effective_cost`), falling back to the
  config's own figure when the symbol has not been measured.
* **A watch candidate** is not a trade: it is a name whose trigger has not
  fired. It resolves TRIGGERED when a close clears the prior high it was
  waiting on within `WATCH_WINDOW` sessions, EXPIRED when the window passes
  without that. The useful number is the conversion rate, not a return.
* **Both are scored against the median symbol** over the same sessions
  (`core.measured_benchmark`), because a breakout that rose with the whole
  market has not shown anything.

Nothing is resolved early: a window that has not finished in the measured
record stays unresolved and is counted as still running. Prices for
resolution come from the measured Mubasher record, the same source the daily
strategy's replay uses — never from the live scan's own view.

The store is append-only and its triggers refuse an update or a delete, so a
disappointing week cannot be edited afterwards.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "swing_breakout_forward.db"

#: How long a watch candidate has to fire before it is called expired. Ten
#: sessions is the page's own horizon for "approaching", and it is fixed here
#: before any conversion rate has been seen.
WATCH_WINDOW = 10

#: How many skipped sessions one run will replay. The recorder runs from the
#: daily click, so a day it is not clicked is a day it did not see -- 2026-09-23
#: was lost that way. Two trading weeks covers a holiday and a missed week;
#: anything older is left out and reported rather than replayed.
MAX_CATCH_UP_SESSIONS = 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    session_date TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    source TEXT NOT NULL,
    symbols_considered INTEGER NOT NULL,
    symbols_unreadable INTEGER NOT NULL,
    candidate_count INTEGER NOT NULL,
    funnel_json TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    config_json TEXT NOT NULL,
    UNIQUE (session_date, source)
);

CREATE TABLE IF NOT EXISTS candidates (
    candidate_id TEXT PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,
    session_date TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    close REAL NOT NULL,
    breakout_level REAL NOT NULL,
    volume_ratio REAL NOT NULL,
    momentum_12_1 REAL,
    momentum_rank REAL,
    atr_percent REAL,
    extension_percent REAL,
    holding_sessions INTEGER NOT NULL,
    config_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watch_candidates (
    watch_id TEXT PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,
    session_date TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    close REAL NOT NULL,
    prior_high REAL NOT NULL,
    distance_percent REAL,
    distance_atr REAL,
    turnover_egp REAL,
    atr_percent REAL,
    window_sessions INTEGER NOT NULL,
    config_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outcomes (
    outcome_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL UNIQUE REFERENCES candidates(candidate_id),
    resolved_at TEXT NOT NULL,
    status TEXT NOT NULL,
    entry_date TEXT,
    entry_price REAL,
    exit_date TEXT,
    exit_price REAL,
    sessions_held INTEGER,
    cost_percent REAL,
    net_percent REAL,
    benchmark_percent REAL,
    lift_percent REAL,
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS watch_outcomes (
    outcome_id TEXT PRIMARY KEY,
    watch_id TEXT NOT NULL UNIQUE REFERENCES watch_candidates(watch_id),
    resolved_at TEXT NOT NULL,
    status TEXT NOT NULL,
    trigger_date TEXT,
    sessions_to_trigger INTEGER,
    note TEXT NOT NULL DEFAULT ''
);

CREATE TRIGGER IF NOT EXISTS candidates_immutable_update
BEFORE UPDATE ON candidates BEGIN
    SELECT RAISE(ABORT, 'forward candidates are immutable');
END;
CREATE TRIGGER IF NOT EXISTS candidates_immutable_delete
BEFORE DELETE ON candidates BEGIN
    SELECT RAISE(ABORT, 'forward candidates are immutable');
END;
CREATE TRIGGER IF NOT EXISTS watch_immutable_update
BEFORE UPDATE ON watch_candidates BEGIN
    SELECT RAISE(ABORT, 'forward candidates are immutable');
END;
CREATE TRIGGER IF NOT EXISTS watch_immutable_delete
BEFORE DELETE ON watch_candidates BEGIN
    SELECT RAISE(ABORT, 'forward candidates are immutable');
END;
CREATE TRIGGER IF NOT EXISTS outcomes_immutable_update
BEFORE UPDATE ON outcomes BEGIN
    SELECT RAISE(ABORT, 'forward outcomes are immutable');
END;
CREATE TRIGGER IF NOT EXISTS outcomes_immutable_delete
BEFORE DELETE ON outcomes BEGIN
    SELECT RAISE(ABORT, 'forward outcomes are immutable');
END;
CREATE TRIGGER IF NOT EXISTS watch_outcomes_immutable_update
BEFORE UPDATE ON watch_outcomes BEGIN
    SELECT RAISE(ABORT, 'forward outcomes are immutable');
END;
CREATE TRIGGER IF NOT EXISTS watch_outcomes_immutable_delete
BEFORE DELETE ON watch_outcomes BEGIN
    SELECT RAISE(ABORT, 'forward outcomes are immutable');
END;

CREATE INDEX IF NOT EXISTS idx_candidates_session ON candidates(session_date);
CREATE INDEX IF NOT EXISTS idx_watch_session ON watch_candidates(session_date);
"""

SWING = "SWING_BREAKOUT"
WATCH = "BREAKOUT_WATCH"


def _now(value=None):
    return value or datetime.now(timezone.utc).astimezone()


def _identifier(*parts):
    """A stable id, so re-recording a session writes nothing new."""
    key = "|".join(str(part) for part in parts)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def config_fingerprint(config):
    """A hash of the thresholds, so two calibrations are never pooled."""
    payload = json.dumps(asdict(config), sort_keys=True, separators=(",", ":"),
                         default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16], payload


class SwingForwardStore:
    """Append-only evidence for both pages, in its own file."""

    def __init__(self, path=DEFAULT_DATABASE):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def rows(self, sql, parameters=()):
        with self._connect() as connection:
            return [dict(r) for r in connection.execute(sql, parameters).fetchall()]

    def record_session(self, session, candidates=(), watch_candidates=()):
        """One session and what it named, in one transaction.

        ``INSERT OR IGNORE`` throughout: re-running a day is a no-op rather
        than a duplicate, and a recorded candidate can never be rewritten.
        """
        written = {"candidates": 0, "watch": 0}
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT OR IGNORE INTO sessions
                   (session_id, session_date, recorded_at, source,
                    symbols_considered, symbols_unreadable, candidate_count,
                    funnel_json, config_hash, config_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                tuple(session[key] for key in (
                    "session_id", "session_date", "recorded_at", "source",
                    "symbols_considered", "symbols_unreadable",
                    "candidate_count", "funnel_json", "config_hash",
                    "config_json")))
            for row in candidates:
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO candidates
                       (candidate_id, dedupe_key, session_date, recorded_at,
                        symbol, close, breakout_level, volume_ratio,
                        momentum_12_1, momentum_rank, atr_percent,
                        extension_percent, holding_sessions, config_hash)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    tuple(row[key] for key in (
                        "candidate_id", "dedupe_key", "session_date",
                        "recorded_at", "symbol", "close", "breakout_level",
                        "volume_ratio", "momentum_12_1", "momentum_rank",
                        "atr_percent", "extension_percent", "holding_sessions",
                        "config_hash")))
                written["candidates"] += cursor.rowcount == 1
            for row in watch_candidates:
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO watch_candidates
                       (watch_id, dedupe_key, session_date, recorded_at, symbol,
                        close, prior_high, distance_percent, distance_atr,
                        turnover_egp, atr_percent, window_sessions, config_hash)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    tuple(row[key] for key in (
                        "watch_id", "dedupe_key", "session_date", "recorded_at",
                        "symbol", "close", "prior_high", "distance_percent",
                        "distance_atr", "turnover_egp", "atr_percent",
                        "window_sessions", "config_hash")))
                written["watch"] += cursor.rowcount == 1
            connection.commit()
        return written

    def unresolved_candidates(self):
        return self.rows(
            """SELECT c.* FROM candidates c
               LEFT JOIN outcomes o ON o.candidate_id = c.candidate_id
               WHERE o.candidate_id IS NULL
               ORDER BY c.session_date, c.symbol""")

    def unresolved_watch(self):
        return self.rows(
            """SELECT w.* FROM watch_candidates w
               LEFT JOIN watch_outcomes o ON o.watch_id = w.watch_id
               WHERE o.watch_id IS NULL
               ORDER BY w.session_date, w.symbol""")

    def record_outcome(self, outcome):
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT OR IGNORE INTO outcomes
                   (outcome_id, candidate_id, resolved_at, status, entry_date,
                    entry_price, exit_date, exit_price, sessions_held,
                    cost_percent, net_percent, benchmark_percent, lift_percent,
                    note)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                tuple(outcome.get(key) for key in (
                    "outcome_id", "candidate_id", "resolved_at", "status",
                    "entry_date", "entry_price", "exit_date", "exit_price",
                    "sessions_held", "cost_percent", "net_percent",
                    "benchmark_percent", "lift_percent", "note")))
            connection.commit()

    def record_watch_outcome(self, outcome):
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT OR IGNORE INTO watch_outcomes
                   (outcome_id, watch_id, resolved_at, status, trigger_date,
                    sessions_to_trigger, note)
                   VALUES (?,?,?,?,?,?,?)""",
                tuple(outcome.get(key) for key in (
                    "outcome_id", "watch_id", "resolved_at", "status",
                    "trigger_date", "sessions_to_trigger", "note")))
            connection.commit()


def measured_closes(symbol):
    """The measured record's closes for one symbol, or ``None``."""
    import pandas as pd

    from core.mubasher_live_history import READY, mubasher_live_history

    frame, status, _ = mubasher_live_history(symbol.split(".")[0], min_bars=30)
    if status != READY or frame is None or frame.empty:
        return None
    series = frame["Close"].astype(float)
    series.index = pd.to_datetime(series.index).normalize()
    return series[~series.index.duplicated(keep="last")].sort_index()


def _position(series, session):
    """Where ``session`` sits in the series, or ``None`` if it is not a bar."""
    import pandas as pd

    if series is None or series.empty:
        return None
    day = pd.Timestamp(session).normalize()
    position = int(series.index.searchsorted(day, side="right")) - 1
    if position < 0 or series.index[position] != day:
        return None
    return position


class SwingForwardTest:
    """Record a session, and resolve whatever has finished its window."""

    def __init__(self, store=None, config=None):
        from services.swing_breakout import SwingConfig

        self.store = store or SwingForwardStore()
        self.config = config or SwingConfig()
        self.fingerprint, self.config_json = config_fingerprint(self.config)

    # -- recording ---------------------------------------------------------

    def missing_sessions(self, histories, limit=MAX_CATCH_UP_SESSIONS):
        """Sessions the record skipped, oldest first, that ``histories`` can replay.

        Only after the first session already recorded: a gap is a day the daily
        run was not clicked, while anything before the first is history the
        rule never saw live, and writing that in now would be a backtest filed
        as a forward test. The newest session is not included -- ``record``
        takes it from the full histories.
        """
        import pandas as pd

        recorded = {row["session_date"] for row in self.store.rows(
            "SELECT session_date FROM sessions WHERE source = ?", (SWING,))}
        if not recorded:
            return []
        first = pd.Timestamp(min(recorded))
        traded = set()
        for frame in (histories or {}).values():
            if frame is None or not len(frame):
                continue
            index = pd.to_datetime(frame.index)
            traded.update(day.date().isoformat() for day in index[index > first])
        if not traded:
            return []
        newest = max(traded)
        missing = sorted(day for day in traded
                         if day not in recorded and day != newest)
        return missing[-int(limit):]

    def record_missed(self, histories, now=None, limit=MAX_CATCH_UP_SESSIONS):
        """Replay every skipped session from the histories as they stood then.

        Each replay sees only bars on or before its own session, so the liquid
        universe, the momentum rank and both rules are computed from what was
        knowable that evening. ``recorded_at`` is the time of the replay, which
        is what makes a caught-up session distinguishable from one recorded on
        the day.
        """
        import pandas as pd

        results = []
        for day in self.missing_sessions(histories, limit=limit):
            ceiling = pd.Timestamp(day)
            sliced = {}
            for symbol, frame in histories.items():
                if frame is None or not len(frame):
                    continue
                cut = frame[pd.to_datetime(frame.index) <= ceiling]
                if len(cut):
                    sliced[symbol] = cut
            outcome = self.record(now=now, histories=sliced)
            if outcome.get("session") != day:
                # `record` dates a session from the bars themselves, so when no
                # liquid name traded on `day` it recorded the last session that
                # did -- already in the record or a missed day of its own, and
                # correct either way. Nothing is ever written under a date its
                # data does not reach; this only says `day` itself was empty.
                outcome = {**outcome, "wanted": day,
                           "note": f"no liquid symbol traded on {day}"}
            results.append(outcome)
        return results

    def record(self, now=None, histories=None, watch_result=None, failures=None):
        """Run both pages' own entry points and write down what they named."""
        from services.swing_breakout import (load_universe_histories,
                                             most_traded, scan)

        stamp = _now(now).isoformat()
        failures = {} if failures is None else failures
        if histories is None:
            histories = load_universe_histories(
                on_error=lambda symbol, reason: failures.setdefault(symbol, reason))
        if not histories:
            return {"session": None, "note": "no history could be read"}

        universe = most_traded(histories)
        session_date = max((str(frame.index[-1])[:10] for frame in universe.values()),
                           default="")
        if not session_date:
            return {"session": None, "note": "no dated bar in the universe"}

        result = scan(universe, session_date=session_date, config=self.config)
        candidates = [{
            "candidate_id": _identifier(SWING, session_date, candidate.symbol),
            "dedupe_key": f"{SWING}|{session_date}|{candidate.symbol}",
            "session_date": session_date,
            "recorded_at": stamp,
            "symbol": candidate.symbol,
            "close": float(candidate.close),
            "breakout_level": float(candidate.breakout_level),
            "volume_ratio": float(candidate.volume_ratio),
            "momentum_12_1": _finite(candidate.momentum_12_1),
            "momentum_rank": _finite(candidate.momentum_rank),
            "atr_percent": _finite(candidate.atr_percent),
            "extension_percent": _finite(candidate.extension_percent),
            "holding_sessions": int(self.config.measured_holding_days),
            "config_hash": self.fingerprint,
        } for candidate in result.candidates]

        written = self.store.record_session({
            "session_id": _identifier(SWING, session_date),
            "session_date": session_date,
            "recorded_at": stamp,
            "source": SWING,
            "symbols_considered": int(result.symbols_considered),
            "symbols_unreadable": len(failures),
            "candidate_count": len(candidates),
            "funnel_json": json.dumps(_counts(result.symbols_skipped),
                                      sort_keys=True),
            "config_hash": self.fingerprint,
            "config_json": self.config_json,
        }, candidates=candidates)

        watch_written = self._record_watch(session_date, stamp, histories,
                                           watch_result)
        return {"session": session_date, "candidates": len(candidates),
                "candidates_written": written["candidates"],
                "watch": watch_written["named"],
                "watch_written": watch_written["written"],
                "symbols_considered": int(result.symbols_considered),
                "unreadable": len(failures)}

    def _record_watch(self, session_date, stamp, histories, watch_result=None):
        """Breakout Watch's own names for the same session."""
        from strategy_momentum_breakout.watch import watch

        if watch_result is None:
            watch_result = watch(histories=histories)
        rows = [{
            "watch_id": _identifier(WATCH, session_date, candidate.symbol),
            "dedupe_key": f"{WATCH}|{session_date}|{candidate.symbol}",
            "session_date": session_date,
            "recorded_at": stamp,
            "symbol": candidate.symbol,
            "close": float(candidate.close),
            "prior_high": float(candidate.prior_high),
            "distance_percent": _finite(candidate.distance_percent),
            "distance_atr": _finite(candidate.distance_atr),
            "turnover_egp": _finite(candidate.turnover_egp),
            "atr_percent": _finite(candidate.atr_percent),
            "window_sessions": WATCH_WINDOW,
            "config_hash": self.fingerprint,
        } for candidate in watch_result.candidates]

        written = self.store.record_session({
            "session_id": _identifier(WATCH, session_date),
            "session_date": session_date,
            "recorded_at": stamp,
            "source": WATCH,
            "symbols_considered": int(watch_result.considered),
            "symbols_unreadable": len(watch_result.unreadable or {}),
            "candidate_count": len(rows),
            "funnel_json": json.dumps(dict(watch_result.funnel or {}),
                                      sort_keys=True),
            "config_hash": self.fingerprint,
            "config_json": self.config_json,
        }, watch_candidates=rows)
        return {"named": len(rows), "written": written["watch"]}

    # -- resolution --------------------------------------------------------

    def resolve(self, now=None):
        """Score every candidate whose window has finished, and nothing else."""
        from core.effective_cost import load_symbol_costs, round_trip_for
        from core.measured_benchmark import close_panel, median_return

        stamp = _now(now).isoformat()
        panel = close_panel()
        costs = load_symbol_costs()
        series_cache = {}
        counts = {"resolved": 0, "still_running": 0, "unreadable": 0,
                  "stale_config": 0, "watch_resolved": 0,
                  "watch_still_running": 0}

        def closes(symbol):
            if symbol not in series_cache:
                series_cache[symbol] = measured_closes(symbol)
            return series_cache[symbol]

        for row in self.store.unresolved_candidates():
            if row["config_hash"] != self.fingerprint:
                counts["stale_config"] += 1
                continue
            series = closes(row["symbol"])
            start = _position(series, row["session_date"])
            if series is None or start is None:
                counts["unreadable"] += 1
                continue
            held = int(row["holding_sessions"])
            # Entered at the NEXT close, held `held` sessions from there.
            if start + 1 + held >= len(series):
                counts["still_running"] += 1
                continue
            entry_price = float(series.iloc[start + 1])
            exit_price = float(series.iloc[start + 1 + held])
            cost = round_trip_for(costs, row["symbol"])
            cost = (float(cost) if cost is not None
                    else float(self.config.round_trip_cost_percent))
            gross = (exit_price / entry_price - 1.0) * 100.0
            net = gross - cost
            benchmark = median_return(panel, row["session_date"], held + 1)
            self.store.record_outcome({
                "outcome_id": _identifier("OUTCOME", row["candidate_id"]),
                "candidate_id": row["candidate_id"],
                "resolved_at": stamp,
                "status": "CLOSED",
                "entry_date": str(series.index[start + 1].date()),
                "entry_price": round(entry_price, 4),
                "exit_date": str(series.index[start + 1 + held].date()),
                "exit_price": round(exit_price, 4),
                "sessions_held": held,
                "cost_percent": round(cost, 4),
                "net_percent": round(net, 4),
                "benchmark_percent": (None if benchmark is None
                                      else round(benchmark, 4)),
                "lift_percent": (None if benchmark is None
                                 else round(net - benchmark, 4)),
                "note": "" if benchmark is not None else "no cross-section",
            })
            counts["resolved"] += 1

        self._resolve_watch(stamp, closes, counts)
        return counts

    def _resolve_watch(self, stamp, closes, counts):
        """Did the name clear the high it was waiting on, inside its window?"""
        for row in self.store.unresolved_watch():
            series = closes(row["symbol"])
            start = _position(series, row["session_date"])
            if series is None or start is None:
                counts["unreadable"] += 1
                continue
            window = int(row["window_sessions"])
            available = len(series) - 1 - start
            triggered_at = None
            for step in range(1, min(window, available) + 1):
                if float(series.iloc[start + step]) > float(row["prior_high"]):
                    triggered_at = step
                    break
            if triggered_at is None and available < window:
                counts["watch_still_running"] += 1
                continue
            self.store.record_watch_outcome({
                "outcome_id": _identifier("WATCH_OUTCOME", row["watch_id"]),
                "watch_id": row["watch_id"],
                "resolved_at": stamp,
                "status": "TRIGGERED" if triggered_at else "EXPIRED",
                "trigger_date": (str(series.index[start + triggered_at].date())
                                 if triggered_at else None),
                "sessions_to_trigger": triggered_at,
                "note": "",
            })
            counts["watch_resolved"] += 1

    # -- what the record says so far ---------------------------------------

    def report(self):
        import statistics

        rows = self.store.rows(
            """SELECT c.session_date, c.symbol, o.status, o.net_percent,
                      o.lift_percent
               FROM candidates c LEFT JOIN outcomes o
                 ON o.candidate_id = c.candidate_id
               ORDER BY c.session_date, c.symbol""")
        watch_rows = self.store.rows(
            """SELECT w.session_date, w.symbol, o.status, o.sessions_to_trigger
               FROM watch_candidates w LEFT JOIN watch_outcomes o
                 ON o.watch_id = w.watch_id
               ORDER BY w.session_date, w.symbol""")
        lifts = [r["lift_percent"] for r in rows if r["lift_percent"] is not None]
        nets = [r["net_percent"] for r in rows if r["net_percent"] is not None]
        triggered = [r for r in watch_rows if r["status"] == "TRIGGERED"]
        resolved_watch = [r for r in watch_rows if r["status"] is not None]
        sessions = self.store.rows(
            "SELECT source, COUNT(*) n, MIN(session_date) first, "
            "MAX(session_date) last FROM sessions GROUP BY source")
        return {
            "sessions": {r["source"]: {"n": r["n"], "first": r["first"],
                                       "last": r["last"]} for r in sessions},
            "candidates": len(rows),
            "closed": len(nets),
            "still_running": sum(1 for r in rows if r["status"] is None),
            "median_net": round(statistics.median(nets), 2) if nets else None,
            "median_lift": round(statistics.median(lifts), 2) if lifts else None,
            "win_rate": (round(100.0 * sum(1 for n in nets if n > 0) / len(nets), 1)
                         if nets else None),
            "watch_candidates": len(watch_rows),
            "watch_resolved": len(resolved_watch),
            "watch_triggered": len(triggered),
            "watch_conversion": (round(100.0 * len(triggered) / len(resolved_watch), 1)
                                 if resolved_watch else None),
        }


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number


def _counts(mapping):
    counts = {}
    for reason in (mapping or {}).values():
        counts[str(reason)] = counts.get(str(reason), 0) + 1
    return counts
