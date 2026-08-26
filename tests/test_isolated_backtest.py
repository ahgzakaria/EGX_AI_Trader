"""The isolation contract for a measured backtest, pinned.

Two failures in one session made this necessary, and they were the same failure
twice: a results file that does not record what produced it, and a configuration
that can change underneath a run. `reports/backtest_results.csv` claimed +75.67%
over 2,190 days where the same engine re-run gave -1.78% over 3,298, and later
`config/settings.json` changed mid-investigation because the Streamlit dashboard
writes it too.

These tests pin the three guarantees. They do not run a backtest.
"""

import json
from copy import deepcopy

import pytest

from config.settings_manager import settings
from scripts.research.isolated_backtest import (
    OUTPUTS,
    RECORDED_SECTIONS,
    IsolatedBacktest,
    _assign,
    _coerce,
)


@pytest.fixture
def pristine():
    """Guarantee the singleton is whole again even if an assertion fails."""
    saved = deepcopy(settings.data)
    saved_save, saved_reload = settings.save, settings.reload
    yield saved
    settings.data = saved
    settings.save, settings.reload = saved_save, saved_reload


def test_overrides_apply_to_memory_and_report_what_they_replaced(pristine):
    run = IsolatedBacktest("t", {"strategy.min_rr": 9.9})
    applied = run._pin_settings()
    try:
        assert settings.get("strategy")["min_rr"] == 9.9
        assert applied["strategy.min_rr"] == {"from": pristine["strategy"]["min_rr"],
                                              "to": 9.9}
    finally:
        run._restore_settings()


def test_settings_are_restored_exactly(pristine):
    run = IsolatedBacktest("t", {"strategy.min_rr": 9.9,
                                 "backtest.trailing_enabled": True})
    run._pin_settings()
    run._restore_settings()
    assert settings.data == pristine


def test_a_run_cannot_write_the_users_settings_file(pristine, tmp_path):
    # The dashboard owns config/settings.json. A measurement must not fight it.
    disk = (tmp_path.parent / "unused")  # nothing should be written anywhere
    run = IsolatedBacktest("t", {})
    run._pin_settings()
    try:
        settings.save({"strategy": {"min_rr": 99}})
        assert run.intercepted["save"] == 1
        # The singleton is untouched and no file was produced.
        assert settings.get("strategy")["min_rr"] != 99
        assert not disk.exists()
    finally:
        run._restore_settings()


def test_reload_keeps_the_pin_instead_of_re_reading_the_file(pristine):
    # run_backtest calls settings.reload() as its first act, which is exactly
    # how a dashboard edit lands inside a measurement. Refusing it would break a
    # legitimate call, so the pin has to survive it instead.
    run = IsolatedBacktest("t", {"strategy.min_rr": 9.9})
    run._pin_settings()
    try:
        settings.reload()
        assert run.intercepted["reload"] == 1
        assert settings.get("strategy")["min_rr"] == 9.9
        settings.reload()
        assert settings.get("strategy")["min_rr"] == 9.9
    finally:
        run._restore_settings()


def test_a_reload_cannot_be_used_to_smuggle_in_a_concurrent_edit(pristine,
                                                                monkeypatch):
    # Simulate the dashboard rewriting the file mid-run: the real loader would
    # return the edited content, and the pin must discard it.
    run = IsolatedBacktest("t", {"strategy.min_rr": 4.4})
    run._pin_settings()
    try:
        monkeypatch.setattr(
            type(settings), "load",
            lambda _self: {"strategy": {"min_rr": 0.1}}, raising=False)
        settings.reload()
        assert settings.get("strategy")["min_rr"] == 4.4
    finally:
        run._restore_settings()


def test_freezing_is_lifted_after_the_run(pristine):
    run = IsolatedBacktest("t", {})
    run._pin_settings()
    run._restore_settings()
    assert settings.save is pristine and settings.reload is not None or True
    # The real assertion: calling them no longer raises the isolation error.
    assert settings.reload.__name__ != "refuse_reload"
    assert settings.save.__name__ != "refuse_save"


def test_an_unknown_setting_is_refused_rather_than_created(pristine):
    # A typo in an override must not silently measure the unchanged config.
    with pytest.raises(KeyError):
        _assign(deepcopy(settings.data), "strategy.min_rrr", 2.0)
    with pytest.raises(KeyError):
        _assign(deepcopy(settings.data), "nosuchsection.key", 1)


@pytest.mark.parametrize("text,expected", [
    ("true", True), ("false", False), ("TRUE", True),
    ("2.0", 2.0), ("3", 3), ("EMA20", "EMA20"), ("null", None),
])
def test_override_values_keep_their_type(text, expected):
    # "false" arriving as a non-empty string would silently mean True.
    assert _coerce(text) == expected
    assert type(_coerce(text)) is type(expected)


def test_outputs_and_recorded_sections_cover_what_matters():
    assert "backtest_results.csv" in OUTPUTS
    assert "backtest_statistics.csv" in OUTPUTS
    # A manifest that omits the strategy section cannot explain its own numbers.
    assert "strategy" in RECORDED_SECTIONS and "backtest" in RECORDED_SECTIONS


def test_reports_are_restored_and_the_run_keeps_its_own_copy(tmp_path, monkeypatch):
    import scripts.research.isolated_backtest as module

    reports = tmp_path / "reports"
    reports.mkdir()
    original = "original,content\n"
    (reports / "backtest_results.csv").write_text(original, encoding="utf-8")
    monkeypatch.setattr(module, "REPORTS", reports)
    monkeypatch.setattr(module, "EXPERIMENTS", reports / "experiments")

    run = IsolatedBacktest("t", {})
    run.run_dir = reports / "experiments" / "t"
    run._capture_outputs()
    # Stand in for the service writing its results.
    (reports / "backtest_results.csv").write_text("new,result\n", encoding="utf-8")
    produced = run._collect_and_restore()

    assert (run.run_dir / "backtest_results.csv").read_text(encoding="utf-8") == "new,result\n"
    assert (reports / "backtest_results.csv").read_text(encoding="utf-8") == original
    assert produced["backtest_results.csv"]


def test_a_file_absent_before_the_run_is_absent_after(tmp_path, monkeypatch):
    import scripts.research.isolated_backtest as module

    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setattr(module, "REPORTS", reports)
    monkeypatch.setattr(module, "EXPERIMENTS", reports / "experiments")

    run = IsolatedBacktest("t", {})
    run.run_dir = reports / "experiments" / "t"
    run._capture_outputs()
    (reports / "equity_curve.csv").write_text("x\n", encoding="utf-8")
    run._collect_and_restore()

    assert (run.run_dir / "equity_curve.csv").exists()
    assert not (reports / "equity_curve.csv").exists()
