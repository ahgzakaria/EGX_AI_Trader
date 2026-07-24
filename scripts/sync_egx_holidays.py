"""Runnable EGX holiday sync (dry-run by default).

    python -m scripts.sync_egx_holidays            # dry-run, writes nothing
    python -m scripts.sync_egx_holidays --commit   # persist discoveries (NEEDS_REVIEW)
    python -m scripts.sync_egx_holidays --offline  # no network (records source-unavailable)

Never auto-confirms an uncertain closure and never edits Python. Exit 0 when it runs
(even with no changes / unreachable sources); non-zero only on a real crash. Do NOT
register this as a scheduled task until a dry-run has been reviewed by the operator.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.calendar import egx_holiday_sync as sync           # noqa: E402
from core.calendar.egx_calendar_reports import export_all    # noqa: E402
from core.calendar.egx_calendar_service import service       # noqa: E402

CAIRO = ZoneInfo("Africa/Cairo")


def main(argv=None):
    parser = argparse.ArgumentParser(description="EGX official holiday sync (dry-run default)")
    parser.add_argument("--commit", action="store_true",
                        help="persist discovered announcements (still NEEDS_REVIEW)")
    parser.add_argument("--offline", action="store_true", help="skip network fetch")
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc).astimezone(CAIRO)
    day = now.date().isoformat()
    out_dir = PROJECT_ROOT / "reports" / "calendar" / day
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        summary = sync.run_sync(commit=args.commit, offline=args.offline, now=now)
    except Exception as error:                              # only real failures are non-zero
        (out_dir / "sync.log").open("a", encoding="utf-8").write(
            f"{now.isoformat()} FATAL {type(error).__name__}: {error}\n")
        print(json.dumps({"error": type(error).__name__, "message": str(error)}))
        return 1

    # dated log + machine-readable summary; refresh CSV disclosure exports
    (out_dir / "sync.log").open("a", encoding="utf-8").write(
        f"{summary['at']} verdict={summary['verdict']} "
        f"checked={len(summary['sources_checked'])} failures={len(summary['failures'])} "
        f"discovered={summary['discovered']} commit={args.commit}\n")
    (out_dir / "sync_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        export_all(service())
    except Exception:
        pass

    print(json.dumps({k: summary[k] for k in
                      ("verdict", "sources_checked", "failures", "discovered", "commit")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
