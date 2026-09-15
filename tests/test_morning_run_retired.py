"""The morning ORB run retired with the Rubix feed, and the page says so.

The Rubix live feed was retired on the evening of 2026-09-10 because it never
carries the 14:25 auction close. Every step of the scheduled morning run read
that feed, but the task was left scheduled: from 2026-09-13 it was refused each
trading morning, and the sidebar showed a red "Run refused" for a run nobody
should expect. Disabling the task alone would have traded that for a red "No
run recorded today", so the retirement is a state of its own.

And the price-source cell on the Daily Dashboard said green "Rubix" while every
quote behind it was five days old, because the overlay calls a row "available"
whatever its age.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dashboard import home, terminal, ui
from services import automation_status as runs


# --- the retirement --------------------------------------------------------------

@pytest.mark.parametrize("day, retired", [
    ("2026-08-17", False),   # a real lost session: its absence still means something
    ("2026-09-10", False),   # the last run that completed
    ("2026-09-12", False),
    ("2026-09-13", True),    # the first morning it was refused
    ("2026-09-15", True),
])
def test_retirement_starts_at_the_first_morning_the_feed_was_gone(day, retired):
    assert runs.is_retired(day) is retired


def test_history_before_the_retirement_reads_exactly_as_it_did(tmp_path):
    assert runs.read_status("2026-08-17", directory=tmp_path).outcome == runs.NEVER_RAN


def test_the_reason_names_what_replaced_the_run():
    assert "Mubasher" in runs.RETIRED_REASON and "RUN_DAILY.bat" in runs.RETIRED_REASON


# --- the sidebar ------------------------------------------------------------------

class FakeSidebar:
    def __init__(self):
        self.written = []

    def markdown(self, body, **kwargs):
        self.written.append(body)


def test_a_retired_morning_is_grey_and_says_retired(monkeypatch):
    fake = FakeSidebar()
    monkeypatch.setattr(ui.st, "sidebar", fake)
    monkeypatch.setattr("services.automation_status.read_status",
                        lambda day, **kw: pytest.fail("a retired date must not read a status file"))
    ui.sidebar_health("2026-09-15")
    markup = fake.written[-1]
    assert 'class="egx-health retired"' in markup
    assert "Morning run retired" in markup
    assert "No run recorded" not in markup and "refused" not in markup.lower()


def test_the_retired_tone_is_its_own_appearance(monkeypatch):
    captured = []
    monkeypatch.setattr(ui.st, "markdown", lambda body, **kw: captured.append(body))
    ui.apply_global_style()
    css = captured[0]
    rules = {tone: re.search(rf"\.egx-health\.{tone}\s*\{{(.*?)\}}", css, re.S)
             for tone in ("ok", "warn", "bad", "unknown", "retired")}
    assert rules["retired"], "no rule for the retired state"
    others = {rules[t].group(1) for t in ("ok", "warn", "bad", "unknown")}
    assert rules["retired"].group(1) not in others
    assert "--green" not in rules["retired"].group(1)
    assert "--red" not in rules["retired"].group(1)


def test_a_date_before_the_retirement_still_goes_through_the_file(monkeypatch):
    fake = FakeSidebar()
    monkeypatch.setattr(ui.st, "sidebar", fake)
    monkeypatch.setattr("services.automation_status.read_status",
                        lambda day, **kw: runs.AutomationStatus(
                            session_date=day, outcome=runs.NEVER_RAN, reason=""))
    ui.sidebar_health("2026-09-10")
    assert 'class="egx-health bad"' in fake.written[-1]


# --- the terminal rail -------------------------------------------------------------

def test_the_terminal_rail_reports_retired_without_an_alarm_colour():
    assert terminal.run_status_reading("2026-09-15") == ("retired", "")


# --- the scheduled-task verifier -----------------------------------------------------

def test_the_verifier_expects_the_task_retired_not_running():
    source = Path("scripts/windows/verify_scheduled_tasks.ps1").read_text(encoding="utf-8")
    declared = source.split("$declared = @(", 1)[1].split("$retired = @(", 1)[0]
    retired = source.split("$retired = @(", 1)[1].split(")", 1)[0]
    assert "EGX ORB Full Shadow Automation" not in declared
    assert '"EGX ORB Full Shadow Automation"' in retired


# --- the price source ----------------------------------------------------------------

def row(provider="rubix", freshness="FRESH", quote_status=None):
    r = {"LiveProvider": provider, "LivePriceStatus": freshness}
    if quote_status is not None:
        r["RubixQuoteStatus"] = quote_status
    return r


def test_fresh_quotes_are_the_live_source_in_green():
    assert home.price_source_reading([row(), row()]) == ("Rubix", "green", "")


def test_a_stale_quote_is_never_green():
    """The case that happened: every quote five days old, labelled live."""
    value, tone, sub = home.price_source_reading([row(freshness="STALE")] * 3)
    assert (value, tone, sub) == ("Rubix", "amber", "قديم")


def test_one_stale_row_downgrades_the_whole_cell():
    """Same rule as the scan banner: fresh alone is never enough."""
    assert home.price_source_reading([row(), row(freshness="STALE")])[1] == "amber"


def test_a_non_current_rubix_status_is_stale_too():
    assert home.price_source_reading(
        [row(freshness="", quote_status="RUBIX_PREVIOUS_SESSION")])[1] == "amber"


def test_no_quote_at_all_is_unavailable_not_a_source():
    assert home.price_source_reading([row(provider="unavailable"), {}]) == (
        "Unavailable", "unknown", "")
    assert home.price_source_reading([]) == ("Unavailable", "unknown", "")


def test_the_strip_uses_the_reading():
    import inspect

    source = inspect.getsource(home.show_dashboard)
    assert "price_source_reading(" in source
