"""Phase 9/10/14 — pre-session scanner + live monitor for EXPECTED_RANGE_SCALPER.

Runs the whole liquidity-first pipeline across the EGX universe on real data:
historical selection (frozen for the session), cross-sectional scoring, expected
ranges, and live scenario evaluation from a read-only Rubix quote snapshot.

Isolation: reads the Yahoo-backed completed-daily cache and the Rubix DB
``mode=ro`` only. Never writes to any provider, never touches an existing
strategy, never places an order. Paper recording is append-only and immutable,
and stays OFF unless ``paper_enabled`` is set.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import pandas as pd

from config.settings_manager import settings
from core.symbols import SYMBOL_SOURCE, load_symbols
from providers.symbol_mapping import to_rubix_symbol
from scalping_expected_range.config import ExpectedRangeConfig
from scalping_expected_range.historical_selector import HistoricalSelector
from scalping_expected_range.scenario_engine import (
    LiveQuote,
    evaluate_scenarios,
)
from scalping_expected_range.scoring import add_scores, default_rank
from scalping_expected_range.liquidity_model import (
    LIQUIDITY_LIMITED,
    LIQUIDITY_VALID,
)


class ExpectedRangeScanner:
    def __init__(self, config=None, rubix_db_path=None, symbols_path=SYMBOL_SOURCE,
                 holidays=(), now=None, cache=None):
        self.config = config or ExpectedRangeConfig.load()
        market = settings.get("market_data")
        self.rubix_path = Path(rubix_db_path or market.get(
            "rubix_db_path", "data/rubix_live_market.db"))
        self.symbols_path = symbols_path
        self.holidays = tuple(holidays or ())
        self.selector = HistoricalSelector(
            config=self.config, cache=cache,
            period=settings.get("data").get("history_period", "10y"),
            holidays=self.holidays, now=now)

    # -- pre-session scan --------------------------------------------------

    def scan(self, symbols=None, with_live=True):
        symbols = tuple(symbols or load_symbols(self.symbols_path))
        analyses = {s: self.selector.analyze(s) for s in symbols}
        records = [a.flat() for a in analyses.values()]
        frame = pd.DataFrame(records)

        live_map = {}
        if with_live:
            for s in symbols:
                live_map[s] = self._live_quote(s)
            frame["live_last"] = [live_map[s].last for s in frame["Symbol"]]
            frame["live_bid"] = [live_map[s].bid for s in frame["Symbol"]]
            frame["live_ask"] = [live_map[s].ask for s in frame["Symbol"]]
            frame["live_spread_percent"] = [live_map[s].spread_percent for s in frame["Symbol"]]
            frame["live_quote_age_seconds"] = [live_map[s].quote_age_seconds for s in frame["Symbol"]]
            frame["live_available"] = [live_map[s].available for s in frame["Symbol"]]
        else:
            frame["live_spread_percent"] = None
            frame["live_available"] = False

        frame = add_scores(frame, self.config)
        ranked = default_rank(frame)

        scenarios = self._scenarios_frame(analyses, live_map if with_live else {})
        views = self._views(ranked)
        return {
            "config": self.config,
            "universe": ranked,
            "scenarios": scenarios,
            "views": views,
            "summary": self._summary(ranked, scenarios),
            "analyses": analyses,
            "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        }

    # -- live monitor (Phase 10): refresh live fields only -----------------

    def live_monitor(self, previous_scan):
        """Refresh only live quote + scenario fields; historical ranking frozen."""

        analyses = previous_scan["analyses"]
        frame = previous_scan["universe"].copy()
        live_map = {s: self._live_quote(s) for s in analyses}
        frame["live_last"] = [live_map[s].last for s in frame["Symbol"]]
        frame["live_bid"] = [live_map[s].bid for s in frame["Symbol"]]
        frame["live_ask"] = [live_map[s].ask for s in frame["Symbol"]]
        frame["live_spread_percent"] = [live_map[s].spread_percent for s in frame["Symbol"]]
        frame["live_quote_age_seconds"] = [live_map[s].quote_age_seconds for s in frame["Symbol"]]
        frame["live_available"] = [live_map[s].available for s in frame["Symbol"]]
        # Re-score ONLY the live-spread component; the historical percentiles are
        # preserved because the historical inputs are unchanged.
        frame = add_scores(frame, self.config)
        scenarios = self._scenarios_frame(analyses, live_map)
        return {**previous_scan, "universe": frame, "scenarios": scenarios,
                "views": self._views(frame), "summary": self._summary(frame, scenarios),
                "generated_at": datetime.now(timezone.utc).astimezone().isoformat()}

    # -- scenario evaluation ----------------------------------------------

    def _scenarios_frame(self, analyses, live_map):
        rows = []
        for symbol, analysis in analyses.items():
            live = live_map.get(symbol, LiveQuote())
            ev = evaluate_scenarios(analysis, live, self.config)
            liq = analysis.liquidity
            vol = analysis.volatility
            base = analysis.expected.base
            for scn in ev.scenarios:
                rows.append({
                    "Symbol": symbol, "Scenario": scn.name, "Status": scn.status,
                    "Advisory": ev.advisory,
                    "Last": live.last, "Bid": live.bid, "Ask": live.ask,
                    "Spread%": live.spread_percent, "QuoteAgeSec": live.quote_age_seconds,
                    "EntryZoneLow": scn.entry_zone_low, "EntryZoneHigh": scn.entry_zone_high,
                    "EntryTrigger": scn.entry_trigger, "TakeProfit": scn.take_profit,
                    "StopLoss": scn.stop_loss, "Invalidation": scn.invalidation,
                    "ExpectedLow": base.expected_low if base else None,
                    "ExpectedHigh": base.expected_high if base else None,
                    "RangePosition%": scn.range_position_percent,
                    "RemainingUpside%": scn.remaining_upside_percent,
                    "RoomFor2%": scn.room_for_2pct, "NetRR": scn.net_rr,
                    "HistoricalEvidence": scn.historical_evidence,
                    "AvgVolume": liq.avg_volume_20, "MedianVolume": liq.median_volume_20,
                    "AvgTurnover": liq.avg_turnover_egp_20,
                    "LiquidityStatus": liq.status, "VolatilityClass": vol.classification,
                    "Reason": scn.reason, "DataWarning": ev.data_warning,
                })
        return pd.DataFrame(rows)

    # -- default views (Phase 9) ------------------------------------------

    def _views(self, ranked):
        tradable = ranked[ranked["TradableCandidate"]].copy()
        rejected = ranked[~ranked["TradableCandidate"]].copy()

        def top(frame, by, n=30, ascending=False):
            if frame.empty or by not in frame.columns:
                return frame.head(0)
            return frame.sort_values(by, ascending=ascending).head(n)

        rising = tradable[tradable.get("liq_volume_trend") == "RISING"] if "liq_volume_trend" in tradable else tradable.head(0)
        stale = ranked[ranked.get("prov_data_status").isin(["DATA_STALE", "DATA_INSUFFICIENT", "MISSING"])] \
            if "prov_data_status" in ranked else ranked.head(0)
        return {
            "highest_volume": top(tradable, "liq_avg_volume_20"),
            "highest_turnover": top(tradable, "liq_avg_turnover_egp_20"),
            "best_volume_volatility": tradable.sort_values(
                "EXPECTED_RANGE_SCALPING_SCORE", ascending=False).head(30),
            "most_consistent_2pct": top(tradable, "vol_target_2pct_frequency"),
            "highest_expected_range": top(tradable, "vol_adr_percent_20"),
            "rising_volume": rising.head(30),
            "rejected_low_liquidity": rejected.head(100),
            "data_stale_or_insufficient": stale.head(100),
        }

    def _summary(self, ranked, scenarios):
        liq_status = ranked.get("liq_status")
        vol_class = ranked.get("vol_adr_percent_20")
        ready = scenarios[scenarios["Status"].isin(["BOUNCE_READY", "READY"])] \
            if not scenarios.empty else scenarios
        return {
            "historical_symbols_analyzed": int(len(ranked)),
            "high_volume_candidates": int((ranked["TradableCandidate"]).sum()),
            "high_volume_high_volatility": int(
                ((ranked["TradableCandidate"]) &
                 (pd.to_numeric(ranked.get("vol_adr_percent_20"), errors="coerce") >= 2.0)).sum()),
            "expected_2pct_candidates": int(
                ((ranked["TradableCandidate"]) &
                 (pd.to_numeric(ranked.get("vol_target_2pct_frequency"), errors="coerce")
                  >= self.config.minimum_two_percent_frequency)).sum()),
            "live_executable_candidates": int(
                (ranked.get("live_available") == True).sum()) if "live_available" in ranked else 0,
            "ready_scenarios": int(len(ready)) if not scenarios.empty else 0,
            "range_consumed": int((scenarios["Status"] == "RANGE_CONSUMED").sum())
            if not scenarios.empty else 0,
            "low_liquidity_rejections": int((~ranked["TradableCandidate"]).sum()),
        }

    # -- live-quote accessor (read-only Rubix snapshot) --------------------

    def _live_quote(self, ticker) -> LiveQuote:
        if not self.rubix_path.is_file():
            return LiveQuote(available=False, freshness="RUBIX_DB_MISSING")
        mapped = to_rubix_symbol(ticker)
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT last_price,bid,ask,volume,market_timestamp,received_at "
                    "FROM quotes WHERE UPPER(ticker)=? ORDER BY received_at DESC LIMIT 1",
                    (mapped.upper(),)).fetchone()
        except sqlite3.Error:
            return LiveQuote(available=False, freshness="RUBIX_READ_ERROR")
        if row is None:
            return LiveQuote(available=False, freshness="SYMBOL_MISSING")
        last, bid, ask = _f(row["last_price"]), _f(row["bid"]), _f(row["ask"])
        q = LiveQuote(last=last, bid=bid, ask=ask, volume=_f(row["volume"]),
                      received_at=row["received_at"], exchange_timestamp=row["market_timestamp"])
        if bid and ask and bid > 0 and ask >= bid:
            mid = (bid + ask) / 2.0
            q.spread_percent = round((ask - bid) / mid * 100.0, 4)
        if row["received_at"]:
            age = (pd.Timestamp.now(tz="UTC") -
                   pd.to_datetime(row["received_at"], utc=True, errors="coerce")).total_seconds()
            q.quote_age_seconds = round(float(age), 1)
        q.available = last is not None and last > 0
        q.freshness = "OK" if (q.quote_age_seconds is not None
                               and q.quote_age_seconds <= self.config.maximum_quote_age_seconds) else "STALE"
        return q

    def _connect(self):
        uri = f"file:{self.rubix_path.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn


class ImmutablePaperSignalStore:
    """Append-only paper-signal store (Phase 14). Never rewrites a signal.

    Each pre-outcome signal is written with a content hash. Outcome fields are
    appended as a SEPARATE row keyed by the original signal hash so the original
    decision is never mutated.
    """

    FIELDS = [
        "SignalHash", "RecordKind", "Symbol", "Rank", "Advisory", "Scenario",
        "Status", "DecisionTime", "Last", "Bid", "Ask", "Spread%",
        "Entry", "TakeProfit", "StopLoss", "Invalidation", "RangePosition%",
        "RemainingUpside%", "AvgVolume", "AvgTurnover", "ExpectedLow", "ExpectedHigh",
        "Reason", "DataWarning", "HistoricalDate", "AppendedAt",
        # outcome-only fields (populated on the outcome row)
        "MinutesAfter", "ResultPrice", "MFE", "MAE", "TargetHit", "StopHit",
        "ExecutionFeasible", "SessionResult",
    ]

    def __init__(self, path="reports/expected_range_paper_signals.csv"):
        self.path = Path(path)

    def _existing(self):
        if not self.path.is_file():
            return []
        with self.path.open(encoding="utf-8") as f:
            import csv
            return [dict(r) for r in csv.DictReader(f)]

    @staticmethod
    def signal_hash(symbol, scenario, decision_time, entry):
        h = hashlib.sha256(f"{symbol}|{scenario}|{decision_time}|{entry}".encode())
        return h.hexdigest()[:16]

    def record_signal(self, row: dict, enabled: bool):
        """Append an immutable pre-outcome signal. No-op unless paper is enabled."""
        if not enabled:
            return None
        sh = self.signal_hash(row.get("Symbol"), row.get("Scenario"),
                              row.get("DecisionTime"), row.get("Entry"))
        existing = self._existing()
        if any(r.get("SignalHash") == sh and r.get("RecordKind") == "SIGNAL" for r in existing):
            return sh  # idempotent: never duplicate or rewrite a signal
        out = {k: "" for k in self.FIELDS}
        out.update(row)
        out["SignalHash"] = sh
        out["RecordKind"] = "SIGNAL"
        out["AppendedAt"] = datetime.now(timezone.utc).astimezone().isoformat()
        self._write(existing + [out])
        return sh

    def append_outcome(self, signal_hash: str, outcome: dict):
        """Append a SEPARATE outcome row; the original signal is untouched."""
        existing = self._existing()
        out = {k: "" for k in self.FIELDS}
        out.update(outcome)
        out["SignalHash"] = signal_hash
        out["RecordKind"] = "OUTCOME"
        out["AppendedAt"] = datetime.now(timezone.utc).astimezone().isoformat()
        self._write(existing + [out])

    def _write(self, rows):
        import csv
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=self.FIELDS)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in self.FIELDS})


def _f(value):
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
