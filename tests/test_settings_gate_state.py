"""A setting can be ON and still decide nothing, and the page has to say which.

This is the defect that shaped the whole product: `require_market_analyzer`
read true in the settings for years, over an index no provider served, so the
gate could not fire and every decision trace reported `MarketAnalyzer: PASS`.
The settings screen could show what a switch was set to and nothing about
whether the check behind it could run.

Three states, and they are three different facts:

    LIVE   on, and the check behind it can run
    OFF    off by choice
    INERT  on, but the check cannot run at all

And System Health's tiles now carry a tone, because that is the page somebody
opens when they are worried.

Presentation only: no setting is written and no threshold is invented here.
"""

from __future__ import annotations

import inspect

import pytest

from dashboard import settings as settings_page
from dashboard import system_health
from dashboard.settings import INERT, LIVE, OFF, market_analyzer_state


class FakeStore(dict):
    pass


@pytest.fixture
def index_present(monkeypatch):
    monkeypatch.setattr("core.frozen_mubasher_store.frozen_indices",
                        lambda root=None: ["^CASE30"])


@pytest.fixture
def index_absent(monkeypatch):
    monkeypatch.setattr("core.frozen_mubasher_store.frozen_indices",
                        lambda root=None: [])


def test_on_with_an_index_is_live(index_present):
    state, _ = market_analyzer_state({"require_market_analyzer": True})
    assert state == LIVE


def test_off_with_an_index_is_a_decision_not_a_defect(index_present):
    state, why = market_analyzer_state({"require_market_analyzer": False})
    assert state == OFF
    assert "بقرار" in why


def test_on_without_an_index_is_inert_and_says_so(index_absent):
    """The exact state that shipped for years reading as working."""
    state, why = market_analyzer_state({"require_market_analyzer": True})
    assert state == INERT
    assert "لا ترفض" in why


def test_off_without_an_index_is_still_inert_rather_than_a_clean_off(index_absent):
    """The switch is off, but the check could not run either way, and a reader
    turning it on would get nothing."""
    state, _ = market_analyzer_state({"require_market_analyzer": False})
    assert state == INERT


def test_a_missing_store_does_not_claim_the_gate_works(monkeypatch):
    def explode(root=None):
        raise OSError("no store")

    monkeypatch.setattr("core.frozen_mubasher_store.frozen_indices", explode)
    state, _ = market_analyzer_state({"require_market_analyzer": True})
    assert state == INERT


def test_the_three_states_are_labelled_and_toned_apart():
    labels = settings_page._STATE_LABEL
    assert set(labels) == {LIVE, OFF, INERT}
    assert len({tone for _, tone in labels.values()}) == 3
    assert labels[INERT][1] == "unknown"        # the violet, like every unknown


def test_the_state_is_read_now_rather_than_quoted_from_the_stored_table():
    """The table is a measurement from a date. The switch has a state today,
    and the two stopped agreeing the moment the index was frozen."""
    source = inspect.getsource(settings_page._show_gate_impact)
    assert "market_analyzer_state(settings.get(" in source


def test_the_panel_no_longer_asserts_the_gate_rejects_nothing():
    """It said so, and proved it by running the backtest both ways. That
    proof expired when the index arrived."""
    source = inspect.getsource(settings_page._show_gate_impact)
    assert "مفعّل ويرفض صفر إشارة" not in source
    assert "561" in source or "561" in inspect.getsource(settings_page)


def test_the_stored_zero_is_annotated_rather_than_silently_wrong():
    source = inspect.getsource(settings_page)
    # Split on the tuple's own terminator: a bare ")" lands inside a label.
    table = source.split("MEASURED_GATE_IMPACT = (")[1].split("\n)")[0]
    assert "2026-08-18" in table and "609" in table


# --- System Health tones --------------------------------------------------------

def test_health_tiles_carry_a_tone():
    """Every tile read as plain text: a Rubix database reporting FAILED looked
    exactly like one reporting HEALTHY."""
    source = inspect.getsource(system_health.show_system_health)
    assert "metric_card(" in source
    assert '"HEALTHY": "green"' in source and '"FAILED": "red"' in source


def test_the_disk_threshold_is_the_services_own():
    """1% free is what `collect_system_health` already treats as unhealthy.
    The page must not invent a second threshold beside it."""
    page = inspect.getsource(system_health.show_system_health)
    service = inspect.getsource(
        __import__("services.system_health", fromlist=["x"]))
    assert "free_percent < 1" in page
    assert "disk_free_pct < 1" in service


def test_a_fallback_provider_is_flagged_rather_than_just_named():
    source = inspect.getsource(system_health.show_system_health)
    assert "fallback_active" in source
    assert 'tone="amber" if fallback' in source


# --- the health page's own cost -------------------------------------------------

def test_the_page_does_not_walk_every_database_page_on_every_render():
    """`PRAGMA integrity_check` over the 7.7 GB Rubix store measured 140
    seconds, on the one page somebody opens when something is already wrong."""
    source = inspect.getsource(system_health.show_system_health)
    assert "deep=deep" in source
    assert "_system_health_deep" in source


def test_the_full_scan_is_still_reachable():
    source = inspect.getsource(system_health.show_system_health)
    assert "Full integrity scan" in source


def test_a_quick_probe_is_not_reported_as_an_integrity_check(tmp_path):
    import sqlite3

    from services.system_health import _sqlite_health

    path = tmp_path / "probe.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE t (a INTEGER)")

    quick = _sqlite_health(path)
    deep = _sqlite_health(path, deep=True)
    assert quick["check"] == "quick" and quick["integrity"] == "not checked"
    assert deep["check"] == "integrity" and deep["integrity"] == "ok"
    assert quick["status"] == deep["status"] == "HEALTHY"


def test_the_tile_says_which_check_it_got():
    source = inspect.getsource(system_health.show_system_health)
    assert 'check == "integrity"' in source


def test_a_file_that_is_not_a_database_fails_the_quick_probe_too(tmp_path):
    """The cheap check still has to catch a truncated or wrong file."""
    from services.system_health import _sqlite_health

    path = tmp_path / "junk.db"
    path.write_bytes(b"this is not a database" * 100)
    assert _sqlite_health(path)["status"] == "FAILED"


def test_a_missing_file_is_missing_under_either_check(tmp_path):
    from services.system_health import _sqlite_health

    path = tmp_path / "gone.db"
    for deep in (False, True):
        record = _sqlite_health(path, deep=deep)
        assert record["status"] == "MISSING"
        assert record["check"] == "none"
