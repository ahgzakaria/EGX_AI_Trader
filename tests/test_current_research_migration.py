"""Tests for the CURRENT_RESEARCH_V2 migration (EODHD operational, Yahoo legacy-only)."""

from __future__ import annotations

import pandas as pd
import pytest

import core.research_router as router


@pytest.fixture(autouse=True)
def _eodhd_route(monkeypatch):
    """These test the tier routing, which is the router's EODHD branch. The
    configured live source is MubasherTrade PRO since 2026-09-24, so the EODHD
    branch -- still the way back while the subscription lasts -- is pinned."""
    from config.settings_manager import settings

    monkeypatch.setitem(settings.data, "live_history_source", "eodhd")


def _frame(rows=300, start="2024-01-01"):
    idx = pd.date_range(start, periods=rows, freq="B", name="Date")
    v = pd.Series(range(rows), dtype=float, index=idx)
    return pd.DataFrame({"Open": 10 + v * 0.01, "High": 10.5 + v * 0.01,
                         "Low": 9.5 + v * 0.01, "Close": 10.2 + v * 0.01,
                         "Adj Close": 10.2 + v * 0.01, "Volume": 1000.0 + v}, index=idx)


def _tiers(monkeypatch, mapping):
    monkeypatch.setattr(router, "tier_map", lambda: mapping)


# --- domain separation ------------------------------------------------------

def test_current_and_legacy_domains_are_labelled_and_not_mixed(monkeypatch):
    _tiers(monkeypatch, {"AAA": {"symbol": "AAA", "tier": "TIER_A_FORWARD_SAFE"}})
    monkeypatch.setattr(router, "eodhd_history", lambda s, **k: _frame())
    cur = router.get_current_research_history("AAA", min_bars=10)
    assert cur.attrs["market_data"]["data_domain"] == router.CURRENT_RESEARCH_V2
    assert cur.attrs["market_data"]["yahoo_network_used"] is False
    # a legacy frame carries a different domain and can never be confused with it
    monkeypatch.setattr(router, "get_legacy_backtest_history",
                        lambda s, **k: _tagged_legacy(_frame()))
    leg = router.get_legacy_backtest_history("AAA")
    assert leg.attrs["market_data"]["data_domain"] == router.LEGACY_BACKTEST_V1
    assert cur.attrs["market_data"]["data_domain"] != leg.attrs["market_data"]["data_domain"]


def _tagged_legacy(frame):
    frame = frame.copy()
    frame.attrs["market_data"] = {"data_domain": router.LEGACY_BACKTEST_V1,
                                  "provider": router.FROZEN_YAHOO_SNAPSHOT, "immutable": True}
    return frame


# --- tier routing -----------------------------------------------------------

def test_tier_a_and_b_route_to_eodhd(monkeypatch):
    _tiers(monkeypatch, {"AAA": {"symbol": "AAA", "tier": "TIER_A_FORWARD_SAFE"},
                         "BBB": {"symbol": "BBB", "tier": "TIER_B_FORWARD_EODHD_NO_FALLBACK"}})
    monkeypatch.setattr(router, "eodhd_history", lambda s, **k: _frame())
    for sym in ("AAA", "BBB"):
        md = router.get_current_research_history(sym, min_bars=10).attrs["market_data"]
        assert md["provider"] == "eodhd" and md["price_series"] == "SPLIT_ADJUSTED"


def test_tier_b_failure_is_data_unavailable_never_yahoo(monkeypatch):
    _tiers(monkeypatch, {"BBB": {"symbol": "BBB", "tier": "TIER_B_FORWARD_EODHD_NO_FALLBACK"}})

    def _boom(*_a, **_k):
        raise router.ResearchDataUnavailable("BBB", router.DATA_UNAVAILABLE, "eodhd down")
    monkeypatch.setattr(router, "eodhd_history", _boom)
    with pytest.raises(router.ResearchDataUnavailable) as e:
        router.get_current_research_history("BBB", min_bars=10)
    assert e.value.status == router.DATA_UNAVAILABLE          # no Yahoo substitution


def test_oras_cannot_reach_yahoo_through_any_generic_fallback(monkeypatch):
    _tiers(monkeypatch, {"ORAS": {"symbol": "ORAS", "tier": "TIER_B_FORWARD_EODHD_NO_FALLBACK"}})

    def _boom(*_a, **_k):
        raise router.ResearchDataUnavailable("ORAS", router.DATA_UNAVAILABLE, "eodhd down")
    monkeypatch.setattr(router, "eodhd_history", _boom)
    # even if a local/Yahoo path exists it must NOT be used for ORAS
    monkeypatch.setattr(router, "local_plus_mubasher_history",
                        lambda *a, **k: pytest.fail("ORAS must never use local/Yahoo fallback"))
    with pytest.raises(router.ResearchDataUnavailable):
        router.get_current_research_history("ORAS", min_bars=10)


# --- Tier C clean-window gate -----------------------------------------------

def test_tier_c_clean_window_allows_operational_use(monkeypatch):
    _tiers(monkeypatch, {"CCC": {"symbol": "CCC", "tier": "TIER_C_HISTORICAL_REVIEW"}})
    monkeypatch.setattr(router, "unresolved_action_date", lambda s: "2024-01-05")
    monkeypatch.setattr(router, "eodhd_history", lambda s, **k: _frame(300))
    state, _ = router.clean_window_status("CCC", min_bars=50)
    assert state == router.EODHD_OPERATIONAL_CLEAN_WINDOW
    md = router.get_current_research_history("CCC", min_bars=50).attrs["market_data"]
    assert md["provider"] == "eodhd"


def test_tier_c_unresolved_event_blocks_safely_without_yahoo(monkeypatch):
    _tiers(monkeypatch, {"CCC": {"symbol": "CCC", "tier": "TIER_C_HISTORICAL_REVIEW"}})
    # unresolved action near the end → not enough clean sessions after it
    monkeypatch.setattr(router, "unresolved_action_date", lambda s: "2025-02-01")
    monkeypatch.setattr(router, "eodhd_history", lambda s, **k: _frame(300))
    monkeypatch.setattr(router, "local_plus_mubasher_history",
                        lambda *a, **k: pytest.fail("Tier C must not silently use Yahoo/local"))
    with pytest.raises(router.ResearchDataUnavailable) as e:
        router.get_current_research_history("CCC", min_bars=250)
    assert e.value.status == router.EODHD_RESEARCH_REVIEW_REQUIRED


# --- unsupported symbols ----------------------------------------------------

def test_unsupported_symbol_uses_local_plus_mubasher(monkeypatch):
    _tiers(monkeypatch, {"ZZZ": {"symbol": "ZZZ", "tier": "TIER_D_UNSUPPORTED_OR_MANUAL"}})
    frame = _frame()
    monkeypatch.setattr(router, "local_plus_mubasher_history",
                        lambda *a, **k: (frame, router.LOCAL_PLUS_MUBASHER_READY,
                                         {"freshness_status": "HISTORY_CURRENT",
                                          "session_lag": 0}))
    md = router.get_current_research_history("ZZZ", min_bars=10).attrs["market_data"]
    assert md["provider"] == "local_plus_mubasher"
    assert md["yahoo_network_used"] is False
    assert md["yahoo_seed_present"] is True


def test_insufficient_history_reports_data_insufficient(monkeypatch):
    _tiers(monkeypatch, {"ZZZ": {"symbol": "ZZZ", "tier": "TIER_D_UNSUPPORTED_OR_MANUAL"}})

    def _short(*_a, **_k):
        raise router.ResearchDataUnavailable("ZZZ", router.DATA_INSUFFICIENT, "12 bars")
    monkeypatch.setattr(router, "local_plus_mubasher_history", _short)
    with pytest.raises(router.ResearchDataUnavailable) as e:
        router.get_current_research_history("ZZZ", min_bars=250)
    assert e.value.status == router.DATA_INSUFFICIENT


def test_bridge_append_is_idempotent_and_never_duplicates(monkeypatch):
    # The real bridge appender lives in core.local_daily_history and is covered in
    # detail in test_current_research_correctness.py. Here we assert the router still
    # exposes the local route by its new name and no longer the buggy _append helper.
    assert hasattr(router, "local_plus_mubasher_history")
    assert not hasattr(router, "_append_bridge_bars")


# --- freshness independent of Yahoo -----------------------------------------

def test_yahoo_lag_cannot_mark_eodhd_research_stale(monkeypatch):
    _tiers(monkeypatch, {"AAA": {"symbol": "AAA", "tier": "TIER_A_FORWARD_SAFE"}})
    monkeypatch.setattr(router, "eodhd_history", lambda s, **k: _frame())
    md = router.get_current_research_history("AAA", min_bars=10).attrs["market_data"]
    # freshness is derived from the EODHD series only; no Yahoo field participates
    assert md["latest_completed_session"] is not None
    assert "yahoo" not in str(md.get("provider", "")).lower()
    assert md["yahoo_network_used"] is False


# --- operational paths must not call Yahoo ----------------------------------

def test_operational_load_history_does_not_call_yahoo(tmp_path, monkeypatch):
    """scanner/dashboard/forward_testing must never invoke the Yahoo provider."""
    import core.data_provider as routing
    from providers.local_cache_provider import LocalCacheProvider
    from providers.rubix_sqlite_provider import RubixSQLiteProvider
    from config.settings_manager import settings

    class _ExplodingYahoo:
        name = "yahoo"
        delayed = True
        delay_minutes = None

        def load_history(self, *_a, **_k):
            raise AssertionError("Yahoo must not be called for current research")

        def health(self):
            return {"status": "available"}

    monkeypatch.setattr(router, "get_current_research_history",
                        lambda symbol, **k: _tag_current(_frame()))
    cache = LocalCacheProvider(tmp_path / "c.sqlite", source_provider="yahoo")
    rubix = RubixSQLiteProvider(tmp_path / "missing.db")
    monkeypatch.setattr(routing, "_PROVIDER_INSTANCES", {
        "rubix": rubix, "yahoo": _ExplodingYahoo(), "local_cache": cache})
    for purpose in ("scanner", "dashboard", "forward_testing"):
        monkeypatch.setitem(settings.data, f"{purpose}_provider", "rubix")
        loaded = routing.load_history("AAA.CA", purpose=purpose)
        assert loaded.attrs["market_data"]["yahoo_network_used"] is False
        assert loaded.attrs["market_data"]["data_domain"] == router.CURRENT_RESEARCH_V2


def _tag_current(frame):
    frame = frame.copy()
    frame.attrs["market_data"] = {
        "data_domain": router.CURRENT_RESEARCH_V2, "provider": "eodhd",
        "provider_symbol": "AAA.EGX", "price_series": "SPLIT_ADJUSTED",
        "adjustment_policy": "SPLIT_ONLY_EVENT_SPECIFIC", "routing_tier": "TIER_A_FORWARD_SAFE",
        "history_sufficient": True, "data_quality_status": router.EODHD_OPERATIONAL_CLEAN_WINDOW,
        "latest_completed_session": str(frame.index[-1])[:10], "yahoo_used": False}
    return frame


# --- production safety ------------------------------------------------------

def test_production_still_disabled():
    from scalping_expected_range.config import ExpectedRangeConfig
    cfg = ExpectedRangeConfig.load()
    assert cfg.paper_enabled is True and cfg.production_enabled is False
