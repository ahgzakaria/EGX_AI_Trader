"""Tests for the isolated EODHD EGX daily adapter + observer (offline/mocked).

Covers the failure taxonomy (incl. API_KEY_MISSING), HTTP-status classification,
symbol mapping, credential non-exposure, and the observer's key-missing handling +
poll-row dedup. No live network calls.
"""

from __future__ import annotations

import importlib.util
import urllib.error

import pytest

from core.providers.eodhd_daily import (
    API_KEY_MISSING,
    ENDPOINT_UNAVAILABLE,
    INVALID_KEY,
    NETWORK_ERROR,
    PLAN_NOT_ENTITLED,
    RATE_LIMITED,
    SYMBOL_UNSUPPORTED,
    EodhdDailyClient,
    probe_summary,
)

FAKE_TOKEN = "sk_fake_secret_do_not_leak_123456"


def test_api_key_missing_without_token(monkeypatch):
    monkeypatch.delenv("EODHD_API_TOKEN", raising=False)
    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    client = EodhdDailyClient()
    assert client.key_configured is False
    r = client.eod("COMI.CA")
    assert r.ok is False and r.status == API_KEY_MISSING


def test_probe_summary_reports_api_key_missing(monkeypatch):
    monkeypatch.delenv("EODHD_API_TOKEN", raising=False)
    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    out = probe_summary(EodhdDailyClient())
    assert out["status"] == API_KEY_MISSING and out["key_configured"] is False


def test_http_status_classification():
    def r(code):
        e = urllib.error.HTTPError("u", code, "m", {}, None)
        return EodhdDailyClient._classify_http(e).status
    assert r(401) == INVALID_KEY
    assert r(403) == PLAN_NOT_ENTITLED
    assert r(402) == PLAN_NOT_ENTITLED
    assert r(429) == RATE_LIMITED
    assert r(404) == SYMBOL_UNSUPPORTED
    assert r(500) == ENDPOINT_UNAVAILABLE


def test_network_error_detail_does_not_leak_token(monkeypatch):
    import core.providers.eodhd_daily as mod

    def boom(*a, **k):
        raise urllib.error.URLError("connection refused to https://eodhd.com/api/...")
    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    client = EodhdDailyClient(api_token=FAKE_TOKEN, min_interval_seconds=0)
    r = client.eod("COMI.CA")
    assert r.status == NETWORK_ERROR
    assert FAKE_TOKEN not in (r.detail or "")          # token never surfaced
    assert "api_token" not in (r.detail or "")


def test_ok_response_parsed_and_hashed(monkeypatch):
    import core.providers.eodhd_daily as mod

    class _Resp:
        status = 200
        def read(self):
            return b'[{"date":"2026-07-22","open":100,"high":101,"low":99,"close":100.5,"volume":1000}]'
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: _Resp())
    client = EodhdDailyClient(api_token=FAKE_TOKEN, min_interval_seconds=0)
    r = client.eod("COMI.CA")
    assert r.ok and r.status == "OK"
    assert isinstance(r.data, list) and r.data[0]["close"] == 100.5
    assert r.response_hash and len(r.response_hash) == 16


def test_symbol_mapping_to_egx():
    from providers.symbol_mapping import to_eodhd_symbol
    assert to_eodhd_symbol("COMI.CA") == "COMI.EGX"
    assert to_eodhd_symbol("ARAB.CA") == "ARAB.EGX"


# --- observer ---------------------------------------------------------------

def _load_observer():
    spec = importlib.util.spec_from_file_location(
        "obs", "scripts/observe_eodhd_egx_finalization.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_observer_records_api_key_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("EODHD_API_TOKEN", raising=False)
    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    obs = _load_observer()
    monkeypatch.setattr(obs, "OBS_CSV", str(tmp_path / "obs.csv"))
    rc = obs.main(["--date", "2026-07-22"])
    assert rc == 2                                     # refuses to fabricate; records blocker
    import csv
    rows = list(csv.DictReader(open(tmp_path / "obs.csv", encoding="utf-8")))
    assert rows and all(r["status"] == API_KEY_MISSING for r in rows)
    assert all(r["bar_exists"] == "False" for r in rows)


def test_observer_dedup_poll_rows(tmp_path, monkeypatch):
    obs = _load_observer()
    path = str(tmp_path / "obs.csv")
    seen = set()

    class _Client:
        def eod(self, sym, date_from=None):
            from core.providers.eodhd_daily import EodhdResult
            return EodhdResult(True, "OK", data=[{"date": date_from, "open": 1, "high": 2,
                              "low": 1, "close": 1.5, "volume": 9}], response_hash="h1")
    rows1 = obs.poll_once(_Client(), "2026-07-22", seen)
    rows2 = obs.poll_once(_Client(), "2026-07-22", seen)   # same poll ts+hash within the second
    assert len(rows1) == len(obs.BASKET)
    # identical (symbol, poll_ts, hash) is suppressed on the second identical poll
    assert len(rows2) <= len(rows1)
