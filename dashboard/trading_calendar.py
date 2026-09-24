"""SYSTEM → Trading Calendar — review, approve, and override EGX closures (UI only).

Presents the dynamic calendar (confirmed closures, discovered announcements needing
review, rejected/superseded records, sync history, manual overrides) and lets the
operator Confirm / Reject / Override — all written to JSON with an audit trail, never
to Python. Nothing here changes trading strategy or enables production.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import streamlit as st

from core.calendar.egx_calendar_reports import export_all
from core.calendar.egx_calendar_service import (
    CLOSURE_TYPES,
    service,
)
from dashboard.ui import badge_html, egx_holiday_banner, page_header, section_header


def show_trading_calendar():
    svc = service()
    page_header("Trading Calendar",
                "تقويم تداول البورصة المصرية · Official-source sync + manual approval · "
                "Session-safety only",
                icon="📅", badge="CALENDAR")
    egx_holiday_banner()

    _today_status(svc)
    _sync_bar(svc)
    tabs = st.tabs(["Needs Review", "Upcoming Confirmed", "Manual Overrides",
                    "Conflicts", "Rejected / Superseded", "Sync & Audit"])
    with tabs[0]:
        _needs_review(svc)
    with tabs[1]:
        _upcoming_confirmed(svc)
    with tabs[2]:
        _manual_override_form(svc)
    with tabs[3]:
        _conflicts(svc)
    with tabs[4]:
        _rejected(svc)
    with tabs[5]:
        _sync_audit(svc)


def _today_status(svc):
    s = svc.session_status()
    tone = {"TRADING_DAY_CONFIRMED": "green", "HOLIDAY_CONFIRMED": "blue",
            "WEEKEND": "gray", "HOLIDAY_PENDING_REVIEW": "amber",
            "SESSION_STATUS_UNCERTAIN": "red", "EXCEPTIONAL_CLOSURE": "blue",
            "PARTIAL_SESSION": "amber", "LATE_OPEN": "amber", "EARLY_CLOSE": "amber"}.get(
        s.status, "gray")
    c = st.columns(4)
    c[0].markdown(badge_html(s.status.replace("_", " "), tone), unsafe_allow_html=True)
    c[0].caption(f"Today · {s.date}")
    c[1].metric("Trading day", {True: "Yes", False: "No", None: "Uncertain"}[s.is_trading_day])
    c[2].metric("Next session", str(s.next_trading_session))
    c[3].metric("Review required", "Yes" if s.review_required else "No")
    if s.holiday_name:
        st.caption(f"Closure: **{s.holiday_name}** · type {s.closure_type} · source {s.source}")
    if s.warning:
        st.warning(s.warning)


def _sync_bar(svc):
    state = svc.read_sync_state()
    cols = st.columns([1.4, 1, 1, 1.2])
    cols[0].caption(f"Last sync: **{state.get('last_sync_at') or 'never'}**")
    cols[1].caption(f"Verdict: **{state.get('last_verdict') or '—'}**")
    cols[2].caption(f"Stale: **{'yes' if svc.sync_is_stale() else 'no'}**")
    if cols[3].button("▶ Run Sync (dry-run)", key="cal_run_sync"):
        from core.calendar import egx_holiday_sync as sync
        with st.spinner("Checking official EGX sources…"):
            summary = sync.run_sync(commit=True, svc=svc)
            export_all(svc)
        st.success(f"Sync verdict: {summary['verdict']} · "
                   f"discovered {summary['discovered']} · failures {len(summary['failures'])}")
        st.caption("Discoveries are NEEDS_REVIEW — nothing is auto-confirmed. Review below.")


def _needs_review(svc):
    section_header("Needs Review · مراجعة مطلوبة", "unconfirmed announcements — approve or reject")
    rows = [r for r in svc.all_records()
            if r.get("_bucket") == "discovered" and r.get("status") in ("DISCOVERED", "NEEDS_REVIEW")]
    if not rows:
        st.caption("No announcements awaiting review.")
        return
    for r in rows:
        with st.container(border=True):
            st.markdown(f"**{r.get('date')}** · {r.get('name_en') or r.get('name_ar') or '—'} "
                        f"· {r.get('closure_type')} · conf {r.get('confidence')}")
            st.caption(f"Source: {r.get('source_type')} · {r.get('source_title')} "
                       f"{r.get('source_url') or ''}")
            if r.get("notes"):
                st.caption(f"Evidence: {r['notes']}")
            cc = st.columns([1, 1, 2, 2])
            key = f"{r.get('date')}_{r.get('source_url')}"
            ctype = cc[2].selectbox("Closure", sorted(CLOSURE_TYPES),
                                    index=sorted(CLOSURE_TYPES).index(str(r.get("closure_type", "FULL_DAY")))
                                    if str(r.get("closure_type")) in CLOSURE_TYPES else 0,
                                    key=f"ct_{key}")
            reason = cc[3].text_input("Reason", key=f"rs_{key}", placeholder="approval note")
            if cc[0].button("Confirm", key=f"cf_{key}"):
                svc.confirm(r.get("date"), name_en=r.get("name_en") or "EGX closure",
                            name_ar=r.get("name_ar", ""), closure_type=ctype,
                            source_type="EGX_OFFICIAL", source_url=r.get("source_url", ""),
                            source_title=r.get("source_title", ""), reason=reason or "manual confirm")
                svc.supersede(r.get("date"), r.get("source_type"), reason="confirmed via review")
                export_all(svc)
                st.success(f"Confirmed {r.get('date')}."); st.rerun()
            if cc[1].button("Reject", key=f"rj_{key}"):
                svc.reject(r.get("date"), reason=reason or "manual reject",
                           source_type=r.get("source_type"))
                export_all(svc)
                st.info(f"Rejected {r.get('date')}."); st.rerun()


def _upcoming_confirmed(svc):
    section_header("Upcoming Confirmed Closures", "confirmed EGX non-trading dates")
    today = date.today()
    rows = [r for r in svc.all_records()
            if r.get("status") == "CONFIRMED"
            and _d(r.get("date")) and _d(r.get("date")) >= today]
    rows.sort(key=lambda r: r.get("date"))
    if not rows:
        st.caption("No upcoming confirmed closures on file.")
        return
    st.dataframe(pd.DataFrame([{
        "Date": r.get("date"), "Name": r.get("name_en") or r.get("name_ar"),
        "Type": r.get("closure_type"), "Source": r.get("source_type"),
        "Confidence": r.get("confidence"), "By": r.get("confirmed_by")} for r in rows]),
        width="stretch", hide_index=True)


def _manual_override_form(svc):
    section_header("Manual Override", "operator-declared closure / exceptional day (highest priority)")
    with st.form("cal_override"):
        c = st.columns([1, 2, 1])
        d = c[0].date_input("Date", value=date.today())
        name = c[1].text_input("Holiday / reason name", placeholder="e.g. Exceptional closure")
        ctype = c[2].selectbox("Closure type", sorted(CLOSURE_TYPES))
        reason = st.text_input("Audit reason", placeholder="why this override")
        submitted = st.form_submit_button("Save Override", type="primary")
    if submitted and name:
        svc.override(d.isoformat(), name_en=name, closure_type=ctype, reason=reason or "manual override")
        export_all(svc)
        st.success(f"Override saved for {d.isoformat()} — wins operationally; conflicts are shown.")
    st.caption("Overrides are written to data/calendar/egx_calendar_overrides.json with an audit "
               "trail. Confirmed official records are never silently rewritten.")


def _conflicts(svc):
    section_header("Conflicts", "disagreements between sources — never hidden")
    conflicts = svc.conflicts()
    if not conflicts:
        st.caption("No calendar conflicts detected.")
        return
    st.dataframe(pd.DataFrame([{
        "Date": c["date"], "Kind": c["kind"], "Detail": ", ".join(map(str, c.get("detail", [])))}
        for c in conflicts]), width="stretch", hide_index=True)
    st.caption("Manual override wins operationally, but the conflict remains visible for audit.")


def _rejected(svc):
    section_header("Rejected / Superseded", "kept for audit — decisions are never silently deleted")
    rows = [r for r in svc.all_records() if r.get("status") in ("REJECTED", "SUPERSEDED")]
    if not rows:
        st.caption("None.")
        return
    st.dataframe(pd.DataFrame([{
        "Date": r.get("date"), "Name": r.get("name_en") or r.get("name_ar"),
        "Status": r.get("status"), "Source": r.get("source_type"),
        "Version": r.get("version")} for r in rows]), width="stretch", hide_index=True)


def _sync_audit(svc):
    section_header("Sync History & Audit", "official-source runs + every manual action")
    runs = svc.read_sync_state().get("runs", []) or []
    if runs:
        st.dataframe(pd.DataFrame(runs), width="stretch", hide_index=True)
    else:
        st.caption("No sync runs recorded yet.")
    st.markdown("**Manual action audit (append-only)**")
    audit = svc.audit_entries()
    if audit:
        st.dataframe(pd.DataFrame([{
            "At": a.get("at"), "Action": a.get("action"), "Actor": a.get("actor"),
            "Date": a.get("date"), "Reason": a.get("reason")} for a in audit[-100:]]),
            width="stretch", hide_index=True)
    else:
        st.caption("No manual actions recorded yet.")


def _d(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None
