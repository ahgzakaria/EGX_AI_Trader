"""EGX dynamic trading-calendar service — the single JSON-backed source of truth.

Resolves each date against a deterministic source priority (manual override →
confirmed official → confirmed trading-calendar → built-in fallback → unconfirmed
discovery → weekend → normal trading day) and returns a rich ``SessionStatus``.

Design guarantees (safety first):
  * New holidays are added by editing JSON data or approving in the UI — never by
    editing Python code.
  * An unconfirmed discovery NEVER silently becomes a confirmed holiday; it yields
    HOLIDAY_PENDING_REVIEW and safe gating.
  * The built-in fallback never overrides a newer manual/official decision.
  * Manual actions and every record change are auditable (append-only history).
  * Feed silence alone is never a holiday (that lives in the live-anomaly checker).

This module owns calendar/session classification only. It never touches signal,
indicator, ranking, scoring, TP/SL, provider, database, or execution logic.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo


CAIRO = ZoneInfo("Africa/Cairo")
TRADING_WEEKDAYS = {0, 1, 2, 3, 6}          # Mon-Thu + Sun; Fri/Sat are the EGX weekend
REGULAR_OPEN = time(10, 0)
CONTINUOUS_CLOSE = time(14, 15)
AUCTION_START = time(14, 15)
AUCTION_END = time(14, 25)

DATA_DIR = Path("data/calendar")
OFFICIAL_FILE = "egx_official_holidays.json"
DISCOVERED_FILE = "egx_discovered_holidays.json"
OVERRIDES_FILE = "egx_calendar_overrides.json"
SYNC_STATE_FILE = "egx_calendar_sync_state.json"
AUDIT_FILE = "egx_calendar_audit.json"

# Curated last-resort fallback (used only when no JSON record covers a date). JSON
# is authoritative; this exists so the app degrades safely if data files vanish.
_BUILT_IN_FALLBACK = {
    date(2026, 7, 23): {"name_en": "23 July Revolution Day",
                        "name_ar": "عيد ثورة 23 يوليو", "closure_type": "FULL_DAY"},
}

# --- statuses / enums --------------------------------------------------------
TRADING_DAY_CONFIRMED = "TRADING_DAY_CONFIRMED"
HOLIDAY_CONFIRMED = "HOLIDAY_CONFIRMED"
WEEKEND = "WEEKEND"
HOLIDAY_PENDING_REVIEW = "HOLIDAY_PENDING_REVIEW"
SESSION_STATUS_UNCERTAIN = "SESSION_STATUS_UNCERTAIN"
EXCEPTIONAL_CLOSURE = "EXCEPTIONAL_CLOSURE"
PARTIAL_SESSION = "PARTIAL_SESSION"
LATE_OPEN = "LATE_OPEN"
EARLY_CLOSE = "EARLY_CLOSE"

CLOSURE_TYPES = {"FULL_DAY", "LATE_OPEN", "EARLY_CLOSE", "AUCTION_ONLY_CHANGE",
                 "EXCEPTIONAL_CLOSURE", "UNKNOWN"}
RECORD_STATUSES = {"CONFIRMED", "DISCOVERED", "NEEDS_REVIEW", "REJECTED", "SUPERSEDED"}
SOURCE_TYPES = {"EGX_OFFICIAL", "EGX_TRADING_CALENDAR", "EGX_NEWS", "MANUAL_OVERRIDE",
                "BUILT_IN_FALLBACK", "THIRD_PARTY_REFERENCE"}

# Source-priority rank (lower = higher priority) for a CONFIRMED closure decision.
_CONFIRMED_PRIORITY = {"MANUAL_OVERRIDE": 1, "EGX_OFFICIAL": 2,
                       "EGX_TRADING_CALENDAR": 3, "BUILT_IN_FALLBACK": 4}


@dataclass
class SessionStatus:
    date: str
    status: str
    is_trading_day: object                 # True / False / None (None = uncertain)
    phase: str
    holiday_name: str | None
    closure_type: str | None
    source: str | None
    confidence: float
    next_trading_session: str | None
    entries_allowed: bool
    continuous_start: str | None
    continuous_end: str | None
    auction_start: str | None
    auction_end: str | None
    warning: str | None
    review_required: bool
    conflicts: list = field(default_factory=list)

    def as_dict(self):
        return asdict(self)


def _to_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _now_iso():
    return datetime.now(timezone.utc).astimezone(CAIRO).isoformat(timespec="seconds")


class CalendarService:
    """JSON-backed EGX calendar. Cheap to construct; reads files lazily per call."""

    def __init__(self, data_dir=None):
        self.dir = Path(data_dir) if data_dir else DATA_DIR

    # -- low-level json io ---------------------------------------------------

    def _path(self, name):
        return self.dir / name

    def _load(self, name):
        try:
            return json.loads(self._path(name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _records(self, name):
        payload = self._load(name)
        recs = payload.get("records") if isinstance(payload, dict) else None
        return list(recs) if isinstance(recs, list) else []

    def _write_records(self, name, records, extra=None):
        payload = self._load(name)
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("schema_version", 1)
        payload["records"] = records
        if extra:
            payload.update(extra)
        self._atomic_write(name, payload)

    def _atomic_write(self, name, payload):
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self._path(name)
        tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    # -- record queries ------------------------------------------------------

    def _records_for(self, name, day, statuses=None):
        d = _to_date(day)
        out = []
        for r in self._records(name):
            if _to_date(r.get("date")) != d:
                continue
            if statuses and str(r.get("status")) not in statuses:
                continue
            out.append(r)
        return out

    def _confirmed_record(self, day):
        """Highest-priority CONFIRMED closure record for ``day`` (or None)."""
        candidates = []
        for r in self._records_for(OVERRIDES_FILE, day, {"CONFIRMED"}):
            candidates.append(("MANUAL_OVERRIDE", r))
        for r in self._records_for(OFFICIAL_FILE, day, {"CONFIRMED"}):
            st = str(r.get("source_type"))
            candidates.append((st if st in _CONFIRMED_PRIORITY else "EGX_OFFICIAL", r))
        best = None
        best_rank = 99
        for src, r in candidates:
            rank = _CONFIRMED_PRIORITY.get(src, 5)
            if rank < best_rank:
                best, best_rank = r, rank
        d = _to_date(day)
        if best is None and d in _BUILT_IN_FALLBACK:
            fb = _BUILT_IN_FALLBACK[d]
            best = {"date": d.isoformat(), "status": "CONFIRMED",
                    "source_type": "BUILT_IN_FALLBACK", "confidence": 0.9,
                    "name_en": fb["name_en"], "name_ar": fb.get("name_ar", ""),
                    "closure_type": fb["closure_type"],
                    "source_title": "Built-in fallback calendar"}
        return best

    def _pending_record(self, day):
        """An unconfirmed discovery covering ``day`` (advisory → pending review)."""
        pend = self._records_for(DISCOVERED_FILE, day, {"DISCOVERED", "NEEDS_REVIEW"})
        return pend[0] if pend else None

    # -- backward-compatible holiday API (used by core.egx_calendar facade) --

    def holiday_dates(self):
        """Set of CONFIRMED FULL_DAY / EXCEPTIONAL_CLOSURE dates (non-trading)."""
        out = set()
        for name in (OVERRIDES_FILE, OFFICIAL_FILE):
            for r in self._records(name):
                if str(r.get("status")) != "CONFIRMED":
                    continue
                if str(r.get("closure_type")) in ("FULL_DAY", "EXCEPTIONAL_CLOSURE"):
                    d = _to_date(r.get("date"))
                    if d:
                        out.add(d)
        # rejected overrides can cancel a fallback; otherwise include fallback
        rejected = {(_to_date(r.get("date"))) for name in (OVERRIDES_FILE,)
                    for r in self._records(name) if str(r.get("status")) == "REJECTED"}
        for d, fb in _BUILT_IN_FALLBACK.items():
            if fb["closure_type"] in ("FULL_DAY", "EXCEPTIONAL_CLOSURE") and d not in rejected:
                out.add(d)
        return out

    def holiday_name(self, day):
        rec = self._confirmed_record(day)
        if rec and str(rec.get("closure_type")) in ("FULL_DAY", "EXCEPTIONAL_CLOSURE"):
            return rec.get("name_en") or rec.get("name_ar")
        return None

    def is_official_holiday(self, day):
        return _to_date(day) in self.holiday_dates()

    def effective_holidays(self):
        return tuple(sorted(self.holiday_dates()))

    # -- trading-day helpers (self-contained; no egx_session import) ---------

    def is_trading_day(self, day):
        d = _to_date(day)
        if d is None or d.weekday() not in TRADING_WEEKDAYS:
            return False
        return d not in self.holiday_dates()

    def next_trading_session(self, day):
        d = _to_date(day)
        candidate = d + timedelta(days=1)
        holidays = self.holiday_dates()
        while candidate.weekday() not in TRADING_WEEKDAYS or candidate in holidays:
            candidate += timedelta(days=1)
        return candidate

    # -- rich session status -------------------------------------------------

    def session_status(self, now=None):
        current = self._cairo(now)
        d = current.date()
        nxt = self.next_trading_session(d).isoformat()
        conflicts = self.conflicts(d)

        if d.weekday() not in TRADING_WEEKDAYS:
            return self._status(d, WEEKEND, is_trading=False, phase="WEEKEND",
                                nxt=nxt, entries=False, review=False, conflicts=conflicts)

        rec = self._confirmed_record(d)
        if rec is not None:
            return self._status_from_record(current, d, rec, nxt, conflicts)

        pend = self._pending_record(d)
        if pend is not None:
            name = pend.get("name_en") or pend.get("name_ar")
            warn = (f"Unconfirmed announcement suggests a possible closure "
                    f"({name or 'unnamed'}). Awaiting manual review — session gated safely.")
            return self._status(d, HOLIDAY_PENDING_REVIEW, is_trading=None,
                                phase="PENDING_REVIEW", holiday=name,
                                closure=pend.get("closure_type", "UNKNOWN"),
                                source=pend.get("source_type"),
                                confidence=float(pend.get("confidence") or 0.0),
                                nxt=nxt, entries=False, warning=warn, review=True,
                                conflicts=conflicts)

        # normal trading day — attach time-of-day phase
        return self._status(d, TRADING_DAY_CONFIRMED, is_trading=True,
                            phase=self._time_phase(current), nxt=nxt, entries=True,
                            continuous_start=REGULAR_OPEN.isoformat(timespec="minutes"),
                            continuous_end=CONTINUOUS_CLOSE.isoformat(timespec="minutes"),
                            auction_start=AUCTION_START.isoformat(timespec="minutes"),
                            auction_end=AUCTION_END.isoformat(timespec="minutes"),
                            review=False, conflicts=conflicts)

    def _status_from_record(self, current, d, rec, nxt, conflicts):
        ct = str(rec.get("closure_type") or "UNKNOWN")
        name = rec.get("name_en") or rec.get("name_ar")
        src = rec.get("source_type")
        conf = float(rec.get("confidence") or 1.0)
        warn = None
        if conflicts:
            warn = "Conflicting calendar records exist for this date — see Trading Calendar."
        if ct in ("FULL_DAY",):
            return self._status(d, HOLIDAY_CONFIRMED, is_trading=False, phase="HOLIDAY",
                                holiday=name, closure=ct, source=src, confidence=conf,
                                nxt=nxt, entries=False, review=False, warning=warn,
                                conflicts=conflicts)
        if ct == "EXCEPTIONAL_CLOSURE":
            return self._status(d, EXCEPTIONAL_CLOSURE, is_trading=False, phase="HOLIDAY",
                                holiday=name, closure=ct, source=src, confidence=conf,
                                nxt=nxt, entries=False, review=False, warning=warn,
                                conflicts=conflicts)
        if ct == "LATE_OPEN":
            cs = str(rec.get("continuous_start") or "11:00")
            return self._status(d, LATE_OPEN, is_trading=True,
                                phase=self._time_phase(current, cont_start=_parse_t(cs)),
                                holiday=name, closure=ct, source=src, confidence=conf,
                                nxt=nxt, entries=True, continuous_start=cs,
                                continuous_end=CONTINUOUS_CLOSE.isoformat(timespec="minutes"),
                                auction_start=AUCTION_START.isoformat(timespec="minutes"),
                                auction_end=AUCTION_END.isoformat(timespec="minutes"),
                                review=False, warning=warn, conflicts=conflicts)
        if ct in ("EARLY_CLOSE", "AUCTION_ONLY_CHANGE"):
            ce = str(rec.get("continuous_end") or "13:00")
            status = EARLY_CLOSE if ct == "EARLY_CLOSE" else PARTIAL_SESSION
            return self._status(d, status, is_trading=True,
                                phase=self._time_phase(current, cont_end=_parse_t(ce)),
                                holiday=name, closure=ct, source=src, confidence=conf,
                                nxt=nxt, entries=True,
                                continuous_start=REGULAR_OPEN.isoformat(timespec="minutes"),
                                continuous_end=ce, auction_start=ce,
                                auction_end=str(rec.get("auction_end") or AUCTION_END.isoformat(timespec="minutes")),
                                review=False, warning=warn, conflicts=conflicts)
        # UNKNOWN closure on a confirmed record → safe-uncertain, needs review
        return self._status(d, SESSION_STATUS_UNCERTAIN, is_trading=None,
                            phase="UNCERTAIN", holiday=name, closure=ct, source=src,
                            confidence=conf, nxt=nxt, entries=False, review=True,
                            warning="Confirmed record has an UNKNOWN closure type — review required.",
                            conflicts=conflicts)

    def _status(self, d, status, *, is_trading, phase, nxt, entries, review,
                holiday=None, closure=None, source=None, confidence=1.0,
                continuous_start=None, continuous_end=None, auction_start=None,
                auction_end=None, warning=None, conflicts=None):
        return SessionStatus(
            date=d.isoformat(), status=status, is_trading_day=is_trading, phase=phase,
            holiday_name=holiday, closure_type=closure, source=source,
            confidence=confidence, next_trading_session=nxt, entries_allowed=entries,
            continuous_start=continuous_start, continuous_end=continuous_end,
            auction_start=auction_start, auction_end=auction_end, warning=warning,
            review_required=review, conflicts=conflicts or [])

    def _time_phase(self, current, cont_start=None, cont_end=None):
        t = current.timetz().replace(tzinfo=None)
        start = cont_start or REGULAR_OPEN
        end = cont_end or CONTINUOUS_CLOSE
        if t < start:
            return "PRE_OPEN"
        if t < end:
            return "CONTINUOUS"
        if t < AUCTION_END:
            return "CLOSING_AUCTION"
        return "POST_AUCTION"

    def _cairo(self, now=None):
        current = now or datetime.now(timezone.utc)
        if isinstance(current, datetime):
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            return current.astimezone(CAIRO)
        d = _to_date(current) or date.today()
        return datetime(d.year, d.month, d.day, 12, 0, tzinfo=CAIRO)

    # -- conflict detection --------------------------------------------------

    def conflicts(self, day=None):
        """List of conflict descriptors (built-in vs official, two officials, etc.)."""
        found = []
        dates = [_to_date(day)] if day is not None else None
        seen = {}
        for name in (OVERRIDES_FILE, OFFICIAL_FILE, DISCOVERED_FILE):
            for r in self._records(name):
                if str(r.get("status")) in ("REJECTED", "SUPERSEDED"):
                    continue
                d = _to_date(r.get("date"))
                if d is None or (dates and d not in dates):
                    continue
                seen.setdefault(d, []).append((name, r))
        for d, recs in seen.items():
            closures = {str(r.get("closure_type")) for _, r in recs}
            confirmed = [(n, r) for n, r in recs if str(r.get("status")) == "CONFIRMED"]
            # built-in vs an official 'trading day' can't happen (fallback only closes);
            # flag when confirmed records disagree on closure type, or a pending record
            # contradicts a confirmed one.
            if len({c for c in closures if c not in ("UNKNOWN",)}) > 1:
                found.append({"date": d.isoformat(), "kind": "CLOSURE_TYPE_DISAGREEMENT",
                              "detail": sorted(closures)})
            if len(confirmed) > 1 and len({r.get("source_type") for _, r in confirmed}) > 1:
                found.append({"date": d.isoformat(), "kind": "MULTIPLE_CONFIRMED_SOURCES",
                              "detail": sorted(str(r.get("source_type")) for _, r in confirmed)})
        return found

    # -- manual actions (write JSON + append-only audit) ---------------------

    def _audit(self, action, record, actor, reason, old=None):
        payload = self._load(AUDIT_FILE)
        if not isinstance(payload, dict):
            payload = {}
        entries = payload.get("entries")
        if not isinstance(entries, list):
            entries = []
        entries.append({
            "at": _now_iso(), "action": action, "actor": actor, "reason": reason,
            "date": record.get("date"), "old": old, "new": record})
        payload["schema_version"] = 1
        payload["entries"] = entries
        self._atomic_write(AUDIT_FILE, payload)

    def _upsert(self, name, record):
        records = self._records(name)
        d = _to_date(record.get("date"))
        replaced = False
        old = None
        for i, r in enumerate(records):
            if _to_date(r.get("date")) == d and r.get("source_type") == record.get("source_type"):
                old = dict(r)
                record["version"] = int(r.get("version", 0)) + 1
                records[i] = record
                replaced = True
                break
        if not replaced:
            record.setdefault("version", 1)
            records.append(record)
        self._write_records(name, records)
        return old

    def confirm(self, day, *, name_en, closure_type="FULL_DAY", name_ar="",
                source_type="MANUAL_OVERRIDE", source_url="", source_title="",
                actor="MANUAL", reason="", confidence=1.0, extra=None):
        """Operator-confirm a closure (writes an override record + audit)."""
        if closure_type not in CLOSURE_TYPES:
            raise ValueError(f"unknown closure_type {closure_type}")
        d = _to_date(day)
        record = {
            "date": d.isoformat(), "name_en": name_en, "name_ar": name_ar,
            "exchange": "EGX", "closure_type": closure_type, "status": "CONFIRMED",
            "source_type": source_type, "source_url": source_url,
            "source_title": source_title, "confirmed_at": _now_iso(),
            "confirmed_by": "MANUAL", "confidence": float(confidence),
            "notes": reason, "source_hash": "", "version": 1}
        if extra:
            record.update(extra)
        target = OVERRIDES_FILE if source_type == "MANUAL_OVERRIDE" else OFFICIAL_FILE
        old = self._upsert(target, record)
        self._audit("CONFIRM", record, actor, reason, old)
        return record

    def reject(self, day, *, actor="MANUAL", reason="", source_type=None):
        """Mark discovered/override records for a date REJECTED (never deleted)."""
        d = _to_date(day)
        touched = []
        for name in (DISCOVERED_FILE, OVERRIDES_FILE):
            records = self._records(name)
            changed = False
            for r in records:
                if _to_date(r.get("date")) == d and (source_type is None
                                                     or r.get("source_type") == source_type):
                    if str(r.get("status")) != "REJECTED":
                        old = dict(r)
                        r["status"] = "REJECTED"
                        r["version"] = int(r.get("version", 0)) + 1
                        r["rejected_at"] = _now_iso()
                        self._audit("REJECT", r, actor, reason, old)
                        touched.append(r)
                        changed = True
            if changed:
                self._write_records(name, records)
        return touched

    def override(self, day, *, name_en, closure_type, actor="MANUAL", reason="",
                 name_ar="", **kw):
        """Add/replace a MANUAL_OVERRIDE record (highest priority)."""
        return self.confirm(day, name_en=name_en, name_ar=name_ar,
                            closure_type=closure_type, source_type="MANUAL_OVERRIDE",
                            actor=actor, reason=reason, extra=kw or None)

    def supersede(self, day, source_type, *, actor="MANUAL", reason=""):
        """Mark an existing record SUPERSEDED (kept for audit, no longer active)."""
        d = _to_date(day)
        for name in (OFFICIAL_FILE, DISCOVERED_FILE, OVERRIDES_FILE):
            records = self._records(name)
            changed = False
            for r in records:
                if _to_date(r.get("date")) == d and r.get("source_type") == source_type \
                        and str(r.get("status")) != "SUPERSEDED":
                    old = dict(r)
                    r["status"] = "SUPERSEDED"
                    r["version"] = int(r.get("version", 0)) + 1
                    r["superseded_at"] = _now_iso()
                    self._audit("SUPERSEDE", r, actor, reason, old)
                    changed = True
            if changed:
                self._write_records(name, records)

    # -- discovery ingest (used by the sync; never auto-confirms) ------------

    def ingest_discovery(self, record):
        """Add/version a discovered announcement (status DISCOVERED/NEEDS_REVIEW).

        Idempotent by (date, source_url); a changed source_hash creates a new
        version. NEVER writes a CONFIRMED record.
        """
        d = _to_date(record.get("date"))
        record = dict(record)
        if str(record.get("status")) not in ("DISCOVERED", "NEEDS_REVIEW"):
            record["status"] = "NEEDS_REVIEW"
        record.setdefault("source_hash", _hash(record))
        record["discovered_at"] = record.get("discovered_at") or _now_iso()
        records = self._records(DISCOVERED_FILE)
        for i, r in enumerate(records):
            same = (_to_date(r.get("date")) == d
                    and r.get("source_url", "") == record.get("source_url", ""))
            if same:
                if r.get("source_hash") == record.get("source_hash"):
                    return {"action": "unchanged", "record": r}
                record["version"] = int(r.get("version", 0)) + 1
                records[i] = record
                self._write_records(DISCOVERED_FILE, records)
                self._audit("DISCOVERY_UPDATED", record, "AUTO_SYNC", "source changed", r)
                return {"action": "updated", "record": record}
        record.setdefault("version", 1)
        records.append(record)
        self._write_records(DISCOVERED_FILE, records)
        self._audit("DISCOVERY_NEW", record, "AUTO_SYNC", "new announcement")
        return {"action": "new", "record": record}

    # -- sync state ----------------------------------------------------------

    def read_sync_state(self):
        return self._load(SYNC_STATE_FILE)

    def write_sync_state(self, state):
        state = dict(state)
        state.setdefault("schema_version", 1)
        self._atomic_write(SYNC_STATE_FILE, state)

    def sync_is_stale(self, max_age_hours=48, now=None):
        state = self.read_sync_state()
        last = state.get("last_sync_at")
        if not last:
            return True
        try:
            ts = datetime.fromisoformat(str(last))
        except ValueError:
            return True
        current = self._cairo(now)
        return (current - ts.astimezone(CAIRO)).total_seconds() > max_age_hours * 3600

    def all_records(self):
        rows = []
        for name, bucket in ((OFFICIAL_FILE, "official"), (DISCOVERED_FILE, "discovered"),
                             (OVERRIDES_FILE, "override")):
            for r in self._records(name):
                rows.append({**r, "_bucket": bucket})
        return rows

    def audit_entries(self):
        payload = self._load(AUDIT_FILE)
        entries = payload.get("entries") if isinstance(payload, dict) else None
        return list(entries) if isinstance(entries, list) else []


def _parse_t(text):
    try:
        hh, mm = str(text).split(":")[:2]
        return time(int(hh), int(mm))
    except (ValueError, TypeError):
        return REGULAR_OPEN


def _hash(record):
    key = "|".join(str(record.get(k, "")) for k in
                   ("date", "name_en", "closure_type", "source_url", "source_title"))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


# Module-level default instance + thin function API (the shared singleton).
_service = CalendarService()


def service():
    return _service
