"""Safe paper forward recorder for EXPECTED_RANGE_SCALPER.

Records unbiased forward evidence: an immutable pre-session snapshot, every
scenario state transition (WAIT / READY / rejected — not only READY, to prevent
selection bias), an immutable decision-time snapshot when a scenario first turns
READY (one per activation cycle, duplicate-suppressed), and outcomes computed
from genuine CHRONOLOGICAL Rubix quote events (never inferred from daily High/Low).

Hard safety: records only. Never places an order, never auto-executes, never
enables production. Honors EGX Cairo phases — no NEW continuous-session entry
after 14:15; auction (14:15-14:25) activity is stored separately and never treated
as ordinary breakout confirmation. Fixed +2% target / -2% stop; range percentiles,
score weights, TP and SL are unchanged.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid
from zoneinfo import ZoneInfo

import pandas as pd

from core.egx_session import AUCTION_END, CONTINUOUS_CLOSE, cairo_now
from providers.symbol_mapping import to_rubix_symbol
from scalping_expected_range.scenario_engine import LiveQuote, evaluate_scenarios

CAIRO = "Africa/Cairo"

# Canonical scenario names for recording (task vocabulary).
CANONICAL_SCENARIOS = [
    "EXPECTED_LOWER_RANGE_BOUNCE", "DIP_AND_RECLAIM", "TREND_CONTINUATION",
    "EXPECTED_RANGE_BREAKOUT_RETEST", "GAP_UP_REMAINING_ROOM", "GAP_DOWN_RECOVERY",
    "RANGE_CONSUMED_NO_CHASE",
]
ENGINE_TO_CANONICAL = {
    "EXPECTED_LOWER_RANGE_BOUNCE": "EXPECTED_LOWER_RANGE_BOUNCE",
    "DIP_AND_RECLAIM": "DIP_AND_RECLAIM",
    "TREND_CONTINUATION_INSIDE_RANGE": "TREND_CONTINUATION",
    "EXPECTED_RANGE_BREAKOUT_AND_RETEST": "EXPECTED_RANGE_BREAKOUT_RETEST",
    "GAP_UP_WITH_REMAINING_ROOM": "GAP_UP_REMAINING_ROOM",
    "GAP_DOWN_RECOVERY": "GAP_DOWN_RECOVERY",
    "NO_CHASE_RANGE_CONSUMED": "RANGE_CONSUMED_NO_CHASE",
}

READY_STATES = {"READY"}


def normalize_state(status: str) -> str:
    s = (status or "").upper()
    if s in ("BOUNCE_READY", "READY"):
        return "READY"
    if s in ("BOUNCE_WAIT", "LOWER_RANGE_NOT_REACHED", "FALLING_WITHOUT_CONFIRMATION"):
        return "WAIT"
    if s in ("DATA_STALE",):
        return "DATA_STALE"
    if s in ("DATA_INSUFFICIENT",):
        return "DATA_INSUFFICIENT"
    if s in ("SPREAD_TOO_WIDE",):
        return "SPREAD_TOO_WIDE"
    if s in ("LIQUIDITY_TOO_LOW",):
        return "LIQUIDITY_TOO_LOW"
    if s in ("RANGE_CONSUMED",):
        return "RANGE_CONSUMED"
    if s in ("NO_TRADE",):
        return "NO_TRADE"
    if s in ("INVALID",):
        return "INVALID"
    return "WAIT"


# --- report schemas ---------------------------------------------------------

SIGNAL_FIELDS = [
    "SignalUUID", "SessionDate", "Symbol", "Scenario", "ActivationCycle",
    "DecisionTimestampCairo", "ReceiptTimestamp", "ExchangeTimestamp",
    "Last", "Bid", "Ask", "Spread%", "QuoteAgeSec", "SessionCumulativeVolume",
    "EntryTrigger", "EntryPrice", "FixedTargetPrice", "FixedStopPrice",
    "EstimatedCosts%", "NetTarget%", "NetRisk%", "NetRR",
    "CoreLow", "CoreHigh", "ExpansionLow", "ExpansionHigh", "ExtremeLow", "ExtremeHigh",
    "RangePosition%", "RangeConsumed%", "RoomToCoreHigh%", "RoomToExpansionHigh%",
    "RoomToExtremeHigh%", "HistoricalRank", "LiquidityScore", "VolatilityScore",
    "TotalScore", "ConfirmationEvidence", "InvalidationReason",
    "HistoricalDataThrough", "DatasetHash", "StrategyVersion",
]

TRANSITION_FIELDS = [
    "SessionDate", "Symbol", "Scenario", "FromState", "ToState", "ActivationCycle",
    "TransitionAtCairo", "Advisory", "Note",
]

OUTCOME_FIELDS = [
    "SignalUUID", "SessionDate", "Symbol", "Scenario", "ActivationCycle",
    "ExecutableEntryAvailable", "EntryDelaySec", "EntryPrice", "ActualSpread%",
    "SlippageEstimate%", "MFE%", "MAE%", "TargetHit", "StopHit", "FirstHit",
    "TimeToTargetSec", "TimeToStopSec", "NeitherHit", "SessionCloseResult%",
    "AuctionResult%", "OutcomeMaturity", "DataQualityWarning",
] + [f"Ret{h}minPct" for h in (1, 3, 5, 10, 20)] \
  + [f"Ret{h}minStatus" for h in (1, 3, 5, 10, 20)]


@dataclass
class SessionState:
    """In-memory reconstruction of per-(symbol,scenario) state for a session."""
    current: dict = field(default_factory=dict)          # (sym,scn) -> state
    cycle: dict = field(default_factory=dict)            # (sym,scn) -> activation cycle
    ready_recorded: set = field(default_factory=set)     # (sym,scn,cycle)


class PaperRecorder:
    def __init__(self, config, session_date, rubix_db_path=None, now=None, holidays=(),
                 output_root=None, reports_root=None):
        self.cfg = config
        self.session_date = session_date            # ISO date string
        self.now = now
        self.holidays = tuple(holidays or ())
        from config.settings_manager import settings
        market = settings.get("market_data")
        self.rubix_path = Path(rubix_db_path or market.get(
            "rubix_db_path", "data/rubix_live_market.db"))
        self.root = Path(output_root or getattr(config, "paper_output_root",
                                                "reports/expected_range_paper"))
        self.reports = Path(reports_root or "reports")
        self.session_dir = self.root / session_date
        # rolling reports
        self.signals_csv = self.reports / "expected_range_paper_signals.csv"
        self.outcomes_csv = self.reports / "expected_range_paper_outcomes.csv"
        self.states_csv = self.reports / "expected_range_scenario_states.csv"
        self.summary_csv = self.reports / "expected_range_daily_paper_summary.csv"
        self.log_lines = []

    # -- guards -----------------------------------------------------------

    def _assert_safe(self):
        assert self.cfg.paper_enabled, "paper_enabled must be true to record"
        assert not self.cfg.production_enabled, "production must remain disabled"
        assert not getattr(self.cfg, "automatic_execution", False), "auto-exec must be off"
        assert not getattr(self.cfg, "broker_orders_enabled", False), "broker orders must be off"

    def _log(self, msg):
        self.log_lines.append(f"{datetime.now(timezone.utc).isoformat()}  {msg}")

    # -- pre-session snapshot (immutable) ---------------------------------

    def write_presession_snapshot(self, universe: pd.DataFrame, historical_through):
        self.session_dir.mkdir(parents=True, exist_ok=True)
        path = self.session_dir / "pre_session_snapshot.csv"
        if path.is_file() and path.stat().st_size > 0:
            self._log(f"pre-session snapshot already exists (immutable): {path}")
            return path, self._dataset_hash_from_file(path)

        dataset_hash = self._dataset_hash(universe, historical_through)
        rows = []
        for _, r in universe.iterrows():
            rows.append({
                "SessionDate": self.session_date,
                "HistoricalDataThrough": r.get("prov_latest_completed_session"),
                "Provider": r.get("prov_historical_provider"),
                "DatasetHash": dataset_hash,
                "Symbol": r.get("Symbol"),
                "CombinedRank": r.get("Rank"),
                "RawAverageVolumeRank": r.get("_RawVolumeRank"),
                "TurnoverRank": r.get("_TurnoverRank"),
                "AverageVolume20": r.get("liq_avg_volume_20"),
                "MedianVolume20": r.get("liq_median_volume_20"),
                "AverageTurnover20": r.get("liq_avg_turnover_egp_20"),
                "MedianTurnover20": r.get("liq_median_turnover_egp_20"),
                "VolumeConsistency": r.get("liq_volume_consistency"),
                "ADR%": r.get("vol_adr_percent_20"),
                "ATR%": r.get("vol_atr_percent_14"),
                "TwoPctRangeFrequency": r.get("vol_target_2pct_frequency"),
                "TwoPctUpsideFrequency": r.get("vol_upside_2pct_frequency"),
                "CoreLow": r.get("er_base_expected_low"), "CoreHigh": r.get("er_base_expected_high"),
                "ExpansionLow": r.get("er_high_volatility_expected_low"),
                "ExpansionHigh": r.get("er_high_volatility_expected_high"),
                "ExtremeLow": r.get("er_extreme_expected_low"),
                "ExtremeHigh": r.get("er_extreme_expected_high"),
                "TotalScalpingScore": r.get("EXPECTED_RANGE_SCALPING_SCORE"),
                "HardGateStatus": r.get("liq_status"),
                "RejectionReasons": r.get("liq_reasons"),
            })
        pd.DataFrame(rows).to_csv(path, index=False)
        self._log(f"wrote immutable pre-session snapshot ({len(rows)} rows) hash={dataset_hash}")
        return path, dataset_hash

    # -- one evaluation cycle --------------------------------------------

    def evaluate_cycle(self, analyses, universe, live_quote_fn, dataset_hash,
                       historical_through):
        """Evaluate every scenario for every symbol and record states/transitions."""

        self._assert_safe()
        now = cairo_now(self.now)
        after_1415 = now.timetz().replace(tzinfo=None) >= CONTINUOUS_CLOSE
        state = self._load_session_state()
        rank_map = {r["Symbol"]: r for _, r in universe.iterrows()}

        transitions, new_signals, rejected = [], [], []
        for symbol, analysis in analyses.items():
            live = live_quote_fn(symbol)
            ev = evaluate_scenarios(analysis, live, self.cfg)
            per_scn = self._states_for_symbol(ev)
            uni = rank_map.get(symbol, {})
            for canonical, (state_name, scn_obj, advisory) in per_scn.items():
                key = (symbol, canonical)
                prev = state.current.get(key, "INIT")
                if state_name != prev:
                    cycle = state.cycle.get(key, 0)
                    note = ""
                    if state_name == "READY" and prev not in READY_STATES:
                        cycle += 1                      # genuine new activation
                        state.cycle[key] = cycle
                    transitions.append({
                        "SessionDate": self.session_date, "Symbol": symbol,
                        "Scenario": canonical, "FromState": prev, "ToState": state_name,
                        "ActivationCycle": cycle, "TransitionAtCairo": now.isoformat(),
                        "Advisory": advisory, "Note": note,
                    })
                    state.current[key] = state_name
                cycle = state.cycle.get(key, 0)

                # decision-time READY snapshot: once per activation cycle, before 14:15
                if state_name == "READY":
                    sig_key = (symbol, canonical, cycle)
                    if sig_key not in state.ready_recorded:
                        if after_1415:
                            self._log(f"{symbol}/{canonical} READY after 14:15 — no new entry recorded")
                            transitions.append({
                                "SessionDate": self.session_date, "Symbol": symbol,
                                "Scenario": canonical, "FromState": "READY", "ToState": "READY",
                                "ActivationCycle": cycle, "TransitionAtCairo": now.isoformat(),
                                "Advisory": advisory, "Note": "NO_NEW_ENTRY_AFTER_1415",
                            })
                        else:
                            sig = self._build_signal(symbol, canonical, cycle, scn_obj, ev,
                                                     live, uni, now, dataset_hash,
                                                     historical_through)
                            new_signals.append(sig)
                        state.ready_recorded.add(sig_key)
                else:
                    rejected.append({
                        "SessionDate": self.session_date, "Symbol": symbol,
                        "Scenario": canonical, "State": state_name, "Advisory": advisory,
                        "Reason": getattr(scn_obj, "reason", ""),
                        "DataWarning": ev.data_warning, "AtCairo": now.isoformat(),
                    })

        self._append(self.states_csv, TRANSITION_FIELDS, transitions)
        self._write_session_csv("scenario_transitions.csv", TRANSITION_FIELDS, transitions, append=True)
        self._write_session_csv("rejected_scenarios.csv",
                                list(rejected[0].keys()) if rejected else
                                ["SessionDate", "Symbol", "Scenario", "State", "Advisory",
                                 "Reason", "DataWarning", "AtCairo"], rejected, append=True)
        if new_signals:
            self._append(self.signals_csv, SIGNAL_FIELDS, new_signals)
            self._write_session_csv("ready_signals.csv", SIGNAL_FIELDS, new_signals, append=True)
        self._log(f"cycle: {len(transitions)} transitions, {len(new_signals)} new READY signals, "
                  f"{len(rejected)} non-ready states (after_1415={after_1415})")
        return {"transitions": transitions, "signals": new_signals, "rejected": rejected}

    def _states_for_symbol(self, ev):
        """Map an evaluation to a state per canonical scenario (all 7 always present)."""
        out = {c: ("WAIT", None, ev.advisory) for c in CANONICAL_SCENARIOS}
        gate = ev.scenarios[0] if (len(ev.scenarios) == 1) else None
        if gate is not None and normalize_state(gate.status) in (
                "DATA_STALE", "DATA_INSUFFICIENT", "SPREAD_TOO_WIDE", "LIQUIDITY_TOO_LOW"):
            gstate = normalize_state(gate.status)
            return {c: (gstate, gate, ev.advisory) for c in CANONICAL_SCENARIOS}
        for scn in ev.scenarios:
            canonical = ENGINE_TO_CANONICAL.get(scn.name)
            if canonical:
                out[canonical] = (normalize_state(scn.status), scn, ev.advisory)
        return out

    def _build_signal(self, symbol, canonical, cycle, scn, ev, live, uni, now,
                      dataset_hash, historical_through):
        return {
            "SignalUUID": str(uuid.uuid4()), "SessionDate": self.session_date,
            "Symbol": symbol, "Scenario": canonical, "ActivationCycle": cycle,
            "DecisionTimestampCairo": now.isoformat(),
            "ReceiptTimestamp": getattr(live, "received_at", None),
            "ExchangeTimestamp": getattr(live, "exchange_timestamp", None),
            "Last": live.last, "Bid": live.bid, "Ask": live.ask,
            "Spread%": live.spread_percent, "QuoteAgeSec": live.quote_age_seconds,
            "SessionCumulativeVolume": live.volume,
            "EntryTrigger": scn.entry_trigger, "EntryPrice": scn.entry_trigger,
            "FixedTargetPrice": scn.take_profit, "FixedStopPrice": scn.stop_loss,
            "EstimatedCosts%": _costs(self.cfg, live.spread_percent),
            "NetTarget%": scn.net_profit_percent, "NetRisk%": scn.net_loss_percent,
            "NetRR": scn.net_rr,
            "CoreLow": uni.get("er_base_expected_low"), "CoreHigh": uni.get("er_base_expected_high"),
            "ExpansionLow": uni.get("er_high_volatility_expected_low"),
            "ExpansionHigh": uni.get("er_high_volatility_expected_high"),
            "ExtremeLow": uni.get("er_extreme_expected_low"),
            "ExtremeHigh": uni.get("er_extreme_expected_high"),
            "RangePosition%": scn.range_position_percent, "RangeConsumed%": ev.range_consumed_percent,
            "RoomToCoreHigh%": ev.remaining_upside_base_percent,
            "RoomToExpansionHigh%": ev.remaining_upside_highvol_percent,
            "RoomToExtremeHigh%": _room(live.last, uni.get("er_extreme_expected_high")),
            "HistoricalRank": uni.get("Rank"),
            "LiquidityScore": uni.get("AvgVolumeScore"), "VolatilityScore": uni.get("VolatilityScore"),
            "TotalScore": uni.get("EXPECTED_RANGE_SCALPING_SCORE"),
            "ConfirmationEvidence": scn.historical_evidence, "InvalidationReason": scn.reason,
            "HistoricalDataThrough": historical_through, "DatasetHash": dataset_hash,
            "StrategyVersion": self.cfg.strategy_version,
        }

    # -- outcomes (chronological Rubix events only) -----------------------

    def record_outcomes(self, signals=None):
        """Compute outcomes for READY signals from chronological quotes after decision."""
        rows_signals = signals if signals is not None else self._read(self.signals_csv)
        session_signals = [r for r in rows_signals if r.get("SessionDate") == self.session_date]
        existing = {r.get("SignalUUID") for r in self._read(self.outcomes_csv)}
        outcomes = []
        for sig in session_signals:
            if sig.get("SignalUUID") in existing:
                continue                                # immutable: never recompute
            oc = self._outcome_for(sig)
            if oc:
                outcomes.append(oc)
        if outcomes:
            self._append(self.outcomes_csv, OUTCOME_FIELDS, outcomes)
            self._write_session_csv("outcomes.csv", OUTCOME_FIELDS, outcomes, append=True)
        self._log(f"outcomes: computed {len(outcomes)} (chronological quotes only)")
        return outcomes

    def _outcome_for(self, sig, finalize=True):
        entry = _f(sig.get("EntryPrice")) or _f(sig.get("Last"))
        target = _f(sig.get("FixedTargetPrice"))
        stop = _f(sig.get("FixedStopPrice"))
        decision = pd.to_datetime(sig.get("DecisionTimestampCairo"), utc=True, errors="coerce")
        if entry is None or target is None or stop is None or pd.isna(decision):
            return None
        continuous_end = self._continuous_end_utc()
        now_utc = cairo_now(self.now).astimezone(timezone.utc)
        quotes = self._quotes_after(sig.get("Symbol"), decision)
        if quotes.empty:
            base_maturity = "DATA_INSUFFICIENT"
            hz = {f"Ret{h}minStatus": self._horizon_status(decision, h, continuous_end, None, now_utc)
                  for h in (1, 3, 5, 10, 20)}
            return {**_blank_outcome(sig), "ExecutableEntryAvailable": "No",
                    "DataQualityWarning": "NO_QUOTES_AFTER_DECISION",
                    "OutcomeMaturity": base_maturity, "NeitherHit": None, **hz}
        cont, auction = self._split_auction(quotes)
        base = cont if not cont.empty else quotes
        first = base.iloc[0]
        entry_delay = (first["ts"] - decision).total_seconds()
        px = base["last_price"].astype(float)
        mfe = float((px.max() - entry) / entry * 100.0)
        mae = float((px.min() - entry) / entry * 100.0)
        t_hit = base[px >= target]
        s_hit = base[px <= stop]
        tt = (t_hit["ts"].iloc[0] - decision).total_seconds() if not t_hit.empty else None
        ts = (s_hit["ts"].iloc[0] - decision).total_seconds() if not s_hit.empty else None
        first_hit = "NEITHER"
        if tt is not None and (ts is None or tt <= ts):
            first_hit = "TARGET"
        elif ts is not None:
            first_hit = "STOP"
        out = {**_blank_outcome(sig),
               "ExecutableEntryAvailable": "Yes" if (first.get("bid") and first.get("ask")) else "No",
               "EntryDelaySec": round(entry_delay, 1),
               "EntryPrice": round(float(first["last_price"]), 4),
               "ActualSpread%": _spread(first),
               "SlippageEstimate%": round(abs(float(first["last_price"]) - entry) / entry * 100.0, 4),
               "MFE%": round(mfe, 4), "MAE%": round(mae, 4),
               "TargetHit": not t_hit.empty, "StopHit": not s_hit.empty, "FirstHit": first_hit,
               "TimeToTargetSec": round(tt, 1) if tt is not None else None,
               "TimeToStopSec": round(ts, 1) if ts is not None else None,
               "NeitherHit": t_hit.empty and s_hit.empty,
               "SessionCloseResult%": round(float((px.iloc[-1] - entry) / entry * 100.0), 4),
               "AuctionResult%": (round(float((auction["last_price"].astype(float).iloc[-1] - entry)
                                              / entry * 100.0), 4) if not auction.empty else None),
               "OutcomeMaturity": ("FINALIZED" if finalize else "PENDING"),
               "DataQualityWarning": ""}
        last_ts = base["ts"].iloc[-1]
        for h in (1, 3, 5, 10, 20):
            status = self._horizon_status(decision, h, continuous_end, last_ts, now_utc)
            out[f"Ret{h}minStatus"] = status
            if status == "MATURED":
                cutoff = decision + timedelta(minutes=h)
                upto = base[base["ts"] <= cutoff]
                out[f"Ret{h}minPct"] = (round(float((float(upto["last_price"].iloc[-1]) - entry)
                                                     / entry * 100.0), 4) if not upto.empty else None)
            else:
                out[f"Ret{h}minPct"] = None      # never record a still-forming horizon
        return out

    def _continuous_end_utc(self):
        d = datetime.fromisoformat(self.session_date).date()
        return datetime.combine(d, CONTINUOUS_CLOSE, tzinfo=ZoneInfo(CAIRO)).astimezone(timezone.utc)

    @staticmethod
    def _horizon_status(decision, horizon_minutes, continuous_end, last_quote_ts, now_utc):
        from scalping_expected_range.orchestration import horizon_status
        return horizon_status(decision, horizon_minutes, continuous_end, last_quote_ts, now_utc)

    def _quotes_after(self, ticker, decision):
        mapped = to_rubix_symbol(ticker)
        if not self.rubix_path.is_file():
            return pd.DataFrame()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT last_price,bid,ask,volume,market_timestamp,received_at "
                "FROM quotes WHERE UPPER(ticker)=? AND received_at > ? ORDER BY received_at",
                (mapped.upper(), decision.isoformat())).fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame([dict(r) for r in rows])
        df["ts"] = pd.to_datetime(df["received_at"], utc=True, errors="coerce")
        return df.dropna(subset=["ts"]).sort_values("ts")

    def _split_auction(self, quotes):
        cairo = quotes["ts"].dt.tz_convert(CAIRO)
        tod = cairo.dt.time
        is_auction = (tod >= CONTINUOUS_CLOSE) & (tod < AUCTION_END)
        return quotes[~is_auction], quotes[is_auction]

    # -- session summary --------------------------------------------------

    def write_session_summary(self, universe, cycle_result, outcomes):
        self.session_dir.mkdir(parents=True, exist_ok=True)
        signals = cycle_result["signals"]
        by_scn = {}
        for t in cycle_result["transitions"]:
            by_scn.setdefault(t["Scenario"], {}).setdefault(t["ToState"], 0)
            by_scn[t["Scenario"]][t["ToState"]] += 1
        target_first = sum(1 for o in outcomes if o.get("FirstHit") == "TARGET")
        stop_first = sum(1 for o in outcomes if o.get("FirstHit") == "STOP")
        neither = sum(1 for o in outcomes if o.get("NeitherHit"))
        summary = {
            "session_date": self.session_date,
            "strategy_version": self.cfg.strategy_version,
            "paper_enabled": self.cfg.paper_enabled,
            "production_enabled": self.cfg.production_enabled,
            "automatic_execution": self.cfg.automatic_execution,
            "symbols_analyzed": int(len(universe)),
            "ready_signals": len(signals),
            "executable_signals": sum(1 for o in outcomes if o.get("ExecutableEntryAvailable") == "Yes"),
            "target_first": target_first, "stop_first": stop_first, "neither_hit": neither,
            "transitions_by_scenario_state": by_scn,
            "data_quality_failures": sum(1 for t in cycle_result["transitions"]
                                         if t["ToState"] in ("DATA_STALE", "DATA_INSUFFICIENT")),
        }
        (self.session_dir / "session_summary.json").write_text(
            json.dumps(summary, indent=2, default=str), encoding="utf-8")
        (self.session_dir / "validation_log.txt").write_text(
            "\n".join(self.log_lines), encoding="utf-8")
        self._append_summary_row(summary)
        return summary

    def _append_summary_row(self, summary):
        fields = ["SessionDate", "StrategyVersion", "SymbolsAnalyzed", "ReadySignals",
                  "ExecutableSignals", "TargetFirst", "StopFirst", "NeitherHit",
                  "DataQualityFailures", "PaperEnabled", "ProductionEnabled"]
        row = {"SessionDate": summary["session_date"], "StrategyVersion": summary["strategy_version"],
               "SymbolsAnalyzed": summary["symbols_analyzed"], "ReadySignals": summary["ready_signals"],
               "ExecutableSignals": summary["executable_signals"], "TargetFirst": summary["target_first"],
               "StopFirst": summary["stop_first"], "NeitherHit": summary["neither_hit"],
               "DataQualityFailures": summary["data_quality_failures"],
               "PaperEnabled": summary["paper_enabled"], "ProductionEnabled": summary["production_enabled"]}
        existing = [r for r in self._read(self.summary_csv) if r.get("SessionDate") != self.session_date]
        self._overwrite(self.summary_csv, fields, existing + [row])

    # -- state reconstruction --------------------------------------------

    def _load_session_state(self) -> SessionState:
        st = SessionState()
        for r in self._read(self.states_csv):
            if r.get("SessionDate") != self.session_date:
                continue
            key = (r.get("Symbol"), r.get("Scenario"))
            st.current[key] = r.get("ToState")
            try:
                st.cycle[key] = max(st.cycle.get(key, 0), int(float(r.get("ActivationCycle") or 0)))
            except (TypeError, ValueError):
                pass
        for r in self._read(self.signals_csv):
            if r.get("SessionDate") != self.session_date:
                continue
            try:
                st.ready_recorded.add((r.get("Symbol"), r.get("Scenario"),
                                       int(float(r.get("ActivationCycle") or 0))))
            except (TypeError, ValueError):
                pass
        return st

    # -- io helpers -------------------------------------------------------

    def _dataset_hash(self, universe, historical_through):
        h = hashlib.sha256()
        h.update(str(historical_through).encode())
        h.update(str(len(universe)).encode())
        for _, r in universe.iterrows():
            h.update(f"{r.get('Symbol')}:{r.get('er_base_expected_low')}:{r.get('er_base_expected_high')}".encode())
        return h.hexdigest()[:16]

    def _dataset_hash_from_file(self, path):
        rows = self._read(path)
        return rows[0].get("DatasetHash") if rows else None

    def _connect(self):
        uri = f"file:{self.rubix_path.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _read(self, path):
        p = Path(path)
        if not p.is_file():
            return []
        with p.open(encoding="utf-8") as f:
            return [dict(r) for r in csv.DictReader(f)]

    def _append(self, path, fields, rows):
        if not rows:
            return
        existing = self._read(path)
        self._overwrite(path, fields, existing + rows)

    def _overwrite(self, path, fields, rows):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in fields})

    def _write_session_csv(self, name, fields, rows, append=False):
        self.session_dir.mkdir(parents=True, exist_ok=True)
        path = self.session_dir / name
        prior = self._read(path) if (append and path.is_file()) else []
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in prior + rows:
                w.writerow({k: r.get(k, "") for k in fields})


def _costs(cfg, spread):
    spread_frac = (spread or 0.0) / 100.0
    return round((2 * cfg.commission_estimate + spread_frac + 2 * cfg.slippage_estimate) * 100.0, 4)


def _room(last, high):
    last = _f(last); high = _f(high)
    if last and high and last > 0:
        return round((high - last) / last * 100.0, 4)
    return None


def _spread(row):
    bid, ask = _f(row.get("bid")), _f(row.get("ask"))
    if bid and ask and bid > 0 and ask >= bid:
        return round((ask - bid) / ((ask + bid) / 2.0) * 100.0, 4)
    return None


def _blank_outcome(sig):
    return {"SignalUUID": sig.get("SignalUUID"), "SessionDate": sig.get("SessionDate"),
            "Symbol": sig.get("Symbol"), "Scenario": sig.get("Scenario"),
            "ActivationCycle": sig.get("ActivationCycle")}


def _f(v):
    try:
        if v is None or v == "" or pd.isna(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None
