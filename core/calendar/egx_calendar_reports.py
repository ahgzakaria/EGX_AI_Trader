"""CSV exports for the EGX dynamic calendar (audit / disclosure only)."""

from __future__ import annotations

import csv
from pathlib import Path

from core.calendar.egx_calendar_service import CalendarService, service

REPORT_DIR = Path("reports/calendar")


def _write_csv(path: Path, fieldnames, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def export_all(svc: CalendarService | None = None, report_dir: Path | None = None):
    """Write the five calendar CSVs from the JSON store. Returns written paths."""
    svc = svc or service()
    out = Path(report_dir) if report_dir else REPORT_DIR
    written = {}

    records = svc.all_records()
    _write_csv(out / "calendar_records.csv",
               ["date", "name_en", "name_ar", "exchange", "closure_type", "status",
                "source_type", "confidence", "confirmed_by", "version", "_bucket",
                "source_title", "source_url"], records)
    written["calendar_records"] = out / "calendar_records.csv"

    discovered = [r for r in records if r.get("_bucket") == "discovered"]
    _write_csv(out / "discovered_announcements.csv",
               ["date", "name_en", "closure_type", "status", "source_type", "confidence",
                "source_title", "source_url", "discovered_at", "announcement_date",
                "source_hash", "version"], discovered)
    written["discovered_announcements"] = out / "discovered_announcements.csv"

    conflicts = svc.conflicts()
    _write_csv(out / "conflicts.csv", ["date", "kind", "detail"],
               [{**c, "detail": ", ".join(map(str, c.get("detail", [])))} for c in conflicts])
    written["conflicts"] = out / "conflicts.csv"

    runs = svc.read_sync_state().get("runs", []) or []
    _write_csv(out / "sync_history.csv",
               ["at", "verdict", "sources_checked", "discovered", "confirmed", "failures"],
               [{**{k: r.get(k) for k in ("at", "verdict", "discovered", "confirmed")},
                 "sources_checked": ", ".join(map(str, r.get("sources_checked", []))),
                 "failures": ", ".join(map(str, r.get("failures", [])))} for r in runs])
    written["sync_history"] = out / "sync_history.csv"

    _write_csv(out / "manual_actions.csv",
               ["at", "action", "actor", "date", "reason"],
               [a for a in svc.audit_entries()
                if a.get("action") in ("CONFIRM", "REJECT", "SUPERSEDE")])
    written["manual_actions"] = out / "manual_actions.csv"

    return written
