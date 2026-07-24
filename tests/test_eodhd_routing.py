"""Tests for the per-symbol routing policy + failure behavior (all inactive)."""

from __future__ import annotations

import json

from providers.eodhd_routing import load_policy, resolve_route


def _policy(**entries):
    return {k.upper(): {"symbol": k, **v} for k, v in entries.items()}


def test_eodhd_primary_normal_and_fallback():
    pol = _policy(SWDY={"policy": "EODHD_PRIMARY", "primary_provider": "eodhd",
                        "fallback_provider": "yahoo", "price_series": "SPLIT_ADJUSTED"})
    ok = resolve_route("SWDY", pol, eodhd_ok=True, yahoo_ok=True)
    assert ok["provider"] == "eodhd" and ok["series"] == "SPLIT_ADJUSTED" and not ok["fallback_used"]
    fb = resolve_route("SWDY", pol, eodhd_ok=False, yahoo_ok=True)
    assert fb["provider"] == "yahoo" and fb["fallback_used"] is True


def test_oras_never_falls_back_to_bad_yahoo():
    pol = _policy(ORAS={"policy": "EODHD_PRIMARY", "primary_provider": "eodhd",
                        "fallback_provider": None, "yahoo_forbidden": True,
                        "price_series": "SPLIT_ADJUSTED"})
    assert resolve_route("ORAS", pol, eodhd_ok=True)["provider"] == "eodhd"
    # EODHD down: must NOT use Yahoo (known bad) → DATA_UNAVAILABLE
    fail = resolve_route("ORAS", pol, eodhd_ok=False, yahoo_ok=True)
    assert fail["provider"] is None and fail["policy"] == "DATA_UNAVAILABLE"


def test_eodhd_only_returns_unavailable_without_fallback():
    pol = _policy(VALU={"policy": "EODHD_ONLY", "primary_provider": "eodhd"})
    assert resolve_route("VALU", pol, eodhd_ok=True)["provider"] == "eodhd"
    fail = resolve_route("VALU", pol, eodhd_ok=False, yahoo_ok=True)
    assert fail["provider"] is None and fail["policy"] == "DATA_UNAVAILABLE"


def test_unsupported_symbol_never_routes_to_eodhd():
    pol = _policy(MISR={"policy": "YAHOO_ONLY", "primary_provider": "yahoo"})
    r = resolve_route("MISR", pol, eodhd_ok=True, yahoo_ok=True)
    assert r["provider"] == "yahoo" and r["series"] == "PROJECT_CURRENT"


def test_manual_review_and_excluded_have_no_provider():
    pol = _policy(MEGM={"policy": "MANUAL_REVIEW"},
                  EGX30ETF={"policy": "EXCLUDED_NON_EQUITY"})
    assert resolve_route("MEGM", pol)["provider"] is None
    assert resolve_route("EGX30ETF", pol)["provider"] is None


def test_both_providers_down_never_empty():
    pol = _policy(SWDY={"policy": "EODHD_PRIMARY", "primary_provider": "eodhd",
                        "fallback_provider": "yahoo"})
    r = resolve_route("SWDY", pol, eodhd_ok=False, yahoo_ok=False)
    assert r["provider"] is None and r["policy"] == "DATA_UNAVAILABLE"  # not empty history


def test_unknown_symbol_defaults_to_current_yahoo():
    r = resolve_route("ZZZZ", {}, eodhd_ok=True)
    assert r["provider"] == "yahoo" and r["policy"] == "YAHOO_ONLY"


def test_generated_policy_is_inactive_and_unapproved():
    from pathlib import Path
    p = Path("data/eodhd/historical_symbol_routing.json")
    if not p.is_file():
        return
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data.get("active") is False
    assert all(e.get("approved") is False for e in data.get("symbols", []))
