"""Event-specific volume normalization for EODHD daily history.

Replaces the previous *universal* ``Volume = Raw Volume x Split Factor`` transform,
which was proven wrong by the completed corporate-action reconciliation: EGX "splits"
reported by EODHD are a mix of true share-count splits, bonus / capital-increase events,
and provider-disagreement cases whose volume convention is NOT a blanket multiply.

Volume handling is therefore **event-specific** and driven by the validated
``reports/eodhd/corporate_action_reconciliation.csv``, never by the EODHD action name:

  * MULTIPLY_BY_FACTOR       — validated true split; pre-event volume is multiplied by the
                               split ratio so the share-count domain is continuous.
  * KEEP_RAW                 — bonus / capital increase; volume is NOT multiplied.
  * PROVIDER_ALREADY_ADJUSTED— provider already delivered adjusted volume (not used for
                               EODHD raw, reserved for seed provenance).
  * EVENT_SPECIFIC           — convention proven to be neither a clean multiply nor raw;
                               we do not guess.
  * UNRESOLVED               — a split with no validated reconciliation entry.

Safety rule (the operational point): the served volume is **raw by default** and only
true, validated splits contribute a multiply factor. When an EVENT_SPECIFIC or UNRESOLVED
action falls INSIDE the required analysis lookback, the symbol's volume is flagged
``volume_safe_for_lookback = False`` so volume-dependent ranking is blocked rather than
fed a guessed series. Nothing here divides prices — price adjustment is unchanged.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from providers.eodhd_adjustment import _to_date, normalize_splits

# volume policies
MULTIPLY_BY_FACTOR = "MULTIPLY_BY_FACTOR"
KEEP_RAW = "KEEP_RAW"
PROVIDER_ALREADY_ADJUSTED = "PROVIDER_ALREADY_ADJUSTED"
EVENT_SPECIFIC = "EVENT_SPECIFIC"
UNRESOLVED = "UNRESOLVED"
NONE = "NONE"

# order from safest (raw-usable) to most blocking, for "worst policy in window"
_SEVERITY = {NONE: 0, KEEP_RAW: 1, MULTIPLY_BY_FACTOR: 2, PROVIDER_ALREADY_ADJUSTED: 2,
             EVENT_SPECIFIC: 3, UNRESOLVED: 4}
_BLOCKING = {EVENT_SPECIFIC, UNRESOLVED}

RECON_PATH = Path("reports/eodhd/corporate_action_reconciliation.csv")
# The version travels with every frame as provenance, so it moves whenever
# the reconciliation behind it moves. 2026-09-12 added nine events across
# six symbols, judged against the measured Mubasher record rather than
# against Yahoo.
CORPORATE_ACTION_POLICY_VERSION = "corporate_action_reconciliation@2026-09-12"
# Longest volume window used by the liquidity ranking (avg_volume_30); the safety gate
# checks whether a non-multiply event falls within this many completed sessions.
DEFAULT_VOLUME_LOOKBACK_SESSIONS = 30

_RECON_CACHE = {"mtime": None, "map": {}}


def _reconciliation():
    """{(symbol, iso_date): volume_rule} from the validated reconciliation CSV."""
    try:
        mtime = RECON_PATH.stat().st_mtime
    except OSError:
        return {}
    if _RECON_CACHE["mtime"] != mtime:
        m = {}
        try:
            df = pd.read_csv(RECON_PATH)
            for _, r in df.iterrows():
                key = (str(r["symbol"]).upper(), str(r["effective_date"])[:10])
                m[key] = str(r.get("volume_rule") or UNRESOLVED).strip().upper()
        except Exception:
            return _RECON_CACHE["map"]
        _RECON_CACHE["map"] = m
        _RECON_CACHE["mtime"] = mtime
    return _RECON_CACHE["map"]


def event_volume_policy(symbol, effective_date):
    """Validated volume policy for one split event; UNRESOLVED when not reconciled."""
    key = (str(symbol).upper(), str(effective_date)[:10])
    return _reconciliation().get(key, UNRESOLVED)


def classify_events(symbol, splits):
    """[(ex_date, ratio, policy)] for a symbol's normalized splits."""
    out = []
    for ex_date, ratio in normalize_splits(splits):
        out.append((ex_date, ratio, event_volume_policy(symbol, ex_date.isoformat())))
    return out


def _volume_factor_for(day, classified):
    """Cumulative product of ratios for VALIDATED TRUE SPLITS with ex-date after ``day``.

    Only MULTIPLY_BY_FACTOR events grow the share count. Bonus/unresolved/event-specific
    actions never multiply volume — that is the whole correctness fix.
    """
    factor = 1.0
    d = _to_date(day)
    for ex_date, ratio, policy in classified:
        if policy == MULTIPLY_BY_FACTOR and ex_date > d:
            factor *= ratio
    return factor


def resolve_operational_volume(symbol, dates, raw_volume, splits, *,
                               lookback_sessions=DEFAULT_VOLUME_LOOKBACK_SESSIONS):
    """Return (served_volume: pd.Series, meta: dict) for a symbol's raw EODHD volume.

    ``dates`` and ``raw_volume`` are aligned sequences (ascending by date). The served
    volume applies ONLY validated true-split factors; everything else stays raw. The meta
    reports whether the volume is safe for the operational lookback window.
    """
    idx = pd.DatetimeIndex(pd.to_datetime(list(dates)))
    raw = pd.to_numeric(pd.Series(list(raw_volume)), errors="coerce").fillna(0.0)
    raw.index = idx
    classified = classify_events(symbol, splits)

    factors = pd.Series([_volume_factor_for(d.date(), classified) for d in idx],
                        index=idx, dtype="float64")
    served = (raw * factors).astype("float64")

    # window = the last ``lookback_sessions`` completed bars
    window_start = idx[-lookback_sessions] if len(idx) >= lookback_sessions else \
        (idx[0] if len(idx) else None)
    events_in_window = [(d, r, p) for (d, r, p) in classified
                        if window_start is not None and d >= window_start.date()
                        and (idx[-1].date() if len(idx) else date.min) >= d]

    unsafe = any(p in _BLOCKING for (_d, _r, p) in events_in_window)
    if events_in_window:
        worst = max(events_in_window, key=lambda e: _SEVERITY.get(e[2], 0))
        window_policy = worst[2]
        latest_action = max(events_in_window, key=lambda e: e[0])
        latest_action_in_lookback = latest_action[0].isoformat()
    else:
        window_policy = NONE
        latest_action_in_lookback = None

    applied = MULTIPLY_BY_FACTOR if (factors != 1.0).any() else KEEP_RAW
    meta = {
        "volume_series": "RAW_EODHD" if applied == KEEP_RAW else "TRUE_SPLIT_ADJUSTED",
        "volume_adjustment_policy": window_policy,
        "volume_applied_transform": applied,
        "volume_safe_for_lookback": not unsafe,
        "latest_action_in_lookback": latest_action_in_lookback,
        "events_in_lookback": [(d.isoformat(), round(r, 6), p) for d, r, p in events_in_window],
        "true_split_events": [(d.isoformat(), round(r, 6)) for d, r, p in classified
                              if p == MULTIPLY_BY_FACTOR],
        "unresolved_or_event_specific_events": [(d.isoformat(), round(r, 6), p)
                                                for d, r, p in classified if p in _BLOCKING],
        "corporate_action_policy_version": CORPORATE_ACTION_POLICY_VERSION,
        "volume_lookback_sessions": int(lookback_sessions),
    }
    return served, meta
