"""Authentication-assistant tests contain synthetic data only."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

import pytest

from services.rubix_auth_assistant import (
    AUTH_EXPIRED,
    AUTH_INVALID,
    AUTH_MISSING,
    AUTH_VALID,
    FUTURE_CLOCK_SKEW_SECONDS,
    auth_preflight_items,
    format_auth_diagnostics,
    inspect_auth_frame,
    load_launcher_preferences,
    run_preflight,
    safe_auth_diagnostics,
    safe_inspection_dict,
    save_launcher_preferences,
)
from services.launcher_startup import WorkerEvent
from scripts.launch_rubix_production import RubixAuthenticationAssistantUI


NOW = datetime(2026, 7, 14, 10, 0, tzinfo=timezone.utc)


def _frame(tmp_path, payload, name="rubix-price-auth-frame.txt", age_minutes=0):
    path = tmp_path / name
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    modified = NOW - timedelta(minutes=age_minutes)
    os.utime(path, (modified.timestamp(), modified.timestamp()))
    return path


def test_missing_and_expired_files_have_explicit_states(tmp_path):
    assert inspect_auth_frame("").status == AUTH_MISSING
    assert inspect_auth_frame(tmp_path / "missing.json").status == AUTH_MISSING
    old = _frame(tmp_path, {"MT": -1, "TKN": "synthetic"}, age_minutes=16)
    result = inspect_auth_frame(old, now=NOW)
    assert result.status == AUTH_EXPIRED
    assert "960.0 seconds" in result.message


@pytest.mark.parametrize(
    ("age_minutes", "expected_status", "expected_age_valid"),
    (
        (0, AUTH_VALID, True),
        (14, AUTH_VALID, True),
        (15, AUTH_VALID, True),
        (16, AUTH_EXPIRED, False),
    ),
)
def test_filesystem_age_boundaries_are_independent_and_exact(
    tmp_path, age_minutes, expected_status, expected_age_valid
):
    path = _frame(
        tmp_path,
        {"MT": -1, "TKN": "synthetic"},
        name=f"age-{age_minutes}.json",
        age_minutes=age_minutes,
    )
    result = inspect_auth_frame(path, now=NOW)
    assert result.status == expected_status
    assert result.structure_valid is True
    assert result.file_age_valid is expected_age_valid
    assert result.file_age_seconds == pytest.approx(age_minutes * 60, abs=0.01)
    auth_item, age_item = auth_preflight_items(result)
    assert auth_item.display_status == "PASS"
    assert age_item.display_status == ("PASS" if expected_age_valid else "FAIL")
    assert f"{age_minutes * 60:.1f} seconds" in age_item.message


def test_cairo_utc_plus_three_uses_aware_utc_for_file_age(tmp_path):
    cairo = timezone(timedelta(hours=3))
    cairo_now = NOW.astimezone(cairo)
    path = _frame(tmp_path, {"MT": -1, "TKN": "synthetic"})
    result = inspect_auth_frame(path, now=cairo_now)
    assert result.current_utc == NOW.isoformat()
    assert result.current_local == cairo_now.isoformat()
    assert result.local_utc_offset == "+03:00"
    assert result.file_mtime_utc == NOW.isoformat()
    assert result.file_age_seconds == pytest.approx(0.0, abs=0.01)
    assert result.status == AUTH_VALID


def test_future_filesystem_timestamp_within_clock_skew_is_valid(tmp_path):
    path = _frame(
        tmp_path,
        {"MT": -1, "TKN": "synthetic"},
        age_minutes=-(FUTURE_CLOCK_SKEW_SECONDS / 60),
    )
    result = inspect_auth_frame(path, now=NOW)
    assert result.file_age_seconds == pytest.approx(
        -FUTURE_CLOCK_SKEW_SECONDS, abs=0.01
    )
    assert result.file_age_valid is True
    assert result.status == AUTH_VALID


@pytest.mark.parametrize(
    "timestamp",
    (
        int(NOW.timestamp()),
        int(NOW.timestamp() * 1000),
    ),
)
def test_embedded_unix_timestamp_seconds_and_milliseconds_are_diagnostic_only(
    tmp_path, timestamp
):
    path = _frame(
        tmp_path,
        {"MT": -1, "TKN": "synthetic", "timestamp": timestamp},
        name=f"timestamp-{timestamp}.json",
    )
    result = inspect_auth_frame(path, now=NOW)
    assert result.status == AUTH_VALID
    assert result.embedded_timestamp_found is True
    assert result.embedded_timestamp_utc == NOW.isoformat()
    assert result.embedded_age_seconds == pytest.approx(0.0, abs=0.01)
    assert auth_preflight_items(result)[1].name == "File Age"


def test_invalid_structure_with_fresh_mtime_does_not_false_fail_file_age(tmp_path):
    result = inspect_auth_frame(
        _frame(tmp_path, {"MT": 0}, name="fresh-invalid.json"),
        now=NOW,
    )
    assert result.status == AUTH_INVALID
    assert result.structure_valid is False
    assert result.file_age_valid is True
    auth_item, age_item = auth_preflight_items(result)
    assert auth_item.display_status == "FAIL"
    assert age_item.display_status == "PASS"
    assert "0.0 seconds" in age_item.message


def test_invalid_json_heartbeat_metadata_and_subscription_are_rejected(tmp_path):
    cases = (
        ("not-json", "neither JSON"),
        ({"MT": 0}, "heartbeat"),
        ({"HED": ["symbol"], "DAT": [["COMI"]]}, "metadata"),
        ({"MT": 10, "PRM": ["CASE~COMI"]}, "subscription"),
    )
    for index, (payload, expected) in enumerate(cases):
        result = inspect_auth_frame(_frame(tmp_path, payload, f"case-{index}.txt"), now=NOW)
        assert result.status == AUTH_INVALID
        assert expected.lower() in result.message.lower()


def test_valid_auth_frame_returns_safe_metadata_only(tmp_path):
    secret = "synthetic-never-expose-this"
    path = _frame(tmp_path, {"MT": -1, "TKN": secret})
    result = inspect_auth_frame(path, now=NOW)
    assert result.status == AUTH_VALID
    safe = json.dumps(safe_inspection_dict(result))
    assert secret not in safe
    assert "payload" not in safe.lower()
    assert "content" not in safe.lower()


def test_safe_diagnostics_and_launcher_log_text_never_expose_payload_secrets(tmp_path):
    secret = "synthetic-diagnostic-secret-never-log"
    path = _frame(
        tmp_path,
        {"MT": -1, "TKN": secret, "timestamp": int(NOW.timestamp())},
        name="safe-diagnostic-path.json",
    )
    result = inspect_auth_frame(path, now=NOW)
    diagnostics = safe_auth_diagnostics(result)
    rendered = format_auth_diagnostics(result)
    assert set(diagnostics) == {
        "current_utc",
        "current_local",
        "local_utc_offset",
        "selected_file_path",
        "filesystem_last_write_utc",
        "filesystem_age_seconds",
        "embedded_timestamp_found",
        "embedded_timestamp_utc",
        "embedded_age_seconds",
        "validation_decision",
        "validation_reason",
    }
    assert secret not in rendered
    assert "TKN" not in rendered
    assert "payload" not in rendered.lower()
    assert "cookie" not in rendered.lower()
    assert "authorization" not in rendered.lower()


def test_new_file_selection_ignores_older_file_result(monkeypatch, tmp_path):
    old = inspect_auth_frame(
        _frame(tmp_path, {"MT": -1, "TKN": "synthetic"}, age_minutes=16),
        now=NOW,
    )
    new = inspect_auth_frame(
        _frame(
            tmp_path,
            {"MT": -1, "TKN": "synthetic"},
            name="new-auth.json",
        ),
        now=NOW,
    )
    ui = RubixAuthenticationAssistantUI.__new__(RubixAuthenticationAssistantUI)
    ui._auth_selection_revision = 2
    applied = []
    ui._apply_auth_inspection = applied.append
    monkeypatch.setattr(
        "scripts.launch_rubix_production.log_event",
        lambda *_args, **_kwargs: None,
    )

    ui._handle_worker_result(WorkerEvent("result", "auth_inspection:1", old))
    assert applied == []
    ui._handle_worker_result(WorkerEvent("result", "auth_inspection:2", new))
    assert applied == [new]
    assert applied[0].status == AUTH_VALID


def test_documented_delimiter_based_price_auth_frame_is_valid(tmp_path):
    secret = "synthetic-session-value-never-expose"
    fields = {
        "150": "1", "20": secret, "24": "30", "1000": "1", "9": "1",
        "132": "1", "92": "2", "50": "2", "59": "1", "62": "WEB", "99": "1",
    }
    payload = "\x1c".join(f"{tag}\x02{value}" for tag, value in fields.items())
    result = inspect_auth_frame(_frame(tmp_path, payload), now=NOW)
    assert result.status == AUTH_VALID
    assert result.structure == "PRICE_AUTH_DELIMITED"
    assert secret not in json.dumps(safe_inspection_dict(result))


def test_incomplete_delimited_frame_is_rejected_without_exposure(tmp_path):
    secret = "synthetic-session-value-never-expose"
    result = inspect_auth_frame(_frame(tmp_path, f"20\x02{secret}\x1c62\x02WEB"), now=NOW)
    assert result.status == AUTH_INVALID
    assert secret not in json.dumps(safe_inspection_dict(result))


def test_launcher_preferences_never_persist_authentication_data(tmp_path):
    target = tmp_path / "launcher_settings.json"
    secret = "synthetic-secret"
    saved = save_launcher_preferences({
        "adapter_path": "C:/rubix_feed",
        "database_path": "D:/data/rubix.db",
        "last_auth_folder": "D:/temporary",
        "window_geometry": "1080x820",
        "theme": "System",
        "streamlit_port": 8501,
        "recent_launches": [{"mode": "rubix", "result": "ready"}],
        "auth_path": f"D:/temporary/{secret}.txt",
        "auth_frame": secret,
        "token": secret,
        "cookie": secret,
    }, target)
    raw = target.read_text(encoding="utf-8")
    assert secret not in raw
    assert "auth_path" not in raw
    assert saved == load_launcher_preferences(target)


def test_preflight_reports_each_required_component(tmp_path):
    project = tmp_path / "project"
    (project / "venv" / "Scripts").mkdir(parents=True)
    (project / "venv" / "Scripts" / "python.exe").write_text("", encoding="utf-8")
    (project / "scripts").mkdir()
    (project / "scripts" / "rubix_collector_supervisor.py").write_text("", encoding="utf-8")
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    for name in ("adapter.py", "cli.py", "protocol.py", "storage.py", "__init__.py"):
        (adapter / name).write_text("", encoding="utf-8")
    database = tmp_path / "data" / "rubix.db"
    database.parent.mkdir()
    auth = _frame(tmp_path, {"MT": -1, "TKN": "synthetic"})
    results = run_preflight(
        project, adapter, database, auth, 8501, now=NOW,
        module_available=lambda name: name == "streamlit",
    )
    assert [item.name for item in results] == [
        "Python", "Virtual Environment", "SQLite", "Rubix Database Path",
        "Authentication File", "File Age", "Launcher Configuration",
        "Collector", "Streamlit",
    ]
    assert all(item.ok for item in results)


def test_preflight_fails_cleanly_without_auth_or_collector(tmp_path):
    results = run_preflight(
        tmp_path, tmp_path / "missing-adapter", tmp_path / "missing" / "rubix.db",
        "", "bad-port", module_available=lambda _name: False,
    )
    items = {item.name: item for item in results}
    states = {name: item.ok for name, item in items.items()}
    assert states["Authentication File"] is False
    assert states["File Age"] is True
    assert items["File Age"].display_status == "SKIPPED"
    assert states["Collector"] is False
    assert states["Launcher Configuration"] is False
    assert states["Streamlit"] is False


def test_launcher_exposes_every_required_nontechnical_ui_state():
    # No Tk window is created; this verifies the CURRENT_RESEARCH_V2 UI contract in a
    # deterministic headless test. The panel is grouped and must not expose any Yahoo
    # operational provider or fallback.
    sections = {name for name, _fields in RubixAuthenticationAssistantUI.STATUS_SECTIONS}
    assert sections == {"Current Research", "Live Intraday", "Application", "Market", "Safety"}
    keys = {key for _section, fields in RubixAuthenticationAssistantUI.STATUS_SECTIONS
            for _label, key in fields}
    # required cards exist
    assert {"research_provider", "eodhd_token", "eodhd_auth", "research_freshness"} <= keys
    assert {"live_provider", "supervisor", "collector", "coverage", "value_progression"} <= keys
    assert {"paper_mode", "production", "broker"} <= keys
    # no Yahoo/fallback anywhere in the operational panel
    assert not any("yahoo" in key.lower() or "fallback" in key.lower() for key in keys)
