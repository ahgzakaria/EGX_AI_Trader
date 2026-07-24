"""Morning EGX session pre-flight check (run ~08:00 and ~09:30 Cairo, Sun-Thu).

Consults the central calendar service (weekend, confirmed local calendar, pending
announcements, manual overrides, sync freshness) and emits a verdict + structured
session status. It never fetches credentials and never modifies trading logic.

    python -m scripts.egx_session_preflight

Verdicts: CLEAR_TO_START_RESEARCH_SESSION · NON_TRADING_DAY · REVIEW_REQUIRED ·
CALENDAR_SOURCE_UNAVAILABLE · CONFLICTING_OFFICIAL_RECORDS
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.calendar.egx_calendar_service import service       # noqa: E402

CAIRO = ZoneInfo("Africa/Cairo")


def build_verdict(status, svc, sync_state):
    conflicts = status.conflicts
    if conflicts:
        return "CONFLICTING_OFFICIAL_RECORDS"
    if status.status in ("HOLIDAY_CONFIRMED", "WEEKEND", "EXCEPTIONAL_CLOSURE"):
        return "NON_TRADING_DAY"
    if status.review_required or status.status in (
            "HOLIDAY_PENDING_REVIEW", "SESSION_STATUS_UNCERTAIN"):
        return "REVIEW_REQUIRED"
    if str(sync_state.get("last_verdict")) == "CALENDAR_SOURCE_UNAVAILABLE" \
            and svc.sync_is_stale():
        # sources unreachable AND stale — surface it, but a confirmed local trading
        # day still lets research proceed (advisory only).
        return "CALENDAR_SOURCE_UNAVAILABLE"
    return "CLEAR_TO_START_RESEARCH_SESSION"


def main(argv=None):
    now = datetime.now(timezone.utc).astimezone(CAIRO)
    svc = service()
    status = svc.session_status(now)
    sync_state = svc.read_sync_state()
    verdict = build_verdict(status, svc, sync_state)

    payload = {
        "checked_at": now.isoformat(timespec="seconds"),
        "verdict": verdict,
        "session": status.as_dict(),
        "sync": {
            "last_sync_at": sync_state.get("last_sync_at") or None,
            "last_verdict": sync_state.get("last_verdict") or None,
            "stale": svc.sync_is_stale(),
        },
        "next_trading_session": status.next_trading_session,
        "review_required": status.review_required,
        "production_enabled": False,
    }

    out_dir = PROJECT_ROOT / "reports" / "calendar" / now.date().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "session_preflight.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "session_preflight.log").open("a", encoding="utf-8").write(
        f"{payload['checked_at']} verdict={verdict} status={status.status} "
        f"trading={status.is_trading_day} next={status.next_trading_session}\n")

    print(json.dumps({"verdict": verdict, "status": status.status,
                      "is_trading_day": status.is_trading_day,
                      "next_trading_session": status.next_trading_session},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
