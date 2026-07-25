"""Authentication-assistant tests contain synthetic data only."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

from services.rubix_auth_assistant import (
    AUTH_EXPIRED,
    AUTH_INVALID,
    AUTH_MISSING,
    AUTH_VALID,
    inspect_auth_frame,
    load_launcher_preferences,
    run_preflight,
    safe_inspection_dict,
    save_launcher_preferences,
)
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
    assert "expired" in result.message.lower()


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
    states = {item.name: item.ok for item in results}
    assert states["Authentication File"] is False
    assert states["File Age"] is False
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
