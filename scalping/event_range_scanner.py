"""Shadow Event-Driven Range Scanner (research, paper-only, DISABLED by default).

Runs BOTH gates on real Rubix session data and compares them per symbol:
  A. LEGACY minute-coverage gate (>=60% observed minutes) — unchanged, preserved.
  B. NEW event-driven quality gate (scalping/event_quality.py).

Writes reports/scalping_legacy_vs_event_gate.csv with the exact divergence
reason for every symbol. Never changes production behavior, never fabricates or
forward-fills, never enables trading. Read-only against the Rubix DB.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pandas as pd

from config.settings_manager import settings
from core.symbols import load_symbols
from providers.symbol_mapping import to_rubix_symbol
from scalping.event_quality import (
    EventQualityConfig,
    EventStatus,
    RangeStatus,
    compute_event_quality,
)

LEGACY_MIN_COVERAGE = 0.60   # the preserved legacy gate (NEVER lowered here)
# EGX session phases (Africa/Cairo, UTC+3 in July):
#   Continuous trading : 10:00-14:15  ->  07:00-11:15 UTC   (range is evaluated HERE)
#   Closing auction     : 14:15-14:25  ->  11:15-11:25 UTC   (randomized close ~14:23-14:25)
# Range quality is judged on continuous trading only; auction gaps must not
# invalidate the continuous-session range. Auction close/volume are preserved
# separately for closing-price research.
S_UTC, E_UTC = "07:00:00", "11:15:00"          # continuous-trading window
AUCTION_S_UTC, AUCTION_E_UTC = "11:15:00", "11:25:00"
SESSION_MINUTES = 255                            # 10:00-14:15 = 255 continuous minutes
# The legacy minute-coverage denominator stays on the FULL regular session for
# an apples-to-apples "legacy diagnostic" (never changed); continuous minutes
# drive the new event gate only.
LEGACY_SESSION_MINUTES = 270


class EventRangeShadowScanner:
    def __init__(self, rubix_db_path=None, config=None, now=None):
        market = settings.get("market_data")
        self.rubix_path = Path(rubix_db_path or market.get(
            "rubix_db_path", "data/rubix_live_market.db"))
        # Continuous-trading denominator (255 min = 10:00-14:15) for the event gate.
        self.config = config or EventQualityConfig(session_minutes=SESSION_MINUTES)
        self.now = now

    def _legacy_full_session_coverage(self, conn, session_date, mapped):
        """LEGACY diagnostic, unchanged: distinct active minutes over the FULL
        regular session (10:00-14:30 = 270 min). Preserved for apples-to-apples."""
        n = conn.execute(
            f"SELECT COUNT(DISTINCT substr(market_timestamp,12,5)) FROM quotes "
            f"WHERE UPPER(ticker)=? AND substr(market_timestamp,1,10)=? "
            f"AND substr(market_timestamp,12,8)>='07:00:00' AND substr(market_timestamp,12,8)<='11:30:00'",
            (mapped.upper(), session_date)).fetchone()[0]
        return round((n or 0) / LEGACY_SESSION_MINUTES, 4)

    def _auction_data(self, conn, session_date, mapped):
        """Preserve closing-auction (14:15-14:25) last price + volume separately.
        Never used for continuous-session range quality — research only."""
        row = conn.execute(
            f"SELECT last_price, volume FROM quotes WHERE UPPER(ticker)=? "
            f"AND substr(market_timestamp,1,10)=? "
            f"AND substr(market_timestamp,12,8)>'{AUCTION_S_UTC}' AND substr(market_timestamp,12,8)<='{AUCTION_E_UTC}' "
            f"ORDER BY market_timestamp DESC LIMIT 1", (mapped.upper(), session_date)).fetchone()
        if row is None:
            return (None, None)
        return (row["last_price"], row["volume"])

    def _connect(self):
        uri = f"file:{self.rubix_path.resolve().as_posix()}?mode=ro"
        c = sqlite3.connect(uri, uri=True, timeout=15)
        c.row_factory = sqlite3.Row
        return c

    def _connection_coverage(self, conn, session_date):
        """Market-wide active minutes / session minutes (Phase 1 type 1)."""
        n = conn.execute(
            f"SELECT COUNT(DISTINCT substr(market_timestamp,12,5)) FROM quotes "
            f"WHERE substr(market_timestamp,1,10)=? AND substr(market_timestamp,12,8)>='{S_UTC}' "
            f"AND substr(market_timestamp,12,8)<='{E_UTC}'", (session_date,)).fetchone()[0]
        return round((n or 0) / SESSION_MINUTES, 4)

    def _connection_outage_intervals(self, conn, session_date, min_outage_seconds=120):
        """Intervals where the market-wide feed went silent (connection down).

        Computed from the distinct market-wide event timeline: any gap between
        consecutive market-wide frames longer than ``min_outage_seconds`` is a
        genuine connection outage affecting every symbol simultaneously.
        """
        ts = [pd.Timestamp(r[0]) for r in conn.execute(
            f"SELECT DISTINCT market_timestamp FROM quotes WHERE substr(market_timestamp,1,10)=? "
            f"AND substr(market_timestamp,12,8)>='{S_UTC}' AND substr(market_timestamp,12,8)<='{E_UTC}' "
            f"ORDER BY market_timestamp", (session_date,)).fetchall()]
        intervals = []
        for i in range(len(ts) - 1):
            if (ts[i + 1] - ts[i]).total_seconds() > min_outage_seconds:
                intervals.append((ts[i], ts[i + 1]))
        return intervals

    def scan(self, session_date, symbols=None):
        symbols = tuple(symbols or load_symbols("data/symbols.csv"))
        now = self.now or datetime.now(timezone.utc)
        rows = []
        # Completed-session boundaries in UTC (never the report runtime).
        session_start = pd.Timestamp(f"{session_date}T{S_UTC}+00:00")
        session_close = pd.Timestamp(f"{session_date}T{E_UTC}+00:00")
        with self._connect() as conn:
            conn_cov = self._connection_coverage(conn, session_date)
            # Outages computed on the CONTINUOUS window only: an outage that
            # runs into the auction is naturally clipped at 14:15 because the
            # continuous timeline ends there.
            outages = self._connection_outage_intervals(conn, session_date)
            for engine_symbol in symbols:
                mapped = to_rubix_symbol(engine_symbol)
                ev = pd.DataFrame([dict(r) for r in conn.execute(
                    f"SELECT market_timestamp, last_price, bid, ask, volume FROM quotes "
                    f"WHERE UPPER(ticker)=? AND substr(market_timestamp,1,10)=? "
                    f"AND substr(market_timestamp,12,8)>='{S_UTC}' AND substr(market_timestamp,12,8)<='{E_UTC}' "
                    f"ORDER BY market_timestamp", (mapped.upper(), session_date)).fetchall()])
                result = compute_event_quality(
                    engine_symbol, ev, connection_coverage=conn_cov, now=now,
                    config=self.config, session_start=session_start,
                    session_close=session_close, connection_outage_intervals=outages)
                legacy_cov = self._legacy_full_session_coverage(conn, session_date, mapped)
                auction_last, auction_vol = self._auction_data(conn, session_date, mapped)
                rows.append(self._row(engine_symbol, result, legacy_cov,
                                      auction_last, auction_vol))
        return pd.DataFrame(rows)

    def _row(self, symbol, r, legacy_cov, auction_last=None, auction_vol=None):
        legacy_pass = legacy_cov >= LEGACY_MIN_COVERAGE
        legacy_status = "PASS" if legacy_pass else "DATA_INSUFFICIENT"
        event_pass = (r.status == EventStatus.EVENT_DATA_VALID
                      and r.range_status == RangeStatus.RANGE_CONFIRMED)
        # Divergence explanation
        if legacy_pass == event_pass:
            divergence = "agree"
        elif event_pass and not legacy_pass:
            divergence = "EVENT_ADMITS_LEGACY_REJECTS (well-observed but <60% minutes)"
        else:
            divergence = "LEGACY_ADMITS_EVENT_REJECTS"
        # Highlight the key case: legacy rejects an otherwise well-observed liquid symbol
        well_observed = (r.status == EventStatus.EVENT_DATA_VALID)
        legacy_rejects_good = (not legacy_pass) and well_observed
        return {
            "Symbol": symbol,
            "LegacyCoverage%": round(legacy_cov * 100, 1),
            "LegacyStatus": legacy_status,
            "ContinuousMinuteCov%": round(r.observed_minute_coverage * 100, 1),
            "ConnectionCoverage%": round((r.connection_coverage or 0) * 100, 1),
            "ActiveEventCoverage": r.active_event_coverage,
            "EventCount": r.event_count,
            "PositiveEvents": r.positive_event_count,
            "DistinctPrices": r.distinct_prices,
            "SpanMin": r.span_minutes,
            "MedianGapS": r.median_gap_seconds,
            "MaxGapS": r.max_gap_seconds,
            "FatalGapS": r.fatal_gap_seconds,
            "MaxGapClass": r.max_gap_classification,
            "TwoSided%": round(r.two_sided_ratio * 100, 1),
            "Spread%": r.spread_percent,
            "TurnoverEGP": r.turnover_egp,
            "EventHigh": r.event_high, "EventLow": r.event_low,
            "EventActivityScore": r.event_activity_score,
            "LiquidityScore": r.liquidity_score,
            "ExecutabilityScore": r.executability_score,
            "RangeConfidenceScore": r.range_confidence_score,
            "EventDataQualityScore": r.event_data_quality_score,
            "AuctionLast": auction_last, "AuctionVolume": auction_vol,
            "EventStatus": r.status.value,
            "RangeStatus": r.range_status.value,
            "ShadowStatus": "SHADOW_PASS" if event_pass else "SHADOW_REJECT",
            "Divergence": divergence,
            "LegacyRejectsWellObserved": "Yes" if legacy_rejects_good else "No",
            "Reason": " | ".join(r.reasons) if r.reasons else "",
        }


def run(session_date, out="reports/scalping_legacy_vs_event_gate.csv"):
    scanner = EventRangeShadowScanner()
    df = scanner.scan(session_date)
    Path(out).parent.mkdir(exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8")
    summary = {
        "session": session_date, "symbols": len(df),
        "legacy_pass": int((df["LegacyStatus"] == "PASS").sum()),
        "event_valid": int((df["EventStatus"] == "EVENT_DATA_VALID").sum()),
        "range_confirmed": int((df["RangeStatus"] == "RANGE_CONFIRMED").sum()),
        "range_partial": int((df["RangeStatus"] == "RANGE_PARTIAL").sum()),
        "shadow_pass": int((df["ShadowStatus"] == "SHADOW_PASS").sum()),
        "legacy_rejects_well_observed": int((df["LegacyRejectsWellObserved"] == "Yes").sum()),
        "report": out,
    }
    return summary, df


if __name__ == "__main__":
    import json
    import sys
    session = sys.argv[1] if len(sys.argv) > 1 else "2026-07-21"
    summary, _ = run(session)
    print(json.dumps(summary, indent=2, default=str))
