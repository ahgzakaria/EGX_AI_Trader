"""Automated post-session Rubix validation core (research, all-disabled).

Consolidates the multi-task audit into one reusable, idempotent analysis:
  * connection / WebSocket health (real outages vs upstream timestamp stalls),
  * Mubasher market_timestamp staleness periods + value-progression semantics,
  * market-wide material-value progression signal (NOT per-symbol),
  * dual Range-Confidence evaluation: Model A (legacy market_timestamp) and
    Model B (shadow value-progression) — same unchanged 300 s threshold,
  * paper-gate eligibility (locked until the multi-session gate passes),
  * multi-session 2-of-3 acceptance state.

Reads the Rubix DB `mode=ro`. Never changes any production threshold, gate,
strategy, entry/stop/target/RR, collector, or provider routing. All flags
(event_gate_enabled / paper_recording_enabled / production_enabled) default
false and are never flipped here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import hashlib
import sqlite3

import pandas as pd

from scalping.event_quality import EventQualityConfig, EventStatus, RangeStatus, compute_event_quality
from scalping.event_paper_gate import paper_recording_permitted

# EGX Cairo session phases (UTC+3 in summer).
CONT_S_UTC, CONT_E_UTC = "07:00:00", "11:15:00"   # continuous trading 10:00-14:15
AUCTION_S_UTC, AUCTION_E_UTC = "11:15:00", "11:25:00"  # closing auction 14:15-14:25
OPEN_GRACE_END_UTC = "07:15:00"                    # opening-auction edge ends 10:15
MIN_GAP = 120
FATAL_LIMIT = 300.0                                # unchanged 300 s threshold
LEGACY_SESSION_MINUTES = 270


@dataclass
class SessionResult:
    session_date: str
    patched_collector: bool
    continuous_uptime_pct: float
    frames: int
    max_frame_gap_s: float | None
    disconnects: int
    reconnects: int
    resubscribe_complete: int
    connection_outages: int
    max_market_timestamp_gap_s: float
    max_material_value_gap_s: float
    value_gap_median_s: float
    value_gap_p90_s: float
    value_gap_p95_s: float
    value_periods_over_120: int
    value_periods_over_300: int
    timestamp_only_freezes: int
    full_state_freezes: int
    event_data_valid: int
    legacy_range_confirmed: int
    value_range_confirmed: int
    range_partial: int
    range_unreliable: int
    paper_opportunities: int
    dataset_hash: str
    staleness_periods: list = field(default_factory=list)
    value_periods: list = field(default_factory=list)
    connection_rows: list = field(default_factory=list)
    legacy_vs_value: list = field(default_factory=list)
    range_candidates: list = field(default_factory=list)
    paper_decisions: list = field(default_factory=list)
    log: list = field(default_factory=list)


def _connect(db_path):
    c = sqlite3.connect(f"file:{Pathish(db_path)}?mode=ro", uri=True, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def Pathish(p):
    from pathlib import Path
    return Path(p).resolve().as_posix()


def _insess(col, s=CONT_S_UTC, e=CONT_E_UTC):
    return f"substr({col},12,8)>='{s}' AND substr({col},12,8)<='{e}'"


def analyze_session(session_date: str, db_path: str) -> SessionResult:
    """Run the full dual-model analysis for one completed session."""

    log = [f"analyze {session_date} @ {datetime.now(timezone.utc).isoformat()}"]
    conn = _connect(db_path)
    q = pd.read_sql_query(
        f"SELECT received_at, market_timestamp, UPPER(ticker) t, last_price, bid, ask, volume "
        f"FROM quotes WHERE substr(market_timestamp,1,10)=? AND {_insess('market_timestamp')} "
        f"ORDER BY received_at", conn, params=(session_date,))
    if q.empty:
        conn.close()
        raise ValueError(f"no continuous-trading quotes for {session_date}")
    q["recv"] = pd.to_datetime(q["received_at"], utc=True, format="ISO8601")
    q["mkt"] = pd.to_datetime(q["market_timestamp"], utc=True, format="ISO8601")
    log.append(f"loaded {len(q)} continuous quotes")

    # Patched-collector marker: the resubscribe_complete metric only exists in the fix.
    resub = conn.execute(
        "SELECT COUNT(*) FROM feed_metrics WHERE event='resubscribe_complete' AND substr(observed_at,1,10)=?",
        (session_date,)).fetchone()[0]
    disc = conn.execute(
        f"SELECT COUNT(*) FROM feed_metrics WHERE event='disconnect' AND substr(observed_at,1,10)=? "
        f"AND {_insess('observed_at')}", (session_date,)).fetchone()[0]
    recon = conn.execute(
        f"SELECT COUNT(*) FROM feed_metrics WHERE event='reconnect_success' AND substr(observed_at,1,10)=? "
        f"AND {_insess('observed_at')}", (session_date,)).fetchone()[0]
    disc_times = [pd.Timestamp(r[0]) for r in conn.execute(
        f"SELECT observed_at FROM feed_metrics WHERE event='disconnect' AND substr(observed_at,1,10)=? "
        f"AND {_insess('observed_at')}", (session_date,)).fetchall()]
    conn.close()

    session_start = pd.Timestamp(f"{session_date}T{CONT_S_UTC}+00:00")
    session_close = pd.Timestamp(f"{session_date}T{CONT_E_UTC}+00:00")
    grace_end = pd.Timestamp(f"{session_date}T{OPEN_GRACE_END_UTC}+00:00")

    # --- material-value change flags (per symbol) ---
    q = q.sort_values(["t", "recv"])
    changed = (
        (q.groupby("t")["last_price"].diff().fillna(0) != 0)
        | (q.groupby("t")["bid"].diff().fillna(0) != 0)
        | (q.groupby("t")["ask"].diff().fillna(0) != 0)
        | (q.groupby("t")["volume"].diff().fillna(0) > 0)
    )
    q["material_change"] = changed
    q = q.sort_values("recv")

    # --- Phase 4: market-wide material-value progression signal ---
    change_ts = q.loc[q["material_change"], "recv"]
    change_ts = change_ts[change_ts >= grace_end].sort_values().tolist()
    vgaps = [(change_ts[i + 1] - change_ts[i]).total_seconds() for i in range(len(change_ts) - 1)]
    vg = pd.Series(vgaps) if vgaps else pd.Series([0.0])
    value_intervals = [(change_ts[i], change_ts[i + 1]) for i in range(len(change_ts) - 1)
                       if (change_ts[i + 1] - change_ts[i]).total_seconds() > MIN_GAP]

    # --- Phase 5: market_timestamp staleness periods, classified by value progression ---
    mts = sorted(q["mkt"].unique())
    ts_intervals_legacy = []
    staleness_periods = []
    ts_only = full_state = conn_outages = 0
    max_ts_gap = 0.0
    for i in range(len(mts) - 1):
        a, b = pd.Timestamp(mts[i]), pd.Timestamp(mts[i + 1])
        dur = (b - a).total_seconds()
        if dur < MIN_GAP:
            continue
        max_ts_gap = max(max_ts_gap, dur)
        in_opening = a < grace_end
        seg = q[(q["recv"] >= a) & (q["recv"] <= b)]
        frames_in = len(seg)
        changed_last = int((seg.groupby("t")["last_price"].diff().fillna(0) != 0).sum())
        changed_bid = int((seg.groupby("t")["bid"].diff().fillna(0) != 0).sum())
        changed_ask = int((seg.groupby("t")["ask"].diff().fillna(0) != 0).sum())
        vol_delta = float(seg.groupby("t")["volume"].apply(lambda s: (s.max() - s.min()) if len(s) else 0).sum())
        payload = seg[["t", "last_price", "bid", "ask", "volume"]].astype(str).agg("|".join, axis=1)
        dup_pct = round((len(seg) - payload.nunique()) / len(seg) * 100, 1) if len(seg) else 0
        had_disc = any(a <= d <= b for d in disc_times)
        # Legacy Model A treats every market_timestamp gap as an outage interval.
        if not in_opening:
            ts_intervals_legacy.append((a, b))
        # classification
        if had_disc and frames_in == 0:
            cls = "CONNECTION_OUTAGE"; conn_outages += 1
        elif in_opening:
            cls = "SESSION_START_EDGE"
        elif changed_last > 0:
            cls = "TIMESTAMP_ONLY_STALE"; ts_only += 1
        elif changed_bid + changed_ask + (1 if vol_delta > 0 else 0) > 0:
            cls = "PARTIAL_VALUE_PROGRESS"; ts_only += 1
        elif dup_pct > 95:
            cls = "DUPLICATE_SNAPSHOT_STREAM"; full_state += 1
        else:
            cls = "FULL_MARKET_STATE_STALE"; full_state += 1
        staleness_periods.append({
            "SessionDate": session_date, "StartCairo": _cairo(a), "EndCairo": _cairo(b),
            "DurationSec": round(dur), "Frames": frames_in,
            "ChangedLast": changed_last, "ChangedBid": changed_bid, "ChangedAsk": changed_ask,
            "VolDelta": round(vol_delta), "DuplicatePct": dup_pct,
            "Symbols": seg["t"].nunique(), "HadDisconnect": "yes" if had_disc else "no",
            "Classification": cls,
        })

    value_periods = [{
        "SessionDate": session_date, "StartCairo": _cairo(a), "EndCairo": _cairo(b),
        "DurationSec": round((b - a).total_seconds()),
    } for a, b in value_intervals]

    # --- Phase 6: dual Range-Confidence, per symbol ---
    now = q["recv"].max()
    cfg = EventQualityConfig(session_minutes=255, min_turnover_egp=500_000.0)
    legacy_rc = value_rc = ev_valid = partial = unreliable = 0
    legacy_vs_value = []
    range_candidates = []
    for t, g in q.groupby("t"):
        events = g.rename(columns={"last_price": "last_price"})[
            ["market_timestamp", "last_price", "bid", "ask", "volume"]].copy()
        rA = compute_event_quality(t, events, now=now, config=cfg,
                                   session_start=session_start, session_close=session_close,
                                   connection_outage_intervals=ts_intervals_legacy)
        rB = compute_event_quality(t, events, now=now, config=cfg,
                                   session_start=session_start, session_close=session_close,
                                   connection_outage_intervals=value_intervals)
        if rA.status == EventStatus.EVENT_DATA_VALID:
            ev_valid += 1
        a_conf = rA.range_status == RangeStatus.RANGE_CONFIRMED
        b_conf = rB.range_status == RangeStatus.RANGE_CONFIRMED
        legacy_rc += int(a_conf and rA.status == EventStatus.EVENT_DATA_VALID)
        value_rc += int(b_conf and rB.status == EventStatus.EVENT_DATA_VALID)
        # partial/unreliable counted on the shadow (approved-signal) model
        partial += int(rB.range_status == RangeStatus.RANGE_PARTIAL)
        unreliable += int(rB.range_status == RangeStatus.RANGE_UNRELIABLE)
        legacy_vs_value.append({
            "Symbol": t, "EventStatus": rA.status.value,
            "LegacyRangeStatus": rA.range_status.value, "LegacyFatalGapS": rA.fatal_gap_seconds,
            "ValueRangeStatus": rB.range_status.value, "ValueFatalGapS": rB.fatal_gap_seconds,
            "EventQualityScore": rB.event_data_quality_score,
            "RangeConfidenceScore": rB.range_confidence_score,
            "Divergence": ("VALUE_ADMITS_LEGACY_REJECTS" if (b_conf and not a_conf)
                           else "agree" if a_conf == b_conf else "LEGACY_ADMITS_VALUE_REJECTS"),
        })
        if b_conf and rB.status == EventStatus.EVENT_DATA_VALID:
            range_candidates.append({
                "Symbol": t, "EventQualityScore": rB.event_data_quality_score,
                "RangeConfidenceScore": rB.range_confidence_score,
                "Spread%": rB.spread_percent, "TurnoverEGP": rB.turnover_egp,
                "EventHigh": rB.event_high, "EventLow": rB.event_low,
            })

    dataset_hash = _dataset_hash(q)
    # Continuous uptime = distinct continuous minutes with >=1 received frame / 255.
    active_min = q["recv"].dt.strftime("%H:%M").nunique()
    uptime = round(min(100.0, active_min / 255 * 100), 1)
    log.append(f"ModelA RANGE_CONFIRMED={legacy_rc}  ModelB RANGE_CONFIRMED={value_rc}  EVENT_DATA_VALID={ev_valid}")

    return SessionResult(
        session_date=session_date, patched_collector=(resub > 0),
        continuous_uptime_pct=uptime,
        frames=len(q), max_frame_gap_s=_max_frame_gap(q["recv"]),
        disconnects=disc, reconnects=recon, resubscribe_complete=resub,
        connection_outages=conn_outages, max_market_timestamp_gap_s=round(max_ts_gap, 1),
        max_material_value_gap_s=round(float(vg.max()), 1),
        value_gap_median_s=round(float(vg.median()), 1),
        value_gap_p90_s=round(float(vg.quantile(0.9)), 1),
        value_gap_p95_s=round(float(vg.quantile(0.95)), 1),
        value_periods_over_120=int((vg > 120).sum()),
        value_periods_over_300=int((vg > 300).sum()),
        timestamp_only_freezes=ts_only, full_state_freezes=full_state,
        event_data_valid=ev_valid, legacy_range_confirmed=legacy_rc,
        value_range_confirmed=value_rc, range_partial=partial, range_unreliable=unreliable,
        paper_opportunities=0,  # locked until multi-session gate passes (Phase 7/9)
        dataset_hash=dataset_hash, staleness_periods=staleness_periods,
        value_periods=value_periods,
        connection_rows=[{
            "SessionDate": session_date, "Frames": len(q),
            "MaxFrameGapSec": _max_frame_gap(q["recv"]), "Disconnects": disc,
            "Reconnects": recon, "ResubscribeComplete": resub,
            "ConnectionOutages": conn_outages, "PatchedCollector": resub > 0,
        }],
        legacy_vs_value=legacy_vs_value, range_candidates=range_candidates, log=log,
    )


def _max_frame_gap(recv_series):
    ts = recv_series.sort_values().tolist()
    if len(ts) < 2:
        return None
    return round(max((ts[i + 1] - ts[i]).total_seconds() for i in range(len(ts) - 1)), 1)


def _cairo(ts):
    return (pd.Timestamp(ts) + pd.Timedelta(hours=3)).strftime("%H:%M:%S")


def _dataset_hash(q):
    h = hashlib.sha256()
    h.update(str(len(q)).encode())
    h.update(str(q["recv"].min()).encode())
    h.update(str(q["recv"].max()).encode())
    h.update(str(float(q["last_price"].fillna(0).sum())).encode())
    return h.hexdigest()[:16]


# --- multi-session acceptance (Phase 9) ---
def multisession_state(patched_rows):
    """Apply the 2-of-3 rule to patched sessions. Never approves trading.

    Rows are deduplicated by Session Date (a --force-rebuild adds a higher Run
    Version for the same date; only the latest counts) so re-processing one
    session can never inflate the multi-session count.
    """

    patched = [r for r in patched_rows if str(r.get("Patched Collector")).lower() in ("true", "1", "yes")]
    latest = {}
    for r in patched:
        d = r.get("Session Date")
        if d not in latest or _int(r.get("Run Version")) >= _int(latest[d].get("Run Version")):
            latest[d] = r
    patched = list(latest.values())
    n = len(patched)
    qualifying = sum(1 for r in patched if _int(r.get("Value-Progression RANGE_CONFIRMED")) > 0)
    if n < 3:
        return "WAITING_FOR_SESSIONS", f"{n}/3 patched sessions; {qualifying} with value RANGE_CONFIRMED"
    if qualifying >= 2:
        return "PAPER_RECORDING_ELIGIBLE", f"{n} sessions, {qualifying} qualifying (>=2 of 3)"
    return "MULTISESSION_FAILED", f"{n} sessions but only {qualifying} qualifying (<2)"


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0
