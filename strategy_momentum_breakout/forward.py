r"""Forward testing for CONFIRMED_VOLUME_BREAKOUT: an unfalsifiable record.

Everything measured about this strategy so far is in-sample. It was built on
2016-2026 history over 190 symbols that still exist, its thresholds were chosen
with both eras visible, and its walk-forward check re-used the same decade. None
of that is worthless and none of it is evidence about the future. This module
starts the only record that is: signals written down **before** anyone knows the
answer, and resolved later by the same code that backtested them.

**Why this does not use `forward_testing/`.** That subsystem exists and is good,
and it is recording live evidence for the Daily Dashboard strategy right now.
Two things stopped it being reused rather than a preference for a new file:

* its paper portfolio is hard-wired to the other strategy's execution model --
  a `buy_low`/`buy_high` zone filled within `ENTRY_WAIT_DAYS`, sized from
  `config/settings.json -> backtest`. This strategy buys at the **next close**,
  once, and is sized from its own configuration. The shared walker cannot
  represent it, and making it pluggable means editing fourteen live queries in
  a subsystem that is currently collecting real evidence;
* its `dedupe_key` is `ticker|date|signal_type` with no strategy in it, so two
  strategies emitting a BUY for the same name on the same day silently collide
  and the second is dropped.

Merging the two stores later is a `strategy` column and an import. Corrupting a
live record is not undoable, so the records stay separate until someone decides
otherwise.

**What makes the record honest, and each of these is a deliberate choice:**

* **Sessions are recorded even when they produce nothing.** The claim this file
  exists to support is "the rule fired N times in M sessions" -- without the
  denominator, a run of quiet weeks looks like no data instead of like evidence.
* **Signals are immutable**, enforced by triggers, and carry a hash of the
  configuration that produced them. A signal generated under one set of
  thresholds is never silently pooled with one generated under another.
* **Outcomes are resolved by `MomentumBreakoutBacktest.resolve_signal`**, the
  same method the backtest walks, so a forward result and a backtested one
  cannot diverge through a second implementation.
* **Every outcome carries its own benchmark**: what an average liquid name did
  over the identical window. This market's drift is enormous in EGP terms, and
  a forward record of raw returns would measure the market, not the rule.

    venv\Scripts\python.exe -m strategy_momentum_breakout.forward record
    venv\Scripts\python.exe -m strategy_momentum_breakout.forward resolve
    venv\Scripts\python.exe -m strategy_momentum_breakout.forward report
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import uuid

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

DEFAULT_DATABASE = PROJECT_ROOT / "data" / "confirmed_breakout_forward.db"

#: Stable, so the same signal recorded twice is the same row.
UUID_NAMESPACE = uuid.UUID("6b3f0a2e-9f1d-4c7a-9a15-2f5c8b0d41e7")

SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    session_date TEXT NOT NULL UNIQUE,
    recorded_at TEXT NOT NULL,
    symbols_scanned INTEGER NOT NULL,
    symbols_unreadable INTEGER NOT NULL,
    signal_count INTEGER NOT NULL,
    funnel_json TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    config_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    signal_id TEXT PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,
    session_date TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    ticker TEXT NOT NULL,
    close REAL NOT NULL,
    prior_high REAL NOT NULL,
    volume_ratio REAL NOT NULL,
    close_position REAL NOT NULL,
    turnover_egp REAL NOT NULL,
    atr_percent REAL NOT NULL,
    calm_reference REAL,
    stop_loss REAL NOT NULL,
    reference_risk_percent REAL NOT NULL,
    holding_bars INTEGER NOT NULL,
    entry_plan TEXT NOT NULL,
    reasons TEXT NOT NULL,
    config_hash TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS signals_immutable_update
BEFORE UPDATE ON signals BEGIN
    SELECT RAISE(ABORT, 'forward signals are immutable');
END;

CREATE TRIGGER IF NOT EXISTS signals_immutable_delete
BEFORE DELETE ON signals BEGIN
    SELECT RAISE(ABORT, 'forward signals are immutable');
END;

CREATE TABLE IF NOT EXISTS outcomes (
    outcome_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL UNIQUE REFERENCES signals(signal_id),
    resolved_at TEXT NOT NULL,
    status TEXT NOT NULL,
    entry_date TEXT,
    entry_price REAL,
    exit_date TEXT,
    exit_price REAL,
    bars_held INTEGER,
    exit_reason TEXT,
    net_percent REAL,
    benchmark_percent REAL,
    lift_percent REAL,
    note TEXT NOT NULL DEFAULT ''
);

CREATE TRIGGER IF NOT EXISTS outcomes_immutable_update
BEFORE UPDATE ON outcomes BEGIN
    SELECT RAISE(ABORT, 'forward outcomes are immutable');
END;

CREATE TRIGGER IF NOT EXISTS outcomes_immutable_delete
BEFORE DELETE ON outcomes BEGIN
    SELECT RAISE(ABORT, 'forward outcomes are immutable');
END;

CREATE INDEX IF NOT EXISTS idx_signals_session ON signals(session_date);
CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals(ticker);
"""


def _now(value=None):
    return value or datetime.now(timezone.utc).astimezone()


class ForwardStore:
    """Append-only evidence, in its own file."""

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

    def record_session(self, session, signals):
        """One session and its signals, in one transaction.

        `INSERT OR IGNORE` throughout, so re-running a day is a no-op rather
        than a duplicate or an error. Re-recording a session whose signals have
        already been written cannot change them: the triggers refuse.
        """
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT OR IGNORE INTO sessions
                   (session_id, session_date, recorded_at, symbols_scanned,
                    symbols_unreadable, signal_count, funnel_json,
                    config_hash, config_json)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                tuple(session[k] for k in (
                    "session_id", "session_date", "recorded_at",
                    "symbols_scanned", "symbols_unreadable", "signal_count",
                    "funnel_json", "config_hash", "config_json")),
            )
            written = 0
            for signal in signals:
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO signals
                       (signal_id,dedupe_key,session_date,recorded_at,ticker,
                        close,prior_high,volume_ratio,close_position,
                        turnover_egp,atr_percent,calm_reference,stop_loss,
                        reference_risk_percent,holding_bars,entry_plan,reasons,
                        config_hash)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    tuple(signal[k] for k in (
                        "signal_id", "dedupe_key", "session_date", "recorded_at",
                        "ticker", "close", "prior_high", "volume_ratio",
                        "close_position", "turnover_egp", "atr_percent",
                        "calm_reference", "stop_loss", "reference_risk_percent",
                        "holding_bars", "entry_plan", "reasons", "config_hash")),
                )
                written += cursor.rowcount == 1
            connection.commit()
        return written

    def unresolved(self):
        return self.rows(
            """SELECT s.* FROM signals s
               LEFT JOIN outcomes o ON o.signal_id = s.signal_id
               WHERE o.signal_id IS NULL
               ORDER BY s.session_date, s.ticker"""
        )

    def record_outcome(self, outcome):
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT OR IGNORE INTO outcomes
                   (outcome_id,signal_id,resolved_at,status,entry_date,
                    entry_price,exit_date,exit_price,bars_held,exit_reason,
                    net_percent,benchmark_percent,lift_percent,note)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                tuple(outcome[k] for k in (
                    "outcome_id", "signal_id", "resolved_at", "status",
                    "entry_date", "entry_price", "exit_date", "exit_price",
                    "bars_held", "exit_reason", "net_percent",
                    "benchmark_percent", "lift_percent", "note")),
            )
            connection.commit()


def config_fingerprint(cfg):
    """A hash of the thresholds, so two calibrations are never pooled."""
    payload = json.dumps(asdict(cfg), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16], payload


class ForwardTest:
    """Record today's signals; resolve the ones whose window has completed."""

    def __init__(self, store=None, cfg=None):
        from strategy_momentum_breakout.config import load as load_config

        self.cfg = cfg or load_config()
        self.store = store or ForwardStore()
        self.fingerprint, self.config_json = config_fingerprint(self.cfg)

    # -- writing down what the rule says, before anyone knows -------------

    def record(self, histories=None, now=None, require_completed=True) -> dict:
        """Scan and write down what the rule says, or refuse and write nothing.

        A recorded signal is immutable, so a signal computed from a half-formed
        daily bar is wrong *permanently*. The guard is therefore in the recorder
        rather than in the clock: if the session the data carries is newer than
        the one the exchange has authoritatively completed -- closing auction
        finished plus the configured settlement grace -- nothing is written and
        a later run picks it up cleanly.

        `require_completed=False` exists for the replay harness, which feeds
        truncated history and knows each session finished years ago.
        """
        from strategy_momentum_breakout.scan import scan

        timestamp = _now(now)
        result = scan(histories=histories, cfg=self.cfg)
        if not result.session_date:
            return {"session": None, "written": 0, "signals": 0,
                    "note": "no readable session"}

        if require_completed:
            from core.egx_session import authoritative_completed_session

            completed = authoritative_completed_session(now=timestamp)
            if pd.Timestamp(result.session_date).date() > completed:
                return {
                    "session": None, "written": 0, "signals": 0,
                    "note": (f"data carries {result.session_date}, but the "
                             f"exchange has only completed {completed}; "
                             f"refusing to record an unfinished session"),
                }

        session = {
            "session_id": str(uuid.uuid5(
                UUID_NAMESPACE, f"SESSION|{result.session_date}")),
            "session_date": result.session_date,
            "recorded_at": timestamp.isoformat(),
            "symbols_scanned": result.considered,
            "symbols_unreadable": len(result.unreadable),
            "signal_count": result.count,
            "funnel_json": json.dumps(
                {k: v for k, v in result.funnel.items() if v}, sort_keys=True),
            "config_hash": self.fingerprint,
            "config_json": self.config_json,
        }
        rows = []
        for signal in result.signals:
            dedupe = f"{signal.symbol}|{result.session_date}"
            rows.append({
                "signal_id": str(uuid.uuid5(UUID_NAMESPACE, dedupe)),
                "dedupe_key": dedupe,
                "session_date": result.session_date,
                "recorded_at": timestamp.isoformat(),
                "ticker": signal.symbol,
                "close": signal.close,
                "prior_high": signal.prior_high,
                "volume_ratio": signal.volume_ratio,
                "close_position": signal.close_position,
                "turnover_egp": signal.turnover_egp,
                "atr_percent": signal.atr_percent,
                "calm_reference": signal.calm_reference,
                "stop_loss": signal.stop_loss,
                "reference_risk_percent": signal.risk_percent,
                "holding_bars": self.cfg.holding_bars,
                "entry_plan": self.cfg.entry_mode,
                "reasons": " | ".join(signal.reasons),
                "config_hash": self.fingerprint,
            })
        written = self.store.record_session(session, rows)
        return {"session": result.session_date, "written": written,
                "signals": result.count, "scanned": result.considered,
                "funnel": {k: v for k, v in result.funnel.items() if v}}

    # -- finding out what happened ---------------------------------------

    def resolve(self, now=None) -> dict:
        """Walk every signal whose holding window has completed.

        A signal is only resolved once there are enough bars after it to reach
        its cap, so nothing is scored early on a partial window. Until then it
        stays unresolved and is reported as still running.
        """
        from core.research_router import get_current_research_history
        from indicators.technical import calculate_indicators
        from strategy_momentum_breakout.backtest import MomentumBreakoutBacktest

        timestamp = _now(now)
        pending = self.store.unresolved()
        if not pending:
            return {"resolved": 0, "still_running": 0, "unreadable": 0,
                    "stale_config": 0, "checked": 0}

        histories, resolved, running, skipped, stale = {}, 0, 0, 0, 0
        for signal in pending:
            # A signal recorded under one calibration must not be walked under
            # another. The maturity check below reads the holding cap that was
            # written down *with the signal*, while `resolve_signal` walks it
            # with the configuration loaded now -- so if those have diverged the
            # two disagree about what the trade even was. Rather than resolve it
            # wrongly or silently drop it, it is left pending and counted.
            if signal["config_hash"] != self.fingerprint:
                stale += 1
                continue
            ticker = signal["ticker"]
            if ticker not in histories:
                try:
                    frame = get_current_research_history(ticker)
                    histories[ticker] = frame[0] if isinstance(frame, tuple) else frame
                except Exception:                        # noqa: BLE001 - skipped
                    histories[ticker] = None
            frame = histories[ticker]
            if frame is None or frame.empty:
                skipped += 1
                continue

            data = calculate_indicators(frame)
            index = pd.to_datetime(data.index)
            position = int(index.searchsorted(
                pd.Timestamp(signal["session_date"]), side="left"))
            if position >= len(data) or index[position].date() != \
                    pd.Timestamp(signal["session_date"]).date():
                skipped += 1
                continue
            # Entry is the next close and the cap is `holding_bars` after that,
            # so the window needs that many bars beyond the signal before it can
            # be scored at all.
            if len(data) - 1 < position + 1 + int(signal["holding_bars"]):
                running += 1
                continue

            engine = MomentumBreakoutBacktest(ticker, self.cfg)
            trade = engine.resolve_signal(data, position)
            if trade is None:
                self.store.record_outcome(self._outcome(
                    signal, timestamp, status="NO_TRADE",
                    note="the next close was already at or below the stop"))
                resolved += 1
                continue

            net = (trade.exit_price / trade.entry_price - 1) * 100
            benchmark = self._benchmark(trade.entry_date, trade.exit_date)
            self.store.record_outcome(self._outcome(
                signal, timestamp, status="CLOSED",
                entry_date=trade.entry_date, entry_price=trade.entry_price,
                exit_date=trade.exit_date, exit_price=trade.exit_price,
                bars_held=int(trade.exit_index) - int(position) - 1,
                exit_reason=trade.exit_reason, net_percent=round(net, 4),
                benchmark_percent=benchmark,
                lift_percent=(round(net - benchmark, 4)
                              if benchmark is not None else None)))
            resolved += 1

        return {"resolved": resolved, "still_running": running,
                "unreadable": skipped, "stale_config": stale,
                "checked": len(pending)}

    def _outcome(self, signal, timestamp, **fields):
        row = {
            "outcome_id": str(uuid.uuid5(
                UUID_NAMESPACE, f"OUTCOME|{signal['signal_id']}")),
            "signal_id": signal["signal_id"],
            "resolved_at": timestamp.isoformat(),
            "entry_date": None, "entry_price": None, "exit_date": None,
            "exit_price": None, "bars_held": None, "exit_reason": None,
            "net_percent": None, "benchmark_percent": None,
            "lift_percent": None, "note": "",
        }
        row.update(fields)
        return row

    def _benchmark(self, entry_date, exit_date):
        """What an average tradeable name did over the identical window.

        Without this the forward record measures the Egyptian market, which
        over the backtested decade returned several hundred percent in nominal
        EGP. The claim being tested is that the rule *selects*, and selection
        is only visible against what not selecting would have paid.
        """
        pool = self._pool()
        if pool is None or pool.empty:
            return None
        window = pool.loc[(pool.index >= pd.Timestamp(entry_date))
                          & (pool.index <= pd.Timestamp(exit_date))]
        if len(window) < 2:
            return None
        returns = (window.iloc[-1] / window.iloc[0] - 1) * 100
        returns = returns.replace([np.inf, -np.inf], np.nan).dropna()
        return round(float(returns.mean()), 4) if len(returns) else None

    def _pool(self):
        """Closes for the names liquid enough for this strategy to have traded.

        Built once per `resolve` call and cached on the instance. The liquidity
        test is the same turnover floor the rule applies, measured over the
        whole available history rather than per date -- an approximation, and a
        deliberately generous one, because a benchmark that only contained the
        names the rule liked would not be a benchmark.
        """
        if getattr(self, "_pool_cache", None) is not None:
            return self._pool_cache
        from core.research_router import get_current_research_history
        from core.universe import active_symbols

        columns = {}
        for symbol in sorted(active_symbols()):
            try:
                frame = get_current_research_history(symbol)
                frame = frame[0] if isinstance(frame, tuple) else frame
            except Exception:                            # noqa: BLE001 - skipped
                continue
            if frame is None or len(frame) < 60 or "Close" not in frame:
                continue
            turnover = (frame["Close"] * frame["Volume"]).tail(60).median()
            if not np.isfinite(turnover) or turnover < self.cfg.minimum_turnover_egp:
                continue
            series = frame["Close"]
            series.index = pd.to_datetime(series.index)
            columns[symbol] = series[~series.index.duplicated(keep="last")]
        self._pool_cache = (pd.DataFrame(columns).sort_index()
                            if columns else pd.DataFrame())
        return self._pool_cache

    # -- what the record says so far -------------------------------------

    def report(self) -> dict:
        sessions = self.store.rows("SELECT * FROM sessions ORDER BY session_date")
        joined = self.store.rows(
            """SELECT s.*, o.status, o.entry_date, o.exit_date, o.exit_reason,
                      o.net_percent, o.benchmark_percent, o.lift_percent
               FROM signals s LEFT JOIN outcomes o ON o.signal_id = s.signal_id
               ORDER BY s.session_date, s.ticker"""
        )
        closed = [r for r in joined if r["status"] == "CLOSED"]
        lifts = [r["lift_percent"] for r in closed if r["lift_percent"] is not None]
        nets = [r["net_percent"] for r in closed if r["net_percent"] is not None]
        return {
            "sessions": len(sessions),
            "first_session": sessions[0]["session_date"] if sessions else None,
            "last_session": sessions[-1]["session_date"] if sessions else None,
            "signals": len(joined),
            "closed": len(closed),
            "still_running": sum(1 for r in joined if r["status"] is None),
            "no_trade": sum(1 for r in joined if r["status"] == "NO_TRADE"),
            "mean_net": round(float(np.mean(nets)), 2) if nets else None,
            "median_net": round(float(np.median(nets)), 2) if nets else None,
            "win_rate": (round(100.0 * sum(1 for n in nets if n > 0) / len(nets), 1)
                         if nets else None),
            "mean_lift": round(float(np.mean(lifts)), 2) if lifts else None,
            "configs": sorted({r["config_hash"] for r in joined}),
            "rows": joined,
            "session_rows": sessions,
        }


def main() -> int:
    from core.environment import load_project_environment

    load_project_environment()

    parser = argparse.ArgumentParser(
        description="Forward-test CONFIRMED_VOLUME_BREAKOUT.")
    parser.add_argument("action", choices=("record", "resolve", "report", "daily"),
                        help="daily = record then resolve, for a scheduled run")
    parser.add_argument("--database", default=None)
    args = parser.parse_args()

    store = ForwardStore(args.database) if args.database else ForwardStore()
    test = ForwardTest(store=store)

    if args.action in ("record", "daily"):
        outcome = test.record()
        if outcome["session"] is None:
            print(f"nothing recorded: {outcome['note']}")
        else:
            print(f"session {outcome['session']}: {outcome['signals']} signal(s) "
                  f"from {outcome['scanned']} symbols, {outcome['written']} newly "
                  f"written")
            if not outcome["signals"]:
                print("  no signal is the usual outcome; the session is still")
                print("  recorded, because the denominator is the evidence")
            for reason, count in sorted(outcome["funnel"].items(),
                                        key=lambda kv: -kv[1]):
                print(f"    {reason:22}: {count:,}")

    if args.action in ("resolve", "daily"):
        outcome = test.resolve()
        print(f"resolved {outcome['resolved']}, still running "
              f"{outcome['still_running']}, of {outcome['checked']} unresolved")
        if outcome.get("stale_config"):
            print(f"  {outcome['stale_config']} left pending: recorded under a "
                  f"different configuration than the one loaded now, and")
            print("  walking them under this one would score a plan nobody made")

    if args.action in ("report", "daily"):
        summary = test.report()
        print("\nFORWARD RECORD")
        print(f"  sessions recorded : {summary['sessions']}"
              + (f"  ({summary['first_session']} -> {summary['last_session']})"
                 if summary["sessions"] else ""))
        print(f"  signals           : {summary['signals']}")
        print(f"  closed            : {summary['closed']}")
        print(f"  still running     : {summary['still_running']}")
        if summary["no_trade"]:
            print(f"  no trade          : {summary['no_trade']}")
        if summary["closed"]:
            print(f"  mean net          : {summary['mean_net']:+.2f}%")
            print(f"  median net        : {summary['median_net']:+.2f}%")
            print(f"  win rate          : {summary['win_rate']:.0f}%")
            print(f"  mean lift vs pool : {summary['mean_lift']:+.2f}%")
        else:
            print("\n  Nothing has closed yet. The rule fires about ninety times a")
            print("  year across this universe and holds twenty sessions, so a")
            print("  first closed trade is weeks away and a number worth reading")
            print("  is many months away. That is the cost of an honest record.")
        if len(summary["configs"]) > 1:
            print(f"\n  WARNING: {len(summary['configs'])} different configurations "
                  f"in this record.")
            print("  Signals from different calibrations must not be pooled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
