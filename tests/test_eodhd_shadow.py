"""Tests for EODHD provider plumbing: mapping, canonicalization, client safety, modes.

No network: the client cache-key / redaction and the canonicalizer are exercised with
synthetic data. The token is never used.
"""

from __future__ import annotations

import pandas as pd

from providers.eodhd_symbol_map import build_mapping
from providers.eodhd_historical_provider import _canonicalize, CANONICAL_COLUMNS
from providers.eodhd_client import EODHDClient, _redact
from providers import provider_mode


# --- symbol mapping ---------------------------------------------------------

def _eodhd_rows():
    return [{"Code": "COMI", "Name": "Commercial International Bank", "Type": "Common Stock"},
            {"Code": "SWDY", "Name": "Elsewedy Electric", "Type": "Common Stock"},
            {"Code": "EGX30ETF", "Name": "EGX30 ETF", "Type": "ETF"},
            {"Code": "SOMEBOND", "Name": "A Bond", "Type": "BOND"}]


def test_mapping_verified_exact_and_not_found():
    rows, summary = build_mapping(["COMI.CA", "SWDY.CA", "NOPE.CA"], _eodhd_rows())
    by = {r["internal_symbol"]: r for r in rows}
    assert by["COMI"]["mapping_status"] == "VERIFIED_EXACT"
    assert by["COMI"]["eodhd_symbol"] == "COMI.EGX"
    assert by["SWDY"]["mapping_status"] == "VERIFIED_EXACT"
    assert by["NOPE"]["mapping_status"] == "NOT_FOUND" and by["NOPE"]["eodhd_symbol"] == ""
    assert summary["verified_exact"] == 2 and summary["not_found"] == 1
    assert "NOPE" in summary["missing_internal"]


def test_mapping_flags_non_equity_and_extras():
    rows, summary = build_mapping(["EGX30ETF.CA", "SOMEBOND.CA"], _eodhd_rows())
    by = {r["internal_symbol"]: r for r in rows}
    assert by["EGX30ETF"]["mapping_status"] == "VERIFIED_EXACT"     # ETF is an accepted type
    assert by["SOMEBOND"]["mapping_status"] == "NON_EQUITY"          # BOND is not
    # COMI/SWDY are extra EODHD symbols not in this small internal set
    assert "COMI" in summary["extra_eodhd_symbols"]


def test_mapping_is_verified_not_suffix_replacement():
    # a symbol absent from the EODHD list is NEVER mapped by suffix replacement alone
    rows, _ = build_mapping(["GHOST.CA"], _eodhd_rows())
    assert rows[0]["eodhd_symbol"] == "" and rows[0]["mapping_status"] == "NOT_FOUND"


# --- canonicalization -------------------------------------------------------

def test_canonicalize_valid_frame():
    raw = [
        {"date": "2026-07-21", "open": 136.6, "high": 140.4, "low": 136.6, "close": 139.97,
         "adjusted_close": 139.97, "volume": 7840206},
        {"date": "2026-07-22", "open": 139.97, "high": 140.3, "low": 138.7, "close": 140.0,
         "adjusted_close": 140.0, "volume": 3972917},
    ]
    frame, q = _canonicalize(raw)
    assert list(frame.columns) == CANONICAL_COLUMNS
    assert len(frame) == 2 and list(frame["Date"]) == sorted(frame["Date"])   # ascending
    assert q["dropped_non_numeric"] == 0


def test_canonicalize_drops_zero_price_never_substitutes():
    raw = [{"date": "2026-07-20", "open": 0, "high": 0, "low": 0, "close": 0, "volume": 100},
           {"date": "2026-07-21", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 5}]
    frame, q = _canonicalize(raw)
    assert len(frame) == 1 and q["dropped_zero_or_negative"] == 1     # zero row dropped, not filled
    assert (frame["Close"] > 0).all()


def test_canonicalize_dedupes_and_sorts_and_flags_adjustment():
    raw = [{"date": "2026-07-22", "open": 1, "high": 2, "low": 1, "close": 2, "adjusted_close": 1.5, "volume": 1},
           {"date": "2026-07-20", "open": 1, "high": 2, "low": 1, "close": 2, "adjusted_close": 2, "volume": 1},
           {"date": "2026-07-22", "open": 1, "high": 2, "low": 1, "close": 2, "adjusted_close": 1.5, "volume": 9}]
    frame, q = _canonicalize(raw)
    assert q["duplicate_dates"] == 1 and len(frame) == 2
    assert list(frame["Date"]) == sorted(frame["Date"])
    assert q["adjusted_close_distinct"] is True     # adj != close on at least one row
    assert frame.iloc[-1]["Volume"] == 9            # duplicate keeps last


def test_canonicalize_empty_is_safe():
    frame, q = _canonicalize([])
    assert frame.empty and list(frame.columns) == CANONICAL_COLUMNS


# --- client safety ----------------------------------------------------------

def test_cache_key_excludes_token(tmp_path):
    c = EODHDClient(cache_dir=tmp_path)
    p1 = c._cache_path("eod/COMI.EGX", {"period": "d", "api_token": "SECRET123"})
    p2 = c._cache_path("eod/COMI.EGX", {"period": "d"})
    assert p1 == p2                                  # token never part of the cache key
    assert "SECRET" not in str(p1)


def test_redact_strips_token_and_api_token_param():
    token = "SECRETTOKEN"
    msg = f"HTTP error at https://eodhd.com/api/user?api_token={token}&fmt=json ({token})"
    red = _redact(msg, token)
    assert token not in red and "api_token=***REDACTED***" in red


# --- provider mode (shadow keeps Yahoo active) ------------------------------

def test_shadow_mode_keeps_yahoo_active(monkeypatch):
    monkeypatch.setattr(provider_mode, "current_mode", lambda: provider_mode.EODHD_SHADOW)
    assert provider_mode.active_historical_provider() == "yahoo"
    assert provider_mode.is_shadow() is True


def test_yahoo_only_mode_active_yahoo(monkeypatch):
    monkeypatch.setattr(provider_mode, "current_mode", lambda: provider_mode.YAHOO_ONLY)
    assert provider_mode.active_historical_provider() == "yahoo"


def test_configured_mode_is_eodhd_and_yahoo_is_not_operational():
    """The shadow period is over: EODHD is the live historical provider.

    The guard that matters is the second assertion. Shadow mode was a staging
    state; "Yahoo never feeds a decision" is the permanent rule, so that is
    what this test pins.
    """

    assert provider_mode.current_mode() == provider_mode.EODHD_ONLY
    assert provider_mode.active_historical_provider() == "eodhd"
    assert provider_mode.active_historical_provider() != "yahoo"
    assert not provider_mode.is_shadow()
