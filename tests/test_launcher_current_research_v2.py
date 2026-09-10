"""Rubix Production Launcher V2 — CURRENT_RESEARCH_V2 alignment (no operational Yahoo).

Headless tests: no Tk window is constructed. They verify the UI contract, the
research/market/safety status helpers, process-lifecycle guards, and stop-safety.
"""

from __future__ import annotations

import inspect

import pytest

import scripts.launch_rubix_production as launcher
import scripts.launcher_process_utils as process_utils
from services import research_launcher_status as status


# --------------------------------------------------------------------------- #
# 1. Yahoo removed from the launcher UI
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def _isolated_stop_flag(tmp_path, monkeypatch):
    """Never write the production stop flag.

    These tests exercise the real request_collector_stop, which writes
    data/runtime/stop_requested.flag -- the exact file a live supervisor polls
    to shut itself down. On 2026-09-10 that stopped the collector twelve
    minutes before the close, and the same thing on 2026-09-07 cost four hours
    of a session and went unexplained until the stop path learned to name its
    caller:

        14:18:09 stage=stop Asked the collector to stop cleanly.
          Requested by test_stop_only_touches_launcher_owned_processes <- pytest

    The tests were right about the behaviour and wrong about the address. The
    default still has to equal the production path -- a separate test asserts
    that and must keep doing so -- but nothing here may write to it.
    """

    from scripts import launch_rubix_production as launcher

    monkeypatch.setattr(launcher, "COLLECTOR_STOP_FLAG",
                        tmp_path / "stop_requested.flag")

def test_no_start_yahoo_only_button_or_method():
    ui = launcher.RubixAuthenticationAssistantUI
    assert not hasattr(ui, "start_yahoo_only")
    assert hasattr(ui, "start_research_only")
    src = inspect.getsource(launcher)
    assert "Start Yahoo Only" not in src
    assert "Start Rubix & App" in src
    assert "Start Research Only" in src
    assert "Refresh Status" in src


def test_no_yahoo_fallback_status_field():
    keys = {key for _s, fields in launcher.RubixAuthenticationAssistantUI.STATUS_SECTIONS
            for _l, key in fields}
    assert not any("yahoo" in k.lower() or "fallback" in k.lower() for k in keys)
    # provider cards name EODHD and Rubix, never Yahoo
    src = inspect.getsource(launcher.RubixAuthenticationAssistantUI._init_static_status)
    assert '"EODHD"' in src and '"Rubix"' in src


def test_no_broad_taskkill_of_python():
    src = inspect.getsource(launcher)
    assert "taskkill /F python" not in src.lower()
    assert 'taskkill", "/PID"' in src.replace("'", '"')      # only targeted PID kills


def test_rubix_failure_offers_research_only_not_yahoo():
    src = inspect.getsource(launcher.RubixAuthenticationAssistantUI._apply_start_result)
    assert "start_research_only" in src
    assert "Research Only" in src
    assert "Yahoo" not in src or "not used" in src           # no operational Yahoo offer


# --------------------------------------------------------------------------- #
# 3. EODHD authentication status (token never exposed)
# --------------------------------------------------------------------------- #

def test_missing_eodhd_token_reports_invalid(monkeypatch):
    monkeypatch.setattr(status, "eodhd_token_configured", lambda: False)
    result = status.verify_eodhd_authentication(online=False)
    assert result["auth"] == status.AUTH_INVALID
    assert result["mode"] == status.TOKEN_MISSING


def test_token_present_offline_is_unknown(monkeypatch):
    monkeypatch.setattr(status, "eodhd_token_configured", lambda: True)
    result = status.verify_eodhd_authentication(online=False)
    assert result["auth"] == status.AUTH_UNKNOWN          # not verified, not a network call


def test_current_research_status_never_returns_token(monkeypatch):
    monkeypatch.setattr(status, "eodhd_token_configured", lambda: True)
    result = status.current_research_status(online=False)
    assert result["provider"] == "EODHD"
    text = " ".join(str(v) for v in result.values())
    # a real token is long; ensure no long opaque secret leaked into the status
    assert not any(len(tok) >= 20 and tok.isalnum() for tok in text.split())


def test_missing_token_blocks_research_data_mode(monkeypatch):
    monkeypatch.setattr(status, "eodhd_token_configured", lambda: False)
    result = status.current_research_status(online=False)
    assert result["token"] == "Missing"
    assert result["data_mode"] == "DATA_UNAVAILABLE"


# --------------------------------------------------------------------------- #
# 5. Holiday / weekend behavior via central calendar
# --------------------------------------------------------------------------- #

def test_holiday_marks_market_closed_and_needs_no_rubix():
    import datetime
    # 2026-07-24 is a Friday (EGX weekend)
    friday = datetime.datetime(2026, 7, 24, 11, 0)
    market = status.market_status(now=friday)
    assert market["market_closed"] is True
    assert market["is_trading_day"] is False
    assert market["phase"] == "MARKET_CLOSED"
    assert market["next_session"] is not None


def test_research_only_flow_does_not_start_rubix():
    # Start Research Only starts Streamlit only; it never calls start_collector.
    src = inspect.getsource(launcher.RubixAuthenticationAssistantUI._finish_research_start)
    assert "start_streamlit" in src
    assert "start_collector" not in src


# --------------------------------------------------------------------------- #
# 6. Safety panel — production disabled
# --------------------------------------------------------------------------- #

def test_safety_status_reports_production_disabled():
    safety = status.safety_status()
    assert safety["paper_mode"] is True
    assert safety["production_enabled"] is False
    assert safety["broker_orders_enabled"] is False
    assert safety["automatic_execution"] is False


# --------------------------------------------------------------------------- #
# 8/9. Duplicate prevention + stop safety (process lifecycle)
# --------------------------------------------------------------------------- #

def test_duplicate_supervisor_is_refused(tmp_path, monkeypatch):
    supervisor = launcher.ProductionSupervisor(database=tmp_path / "rubix.db")
    # A live supervisor is already recorded -> a second one must NOT be launched.
    monkeypatch.setattr(launcher, "supervisor_status",
                        lambda _path: {"running": True, "pid": 4321, "record": {"pid": 4321}})
    monkeypatch.setattr(launcher, "validate_adapter_path", lambda p: tmp_path)
    monkeypatch.setattr(launcher, "validate_auth_frame", lambda p: tmp_path / "auth.txt")
    started = {"popen": False}
    monkeypatch.setattr(launcher.subprocess, "Popen",
                        lambda *a, **k: started.__setitem__("popen", True))
    with pytest.raises(launcher.InstanceAlreadyRunning):
        supervisor.start_collector(tmp_path / "auth.txt", ["COMI"])
    assert started["popen"] is False


def test_stop_only_touches_launcher_owned_processes(tmp_path, monkeypatch):
    supervisor = launcher.ProductionSupervisor(database=tmp_path / "rubix.db")

    class _Proc:
        def __init__(self):
            self.pid = 12345
            self._alive = True

        def poll(self):
            return None if self._alive else 0

    killed = []
    supervisor.collector = _Proc()
    supervisor.streamlit = None
    supervisor._owns_streamlit = False
    monkeypatch.setattr(launcher, "stop_process", lambda proc, timeout=5: killed.append(proc))
    supervisor.stop()
    # only the launcher's own collector was stopped; no broad process sweep
    assert supervisor.collector is None


def test_rubix_process_matcher_never_matches_bare_python():
    assert process_utils.is_rubix_collector_process("python.exe app.py") is False
    assert process_utils.is_rubix_collector_process(
        "python scripts/rubix_collector_supervisor.py --x") is True
    assert process_utils.is_rubix_collector_process("python -m rubix_feed.cli") is True
    assert process_utils.is_rubix_collector_process("streamlit run app.py") is False


# --------------------------------------------------------------------------- #
# no operational Yahoo import in launcher startup
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# Unified launchers — both entry points open the same V2 launcher
# --------------------------------------------------------------------------- #

def _bat(name):
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / "scripts" / name).read_text(encoding="utf-8")


def test_both_bats_resolve_to_the_unified_v2_launcher():
    rubix_bat = _bat("start_rubix_production.bat")
    egx_bat = _bat("start_egx_ai_trader.bat")
    assert "launch_rubix_production.py" in rubix_bat
    assert "launch_rubix_production.py" in egx_bat        # repointed to V2
    assert "launch_egx_ai_trader.py" not in egx_bat        # no longer the divergent launcher


def test_one_click_launcher_module_redirects_and_has_no_yahoo_fallback():
    import scripts.launch_egx_ai_trader as one_click
    assert not hasattr(one_click, "failure_choice")
    assert not hasattr(one_click, "LauncherUI")
    src = inspect.getsource(one_click)
    # only disclaimer mentions of Yahoo remain; no operational fallback dialog/choice
    assert "Continue with Yahoo fallback" not in src
    assert '"fallback"' not in src
    assert "launch_rubix_production" in inspect.getsource(one_click.main)


def test_no_launcher_offers_start_yahoo_only():
    for name in ("launch_rubix_production.py", "launch_egx_ai_trader.py"):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "scripts" / name).read_text(encoding="utf-8")
        assert "Start Yahoo Only" not in src


def test_one_click_main_delegates_without_starting_yahoo(monkeypatch):
    import scripts.launch_egx_ai_trader as one_click
    called = {}

    def _fake_v2_main(argv=None):
        called["argv"] = argv
        return 0

    monkeypatch.setattr("scripts.launch_rubix_production.main", _fake_v2_main)
    rc = one_click.main(["--check"])
    assert rc == 0 and called["argv"] == ["--check"]


def test_launcher_startup_status_makes_no_yahoo_call(monkeypatch):
    # The offline research status must not construct a Yahoo provider or hit the network.
    import providers.yahoo_provider as yp

    class _Boom:
        def __init__(self, *a, **k):
            raise AssertionError("Yahoo must not be used by the launcher")

    monkeypatch.setattr(yp, "YahooProvider", _Boom)
    monkeypatch.setattr(status, "eodhd_token_configured", lambda: True)
    # offline path: no EODHD network probe, no Yahoo — returns a dict
    result = status.verify_eodhd_authentication(online=False)
    assert result["auth"] in (status.AUTH_UNKNOWN, status.AUTH_VALID, status.AUTH_INVALID)
