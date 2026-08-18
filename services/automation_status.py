"""Read what the scheduled daily automation did — or did not do.

The automation writes one status file per session date. This reads it back so
a human looking at the dashboard can answer "did the scheduler run today?"
without opening Task Scheduler and decoding a hexadecimal last-result code.

That question had no answer before. Two scheduled tasks pointed at working
directories that had been deleted, failed in under a second with 0x8007010B
every trading morning, and reported that to nobody. The operator kept starting
sessions by hand and reasonably assumed that was how the system worked. On
2026-08-17 nobody started anything and the session was lost outright.

A missing status file is itself a finding — it means the task never ran, which
is a different failure from a task that ran and refused. The two are reported
distinctly and neither is silent.

Read-only. Never raises: a dashboard that crashes because a status file is
malformed tells the reader even less than one that says it cannot tell.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATUS_DIR = PROJECT_ROOT / "data" / "automation_status"

#: Written while the run is in flight and replaced on exit. Seeing it after the
#: session should have finished means the run died without an exit path — a
#: crash or a power cut, not a refusal.
RUNNING = "RUNNING"
COMPLETED = "COMPLETED"
REFUSED = "REFUSED"
FAILED = "FAILED"

#: No file at all. The task did not run: disabled, mis-pathed, or the machine
#: was off. This is the state that went unnoticed for weeks.
NEVER_RAN = "NEVER_RAN"

#: The file exists but could not be parsed.
UNREADABLE = "UNREADABLE"


@dataclass(frozen=True)
class AutomationStatus:
    """What the scheduler did for one session date."""

    session_date: str
    outcome: str
    reason: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    steps: Optional[dict] = None
    log: Optional[str] = None

    @property
    def ran(self) -> bool:
        return self.outcome not in (NEVER_RAN, UNREADABLE)

    @property
    def healthy(self) -> bool:
        return self.outcome == COMPLETED

    @property
    def needs_attention(self) -> bool:
        """True when a human should look. Deliberately includes NEVER_RAN."""

        return self.outcome != COMPLETED


def status_path(session_date, *, directory: Optional[Path] = None) -> Path:
    day = session_date.isoformat() if isinstance(session_date, date) else str(session_date)
    return Path(directory or STATUS_DIR) / f"orb_automation_{day}.json"


def read_status(session_date, *, directory: Optional[Path] = None) -> AutomationStatus:
    """Return the recorded outcome for one session date, never raising."""

    day = session_date.isoformat() if isinstance(session_date, date) else str(session_date)
    path = status_path(day, directory=directory)

    if not path.is_file():
        return AutomationStatus(
            session_date=day,
            outcome=NEVER_RAN,
            reason=(
                "No status file was written for this date. The scheduled task "
                "did not run — it is disabled, it failed before reaching the "
                "script, or the machine was off."
            ),
        )

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:                                    # noqa: BLE001
        return AutomationStatus(
            session_date=day,
            outcome=UNREADABLE,
            reason=f"{path.name} could not be parsed: {type(error).__name__}",
        )

    if not isinstance(payload, dict):
        return AutomationStatus(
            session_date=day, outcome=UNREADABLE,
            reason=f"{path.name} does not contain an object.",
        )

    steps = payload.get("steps")
    return AutomationStatus(
        session_date=str(payload.get("session_date") or day),
        outcome=str(payload.get("outcome") or UNREADABLE),
        reason=str(payload.get("reason") or ""),
        started_at=payload.get("started_at"),
        finished_at=payload.get("finished_at"),
        steps=steps if isinstance(steps, dict) else None,
        log=payload.get("log"),
    )


def recent_statuses(days: int = 10, *, directory: Optional[Path] = None):
    """The most recent recorded runs, newest first.

    Only dates that produced a file appear here; absence is reported by
    :func:`read_status` for a date the caller names, because this function
    cannot know which missing dates were trading days.
    """

    folder = Path(directory or STATUS_DIR)
    if not folder.is_dir():
        return []

    found = []
    for path in folder.glob("orb_automation_*.json"):
        stem = path.stem.replace("orb_automation_", "")
        try:
            date.fromisoformat(stem)
        except ValueError:
            continue
        found.append(stem)

    return [read_status(day, directory=folder) for day in sorted(found, reverse=True)[:days]]
