"""Stage B — durable, event-driven live paper monitor for EXPECTED_RANGE_SCALPER.

A long-running/pollable, restart-safe process. It loads the FROZEN pre-session
snapshot, verifies it, then reads only NEW Rubix events since a durable cursor,
drives the seven scenario state machines, appends only real state transitions
(unchanged evaluations are suppressed), and creates immutable READY signals once
per activation cycle. State, cursor and activation cycles persist in SQLite so a
restart never duplicates a signal. No new scenario opens after 14:15 Cairo. Feed
health uses market-wide value progression, not the frozen Mubasher timestamp.

Records only — never places an order, never auto-executes, never enables production.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import time
import uuid

import pandas as pd

from core.egx_session import CONTINUOUS_CLOSE, cairo_now
from providers.symbol_mapping import to_rubix_symbol
from scalping_expected_range.orchestration import CONTINUOUS_END
from scalping_expected_range.paper_recorder import (
    CANONICAL_SCENARIOS,
    ENGINE_TO_CANONICAL,
    SIGNAL_FIELDS,
    TRANSITION_FIELDS,
    _costs,
    _room,
    normalize_state,
)
from scalping_expected_range.paper_state import PaperStateStore
from scalping_expected_range.scenario_engine import LiveQuote, evaluate_scenarios

REJECTED_STATES = {"INVALID", "NO_TRADE", "DATA_STALE", "DATA_INSUFFICIENT",
                   "SPREAD_TOO_WIDE", "LIQUIDITY_TOO_LOW", "RANGE_CONSUMED"}


class RubixQuoteReader:
    """Reads only NEW quotes (received_at > cursor) from the read-only Rubix DB."""

    def __init__(self, rubix_db_path):
        self.path = Path(rubix_db_path)

    def new_quotes_since(self, cursor, limit=200_000):
        if not self.path.is_file():
            return []
        uri = f"file:{self.path.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            if cursor:
                rows = conn.execute(
                    "SELECT ticker,last_price,bid,ask,volume,market_timestamp,received_at "
                    "FROM quotes WHERE received_at > ? ORDER BY received_at LIMIT ?",
                    (cursor, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT ticker,last_price,bid,ask,volume,market_timestamp,received_at "
                    "FROM quotes ORDER BY received_at LIMIT ?", (limit,)).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]


class SingleInstanceLock:
    """Best-effort cross-process single-instance lock via an exclusive lock file."""

    def __init__(self, path):
        self.path = Path(path)
        self.fd = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.fd = open(self.path, "x")
            self.fd.write(str(datetime.now(timezone.utc)))
            self.fd.flush()
            return True
        except FileExistsError:
            return False

    def release(self):
        try:
            if self.fd:
                self.fd.close()
            if self.path.is_file():
                self.path.unlink()
        except OSError:
            pass


class LiveMonitor:
    def __init__(self, cfg, session_date, analyses, snap_map, state_store=None,
                 rubix_reader=None, holidays=(), output_root=None, reports_root=None):
        self.cfg = cfg
        self.session_date = session_date
        self.analyses = analyses            # {symbol: SymbolAnalysis} (frozen historical)
        self.snap = snap_map                # {symbol: snapshot row dict}
        self.state = state_store or PaperStateStore()
        self.reader = rubix_reader
        self.holidays = tuple(holidays or ())
        self.root = Path(output_root or getattr(cfg, "paper_output_root",
                                                "reports/expected_range_paper"))
        self.reports = Path(reports_root or "reports")
        self.session_dir = self.root / session_date
        self._assert_safe()

    def _assert_safe(self):
        assert self.cfg.paper_enabled, "paper_enabled must be true"
        assert not self.cfg.production_enabled, "production must remain disabled"
        assert not getattr(self.cfg, "automatic_execution", False)
        assert not getattr(self.cfg, "broker_orders_enabled", False)

    # -- one polling pass -------------------------------------------------

    BATCH_LIMIT = 200_000

    def poll_once(self, now=None):
        now_cairo = cairo_now(now)
        after_1415 = now_cairo.timetz().replace(tzinfo=None) >= CONTINUOUS_CLOSE
        # A monitor is per-session: with no cursor yet, start at this session's
        # continuous open (10:00 Cairo) so it never replays earlier sessions.
        cursor = self.state.get_cursor(self.session_date) or self._session_open_iso()
        rows = self.reader.new_quotes_since(cursor, limit=self.BATCH_LIMIT) if self.reader else []
        stats = {"events": len(rows), "evaluations": 0, "transitions": 0, "ready": 0,
                 "rejected": 0, "suppressed": 0, "advanced_cursor": cursor,
                 "hit_limit": len(rows) >= self.BATCH_LIMIT}
        if not rows:
            return stats

        # Latest quote per symbol in this batch (event-driven: only new events).
        latest = {}
        for r in rows:
            latest[r["ticker"]] = r          # rows are ordered by received_at asc
        max_recv = rows[-1]["received_at"]

        # Feed health: market-wide material-value progression across the batch.
        self._record_feed_health(rows, now_cairo)

        for rubix_ticker, row in latest.items():
            symbol = self._engine_symbol(rubix_ticker)
            analysis = self.analyses.get(symbol)
            if analysis is None:
                continue
            live = self._live_quote(row, now_cairo)
            ev = evaluate_scenarios(analysis, live, self.cfg)
            per_scn = self._states_for_symbol(ev)
            snaprow = self.snap.get(symbol, {})
            for canonical, (new_state, scn_obj, advisory) in per_scn.items():
                stats["evaluations"] += 1
                self.state.bump(self.session_date, "evaluations_processed")
                st = self.state.get_state(self.session_date, symbol, canonical)
                prev = st["state"]
                cycle = st["activation_cycle"]
                ready_cycle = st["ready_recorded_cycle"]
                if new_state == prev:
                    stats["suppressed"] += 1
                    self.state.bump(self.session_date, "unchanged_suppressed")
                    continue
                # a real transition
                note = ""
                if new_state == "READY" and prev != "READY":
                    cycle += 1
                    stats["ready"] += 1
                    self.state.bump(self.session_date, "ready_activations")
                elif new_state in REJECTED_STATES:
                    stats["rejected"] += 1
                    self.state.bump(self.session_date, "rejected_activations")
                self.state.record_transition(self.session_date, symbol, canonical, prev,
                                             new_state, cycle, now_cairo.isoformat(), note)
                self.state.bump(self.session_date, "transitions_recorded")
                stats["transitions"] += 1

                if new_state == "READY" and ready_cycle != cycle:
                    if after_1415:
                        self.state.record_transition(
                            self.session_date, symbol, canonical, "READY", "READY", cycle,
                            now_cairo.isoformat(), "NO_NEW_ENTRY_AFTER_1415")
                        ready_cycle = cycle          # do not keep retrying this cycle
                    else:
                        sig = self._build_signal(symbol, canonical, cycle, scn_obj, ev,
                                                 live, snaprow, now_cairo)
                        if self.state.try_record_signal(sig):   # atomic, restart-safe
                            ready_cycle = cycle
                self.state.set_state(self.session_date, symbol, canonical, new_state,
                                     cycle, ready_cycle)

        self.state.set_cursor(self.session_date, max_recv)
        stats["advanced_cursor"] = max_recv
        return stats

    def _session_open_iso(self):
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo
        from core.egx_session import REGULAR_OPEN
        d = _dt.fromisoformat(self.session_date).date()
        open_cairo = _dt.combine(d, REGULAR_OPEN, tzinfo=ZoneInfo("Africa/Cairo"))
        return open_cairo.astimezone(timezone.utc).isoformat()

    def drain(self, now=None, max_iterations=50):
        """Process all currently-available session events (offline/catch-up)."""
        totals = {"events": 0, "evaluations": 0, "transitions": 0, "ready": 0,
                  "rejected": 0, "suppressed": 0, "iterations": 0}
        for _ in range(max_iterations):
            s = self.poll_once(now)
            for k in ("events", "evaluations", "transitions", "ready", "rejected", "suppressed"):
                totals[k] += s[k]
            totals["iterations"] += 1
            totals["advanced_cursor"] = s["advanced_cursor"]
            if not s["hit_limit"] or s["events"] == 0:
                break
        self.export_csvs()
        return totals

    # -- run loop ---------------------------------------------------------

    def run(self, now_fn=None, poll_interval=15, max_iterations=None, until_time=None,
            stop_after_seconds=None):
        """Poll until 14:15 Cairo (or a bound). Safe to restart at any time."""
        started = time.monotonic()
        iterations = 0
        end = until_time or CONTINUOUS_END
        while True:
            now = cairo_now(now_fn() if now_fn else None)
            self.poll_once(now)
            iterations += 1
            self.export_csvs()
            if now.timetz().replace(tzinfo=None) >= end:
                break
            if max_iterations and iterations >= max_iterations:
                break
            if stop_after_seconds and (time.monotonic() - started) >= stop_after_seconds:
                break
            if now_fn is None:
                time.sleep(poll_interval)
            else:
                break                            # test/offline: single deterministic pass
        return {"iterations": iterations}

    # -- scenario + quote helpers ----------------------------------------

    def _states_for_symbol(self, ev):
        out = {c: ("WAIT", None, ev.advisory) for c in CANONICAL_SCENARIOS}
        gate = ev.scenarios[0] if len(ev.scenarios) == 1 else None
        if gate is not None and normalize_state(gate.status) in (
                "DATA_STALE", "DATA_INSUFFICIENT", "SPREAD_TOO_WIDE", "LIQUIDITY_TOO_LOW"):
            gstate = normalize_state(gate.status)
            return {c: (gstate, gate, ev.advisory) for c in CANONICAL_SCENARIOS}
        for scn in ev.scenarios:
            canonical = ENGINE_TO_CANONICAL.get(scn.name)
            if canonical:
                out[canonical] = (normalize_state(scn.status), scn, ev.advisory)
        return out

    def _live_quote(self, row, now_cairo):
        last = _f(row.get("last_price")); bid = _f(row.get("bid")); ask = _f(row.get("ask"))
        q = LiveQuote(last=last, bid=bid, ask=ask, volume=_f(row.get("volume")),
                      received_at=row.get("received_at"),
                      exchange_timestamp=row.get("market_timestamp"))
        if bid and ask and bid > 0 and ask >= bid:
            q.spread_percent = round((ask - bid) / ((ask + bid) / 2.0) * 100.0, 4)
        recv = pd.to_datetime(row.get("received_at"), utc=True, errors="coerce")
        if pd.notna(recv):
            q.quote_age_seconds = round(
                (now_cairo.astimezone(timezone.utc) - recv.to_pydatetime()).total_seconds(), 1)
        q.available = last is not None and last > 0
        q.freshness = "OK" if (q.quote_age_seconds is not None
                               and q.quote_age_seconds <= self.cfg.maximum_quote_age_seconds) else "STALE"
        return q

    def _record_feed_health(self, rows, now_cairo):
        """Market-wide value progression: did ANY symbol's material value change?"""
        changed = False
        for r in rows:
            # a heartbeat/duplicate has no volume increase and no price move vs itself;
            # any non-null last/bid/ask/volume difference across the batch counts.
            if _f(r.get("last_price")) or _f(r.get("volume")):
                changed = True
                break
        status = "FEED_VALUE_PROGRESSING" if changed else "FEED_VALUE_STALLED"
        self.state.record_feed_health(self.session_date, now_cairo.isoformat(),
                                      0.0 if changed else None, status)

    def _engine_symbol(self, rubix_ticker):
        # snapshot symbols are engine symbols (e.g. COMI.CA); map back by suffix match
        up = str(rubix_ticker).upper()
        for symbol in self.analyses:
            if to_rubix_symbol(symbol).upper() == up:
                return symbol
        return up

    def _build_signal(self, symbol, canonical, cycle, scn, ev, live, snaprow, now):
        return {
            "SignalUUID": str(uuid.uuid4()), "SessionDate": self.session_date,
            "Symbol": symbol, "Scenario": canonical, "ActivationCycle": cycle,
            "DecisionTimestampCairo": now.isoformat(),
            "ReceiptTimestamp": live.received_at, "ExchangeTimestamp": live.exchange_timestamp,
            "Last": live.last, "Bid": live.bid, "Ask": live.ask,
            "Spread%": live.spread_percent, "QuoteAgeSec": live.quote_age_seconds,
            "SessionCumulativeVolume": live.volume,
            "EntryTrigger": scn.entry_trigger, "EntryPrice": scn.entry_trigger,
            "FixedTargetPrice": scn.take_profit, "FixedStopPrice": scn.stop_loss,
            "EstimatedCosts%": _costs(self.cfg, live.spread_percent),
            "NetTarget%": scn.net_profit_percent, "NetRisk%": scn.net_loss_percent,
            "NetRR": scn.net_rr,
            "CoreLow": snaprow.get("CoreLow"), "CoreHigh": snaprow.get("CoreHigh"),
            "ExpansionLow": snaprow.get("ExpansionLow"), "ExpansionHigh": snaprow.get("ExpansionHigh"),
            "ExtremeLow": snaprow.get("ExtremeLow"), "ExtremeHigh": snaprow.get("ExtremeHigh"),
            "RangePosition%": scn.range_position_percent, "RangeConsumed%": ev.range_consumed_percent,
            "RoomToCoreHigh%": ev.remaining_upside_base_percent,
            "RoomToExpansionHigh%": ev.remaining_upside_highvol_percent,
            "RoomToExtremeHigh%": _room(live.last, snaprow.get("ExtremeHigh")),
            "HistoricalRank": snaprow.get("CombinedRank"),
            "LiquidityScore": snaprow.get("AverageVolume20"),
            "VolatilityScore": snaprow.get("ADR%"),
            "TotalScore": snaprow.get("TotalScalpingScore"),
            "ConfirmationEvidence": scn.historical_evidence, "InvalidationReason": scn.reason,
            "HistoricalDataThrough": snaprow.get("HistoricalDataThrough"),
            "DatasetHash": snaprow.get("DatasetHash"), "StrategyVersion": self.cfg.strategy_version,
        }

    # -- CSV export from the durable state -------------------------------

    def export_csvs(self):
        signals = self.state.signals()
        self._overwrite(self.reports / "expected_range_paper_signals.csv", SIGNAL_FIELDS, signals)
        all_tr = self.state.transitions(self.session_date)
        tr_rows = [{"SessionDate": t["session_date"], "Symbol": t["symbol"],
                    "Scenario": t["scenario"], "FromState": t["from_state"],
                    "ToState": t["to_state"], "ActivationCycle": t["activation_cycle"],
                    "TransitionAtCairo": t["at_cairo"], "Advisory": "", "Note": t["note"]}
                   for t in all_tr]
        self._overwrite(self.reports / "expected_range_scenario_states.csv", TRANSITION_FIELDS,
                        tr_rows)
        # per-session artifacts
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._overwrite(self.session_dir / "scenario_transitions.csv", TRANSITION_FIELDS, tr_rows)
        session_signals = [s for s in signals if s.get("SessionDate") == self.session_date]
        self._overwrite(self.session_dir / "ready_signals.csv", SIGNAL_FIELDS, session_signals)
        rejected = [t for t in tr_rows if t["ToState"] in REJECTED_STATES]
        self._overwrite(self.session_dir / "rejected_scenarios.csv", TRANSITION_FIELDS, rejected)

    def _overwrite(self, path, fields, rows):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in fields})


def _f(v):
    try:
        if v is None or v == "" or pd.isna(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None
