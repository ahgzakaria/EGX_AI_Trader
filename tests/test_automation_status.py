"""The status reader must distinguish "refused" from "never ran".

Those two states looked identical from the dashboard before this existed, and
that is precisely how two scheduled tasks stayed broken for weeks: a task that
fails before reaching its script writes nothing at all, which read the same as
a quiet day.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.automation_status import (
    COMPLETED,
    NEVER_RAN,
    REFUSED,
    UNREADABLE,
    read_status,
    recent_statuses,
    status_path,
)


def _write(directory: Path, day: str, payload: dict) -> None:
    (directory / f"orb_automation_{day}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_absent_file_reports_never_ran_not_success(tmp_path):
    status = read_status("2026-08-17", directory=tmp_path)

    assert status.outcome == NEVER_RAN
    assert not status.ran
    assert not status.healthy
    # The whole point: silence must register as a problem.
    assert status.needs_attention


def test_refusal_is_read_back_with_its_reason_and_steps(tmp_path):
    _write(tmp_path, "2026-08-18", {
        "session_date": "2026-08-18",
        "outcome": REFUSED,
        "reason": "readiness gate failed: median lag -0.60s",
        "started_at": "2026-08-18T09:40:00+03:00",
        "steps": {"trading_day": "OK", "readiness": "REFUSED"},
    })

    status = read_status("2026-08-18", directory=tmp_path)

    assert status.outcome == REFUSED
    assert "median lag" in status.reason
    assert status.steps["readiness"] == "REFUSED"
    assert status.ran                # it ran; it declined to start a session
    assert status.needs_attention


def test_only_completed_counts_as_healthy(tmp_path):
    _write(tmp_path, "2026-08-16", {"outcome": COMPLETED, "reason": "clean"})

    status = read_status("2026-08-16", directory=tmp_path)

    assert status.healthy
    assert not status.needs_attention


@pytest.mark.parametrize("body", ["{not json", '"a string"', "[1, 2]"])
def test_malformed_file_reports_unreadable_rather_than_raising(tmp_path, body):
    (tmp_path / "orb_automation_2026-08-15.json").write_text(body, encoding="utf-8")

    status = read_status("2026-08-15", directory=tmp_path)

    assert status.outcome == UNREADABLE
    assert status.needs_attention


def test_utf8_bom_is_tolerated(tmp_path):
    # Windows PowerShell 5.1 writes a BOM for `-Encoding utf8`. The writer
    # avoids it deliberately, but a file written by any other hand must not
    # take the dashboard down.
    path = tmp_path / "orb_automation_2026-08-14.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"outcome": COMPLETED}).encode())

    assert read_status("2026-08-14", directory=tmp_path).outcome in (COMPLETED, UNREADABLE)


def test_recent_statuses_are_newest_first_and_ignore_foreign_files(tmp_path):
    for day in ("2026-08-12", "2026-08-13", "2026-08-16"):
        _write(tmp_path, day, {"outcome": COMPLETED, "reason": ""})
    (tmp_path / "orb_automation_notadate.json").write_text("{}", encoding="utf-8")
    (tmp_path / "README.md").write_text("x", encoding="utf-8")

    recent = recent_statuses(directory=tmp_path)

    assert [s.session_date for s in recent] == ["2026-08-16", "2026-08-13", "2026-08-12"]


def test_recent_statuses_respects_the_limit(tmp_path):
    for day in ("2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13"):
        _write(tmp_path, day, {"outcome": COMPLETED, "reason": ""})

    assert len(recent_statuses(days=2, directory=tmp_path)) == 2


def test_missing_directory_is_empty_not_an_error(tmp_path):
    assert recent_statuses(directory=tmp_path / "nope") == []


def test_status_path_accepts_a_date_object(tmp_path):
    from datetime import date

    assert status_path(date(2026, 8, 18), directory=tmp_path).name == \
        "orb_automation_2026-08-18.json"
