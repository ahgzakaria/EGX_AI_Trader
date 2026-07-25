"""Dynamic EGX calendar service + sync tests (isolated temp data dir).

Deterministic: each test builds its own CalendarService over a tmp_path, so the real
data/calendar/*.json files are never touched. Calendar/session logic only.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from core.calendar.egx_calendar_service import CalendarService
from core.calendar import egx_holiday_sync as sync

CAIRO = ZoneInfo("Africa/Cairo")


def _svc(tmp_path, official=None, discovered=None, overrides=None):
    d = tmp_path / "calendar"
    d.mkdir(parents=True, exist_ok=True)
    (d / "egx_official_holidays.json").write_text(
        json.dumps({"records": official or []}), encoding="utf-8")
    (d / "egx_discovered_holidays.json").write_text(
        json.dumps({"records": discovered or []}), encoding="utf-8")
    (d / "egx_calendar_overrides.json").write_text(
        json.dumps({"records": overrides or []}), encoding="utf-8")
    return CalendarService(data_dir=d)


def _rec(date_str, closure="FULL_DAY", status="CONFIRMED", source="EGX_OFFICIAL", name="X"):
    return {"date": date_str, "name_en": name, "closure_type": closure, "status": status,
            "source_type": source, "confidence": 1.0}


def _at(svc_day, hh=11, mm=0):
    y, m, d = map(int, svc_day.split("-"))
    return datetime(y, m, d, hh, mm, tzinfo=CAIRO)


# --- fixed + moved holiday classification -----------------------------------

def test_fixed_known_holiday_confirmed(tmp_path):
    svc = _svc(tmp_path, official=[_rec("2026-07-23", name="Revolution Day")])
    s = svc.session_status(_at("2026-07-23"))
    assert s.status == "HOLIDAY_CONFIRMED" and s.is_trading_day is False
    assert s.next_trading_session == "2026-07-26"
    assert svc.holiday_name("2026-07-23") == "Revolution Day"


def test_moved_weekday_holiday(tmp_path):
    # a holiday moved to Wednesday 2026-07-15 (not a fixed date) is honored from JSON
    svc = _svc(tmp_path, official=[_rec("2026-07-15", name="Moved Holiday")])
    assert svc.is_trading_day(date(2026, 7, 15)) is False
    assert svc.session_status(_at("2026-07-15")).status == "HOLIDAY_CONFIRMED"
    # surrounding weekdays remain trading
    assert svc.is_trading_day(date(2026, 7, 14)) is True


def test_normal_trading_day(tmp_path):
    svc = _svc(tmp_path)
    s = svc.session_status(_at("2026-07-22", 11, 0))
    assert s.status == "TRADING_DAY_CONFIRMED" and s.is_trading_day is True
    assert s.phase == "CONTINUOUS" and s.entries_allowed is True


def test_weekend(tmp_path):
    svc = _svc(tmp_path)
    assert svc.session_status(_at("2026-07-24")).status == "WEEKEND"   # Friday
    assert svc.session_status(_at("2026-07-25")).status == "WEEKEND"   # Saturday


# --- source priority + overrides --------------------------------------------

def test_manual_override_beats_official(tmp_path):
    svc = _svc(tmp_path,
               official=[_rec("2026-08-10", closure="FULL_DAY", name="Official")],
               overrides=[_rec("2026-08-10", closure="EARLY_CLOSE", source="MANUAL_OVERRIDE",
                               name="Override")])
    s = svc.session_status(_at("2026-08-10", 11, 0))
    assert s.status == "EARLY_CLOSE" and s.source == "MANUAL_OVERRIDE"


def test_builtin_fallback_used_when_no_json(tmp_path):
    svc = _svc(tmp_path)     # empty official — built-in fallback still knows 2026-07-23
    assert svc.is_official_holiday(date(2026, 7, 23)) is True
    assert svc.session_status(_at("2026-07-23")).status == "HOLIDAY_CONFIRMED"


# --- pending / uncertain safe gating ----------------------------------------

def test_unconfirmed_discovery_is_pending_not_holiday(tmp_path):
    svc = _svc(tmp_path, discovered=[_rec("2026-09-01", status="NEEDS_REVIEW", name="Maybe")])
    s = svc.session_status(_at("2026-09-01", 11, 0))
    assert s.status == "HOLIDAY_PENDING_REVIEW"
    assert s.is_trading_day is None            # uncertain — not auto-holiday, not auto-live
    assert s.review_required is True and s.entries_allowed is False
    # a pending discovery must NOT count as a confirmed holiday
    assert svc.is_official_holiday(date(2026, 9, 1)) is False


def test_unknown_closure_type_on_confirmed_is_uncertain(tmp_path):
    svc = _svc(tmp_path, official=[_rec("2026-09-02", closure="UNKNOWN", name="?")])
    s = svc.session_status(_at("2026-09-02", 11, 0))
    assert s.status == "SESSION_STATUS_UNCERTAIN" and s.review_required is True


# --- partial sessions -------------------------------------------------------

def test_early_close_partial_session(tmp_path):
    svc = _svc(tmp_path, overrides=[{**_rec("2026-09-03", closure="EARLY_CLOSE",
                                            source="MANUAL_OVERRIDE"), "continuous_end": "12:00"}])
    s = svc.session_status(_at("2026-09-03", 11, 0))
    assert s.status == "EARLY_CLOSE" and s.is_trading_day is True
    assert s.continuous_end == "12:00"


def test_late_open_partial_session(tmp_path):
    # 2026-09-07 is a Monday (trading weekday)
    svc = _svc(tmp_path, overrides=[{**_rec("2026-09-07", closure="LATE_OPEN",
                                            source="MANUAL_OVERRIDE"), "continuous_start": "11:30"}])
    s = svc.session_status(_at("2026-09-07", 11, 0))     # 11:00 is before the late open
    assert s.status == "LATE_OPEN" and s.phase == "PRE_OPEN"


# --- next / previous session + no false lag ---------------------------------

def test_next_session_skips_holiday_and_weekend(tmp_path):
    svc = _svc(tmp_path, official=[_rec("2026-07-23")])
    assert svc.next_trading_session(date(2026, 7, 23)) == date(2026, 7, 26)
    assert svc.next_trading_session(date(2026, 7, 24)) == date(2026, 7, 26)


# --- manual actions + audit + versioning ------------------------------------

def test_confirm_reject_override_are_audited(tmp_path):
    svc = _svc(tmp_path)
    svc.confirm("2026-10-06", name_en="Armed Forces Day", closure_type="FULL_DAY",
                source_type="MANUAL_OVERRIDE", reason="operator")
    assert svc.is_official_holiday(date(2026, 10, 6)) is True
    actions = [a["action"] for a in svc.audit_entries()]
    assert "CONFIRM" in actions
    svc.reject("2026-10-06", reason="mistake")
    assert svc.is_official_holiday(date(2026, 10, 6)) is False   # rejected → not a holiday
    assert "REJECT" in [a["action"] for a in svc.audit_entries()]


def test_confirmed_history_not_silently_rewritten(tmp_path):
    svc = _svc(tmp_path, official=[_rec("2026-07-23", name="Revolution Day")])
    svc.override("2026-07-23", name_en="Correction", closure_type="FULL_DAY", reason="fix")
    # original official record still present (superseded/kept), override wins operationally
    dates = [r for r in svc.all_records() if r.get("date") == "2026-07-23"]
    assert len(dates) >= 2
    assert svc.session_status(_at("2026-07-23")).source == "MANUAL_OVERRIDE"


# --- conflicts --------------------------------------------------------------

def test_conflict_detected_between_sources(tmp_path):
    svc = _svc(tmp_path,
               official=[_rec("2026-11-01", closure="FULL_DAY", name="A")],
               overrides=[_rec("2026-11-01", closure="EARLY_CLOSE", source="MANUAL_OVERRIDE",
                               name="B")])
    conflicts = svc.conflicts(date(2026, 11, 1))
    assert any(c["kind"] == "CLOSURE_TYPE_DISAGREEMENT" for c in conflicts)
    # override still wins operationally, but the conflict is surfaced on the status
    assert svc.session_status(_at("2026-11-01", 11, 0)).conflicts


# --- sync: ambiguity, dedup, versioning, source-unavailable -----------------

def test_ambiguous_announcement_becomes_needs_review(tmp_path):
    svc = _svc(tmp_path)
    html = ("<html><body>بمناسبة عيد قادم سيتم تعطيل التداول يوم الخميس المقبل"
            "</body></html>")   # "next Thursday" + occasion-of → ambiguous
    src = {"name": "EGX News", "source_type": "EGX_NEWS", "url": "https://www.egx.com.eg/x"}
    cands = sync.extract_candidates(html, src, year_hint=2026)
    assert cands and all(c.ambiguous for c in cands)
    assert all(sync.classify_confirmation(c, svc) == "NEEDS_REVIEW" for c in cands)


def test_explicit_official_still_routed_through_review_by_default(tmp_path):
    svc = _svc(tmp_path)
    html = "<html><body>Official holiday: trading will be suspended on 2026-12-07.</body></html>"
    src = {"name": "EGX Trading Calendar", "source_type": "EGX_TRADING_CALENDAR",
           "url": "https://www.egx.com.eg/en/homepage.aspx"}
    summary = sync.run_sync(commit=True, svc=svc, html_by_source={src["url"]: html},
                            now=datetime(2026, 12, 1, tzinfo=CAIRO))
    # discovered + written, but NEVER auto-confirmed → still needs manual approval
    assert summary["discovered"] >= 1
    assert svc.is_official_holiday(date(2026, 12, 7)) is False


def test_sync_idempotent_and_versioned(tmp_path):
    svc = _svc(tmp_path)
    rec = {"date": "2026-12-25", "name_en": "X", "closure_type": "FULL_DAY",
           "source_url": "u", "source_title": "t", "source_type": "EGX_NEWS"}
    a = svc.ingest_discovery(dict(rec))
    b = svc.ingest_discovery(dict(rec))
    assert a["action"] == "new" and b["action"] == "unchanged"       # dedup
    changed = {**rec, "name_en": "X updated", "source_hash": "different"}
    c = svc.ingest_discovery(changed)
    assert c["action"] == "updated" and c["record"]["version"] == 2  # hash change → new version


def test_source_hash_change_creates_new_version(tmp_path):
    svc = _svc(tmp_path)
    base = {"date": "2027-01-07", "source_url": "u", "source_title": "t",
            "source_type": "EGX_NEWS", "name_en": "N", "closure_type": "FULL_DAY",
            "source_hash": "h1"}
    svc.ingest_discovery(dict(base))
    res = svc.ingest_discovery({**base, "source_hash": "h2"})
    assert res["action"] == "updated"


def test_sync_source_unavailable_is_clean(tmp_path):
    svc = _svc(tmp_path)
    summary = sync.run_sync(commit=True, svc=svc, offline=True,
                            now=datetime(2026, 7, 20, tzinfo=CAIRO))
    assert summary["verdict"] == "CALENDAR_SOURCE_UNAVAILABLE"
    assert summary["discovered"] == 0            # nothing confirmed/created from silence


# --- preflight verdicts -----------------------------------------------------

def test_preflight_verdicts(tmp_path):
    from scripts.egx_session_preflight import build_verdict
    holiday_svc = _svc(tmp_path, official=[_rec("2026-07-23")])
    hs = holiday_svc.session_status(_at("2026-07-23"))
    assert build_verdict(hs, holiday_svc, {}) == "NON_TRADING_DAY"

    pend_svc = _svc(tmp_path / "b", discovered=[_rec("2026-09-01", status="NEEDS_REVIEW")])
    ps = pend_svc.session_status(_at("2026-09-01", 11))
    assert build_verdict(ps, pend_svc, {}) == "REVIEW_REQUIRED"

    trade_svc = _svc(tmp_path / "c")
    ts = trade_svc.session_status(_at("2026-07-22", 11))
    assert build_verdict(ts, trade_svc, {}) == "CLEAR_TO_START_RESEARCH_SESSION"


# --- facade smoke -----------------------------------------------------------

def test_facade_and_report_smoke(tmp_path):
    import core.egx_calendar as cal
    assert cal.is_official_holiday(date(2026, 7, 23)) is True
    assert cal.next_trading_session(date(2026, 7, 23)) == date(2026, 7, 26)
    from core.calendar.egx_calendar_reports import export_all
    svc = _svc(tmp_path, official=[_rec("2026-07-23")])
    written = export_all(svc, report_dir=tmp_path / "rep")
    assert (tmp_path / "rep" / "calendar_records.csv").is_file()
    assert set(written) >= {"calendar_records", "conflicts", "manual_actions"}


def test_calendar_module_import_smoke():
    import importlib
    for m in ("core.calendar.egx_calendar_service", "core.calendar.egx_holiday_sync",
              "core.calendar.egx_calendar_reports", "dashboard.trading_calendar"):
        assert importlib.import_module(m)
