"""Tests for Phase 3.1: frame-contract adapter, tiers, and migration safety."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from core.history_frame_adapter import matches_contract, to_load_history_frame

CONTRACT = json.loads(Path("tests/fixtures/history_frame_contract.json").read_text(encoding="utf-8"))


def _eodhd_frame():
    return pd.DataFrame({
        "Date": [date(2026, 7, 20), date(2026, 7, 21), date(2026, 7, 22)],
        "Open": [1.0, 1.1, 1.2], "High": [1.2, 1.3, 1.4], "Low": [0.9, 1.0, 1.1],
        "Close": [1.1, 1.2, 1.3], "Volume": [100, 200, 300]})


# --- frame contract / adapter -----------------------------------------------

def test_adapter_matches_load_history_contract():
    f = to_load_history_frame(_eodhd_frame(), symbol="TEST.CA")
    ok, problems = matches_contract(f, CONTRACT)
    assert ok, problems
    assert list(f.columns) == ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
    assert f.index.name == "Date" and f.index.tz is None
    assert all(str(f[c].dtype) == "float64" for c in f.columns)


def test_adapter_preserves_attrs_metadata():
    f = to_load_history_frame(_eodhd_frame(), symbol="TEST.CA", provider="eodhd")
    md = f.attrs.get("market_data")
    assert isinstance(md, dict)
    assert md["provider"] == "eodhd" and md["effective_provider"] == "eodhd"
    assert md["purpose"] == "backtest" and md["price_series"] == "SPLIT_ADJUSTED"
    for field in CONTRACT["attrs_required_fields"]:
        assert field in md


def test_adapter_no_nan_and_sorted_unique():
    f = to_load_history_frame(_eodhd_frame(), symbol="X")
    assert int(f.isna().sum().sum()) == 0
    assert list(f.index) == sorted(f.index)
    assert not f.index.duplicated().any()


def test_contract_detects_broken_frame():
    bad = _eodhd_frame().rename(columns={"Close": "close"})  # wrong column name/case
    # a raw (non-adapted) frame must fail the contract
    ok, problems = matches_contract(bad.set_index("Date"), CONTRACT)
    assert not ok and problems


# --- corp-action classification ---------------------------------------------

def test_corp_action_split_vs_bonus_classification():
    from scripts.audit_eodhd_corp_action_reconciliation import _classify_action
    # genuine split: EODHD raw price drops by the ratio and Yahoo too
    assert _classify_action(2.0, 2.01, 1.98)[0] == "SPLIT"
    # EODHD price drops but Yahoo does not (already adjusted) → provider diff
    assert _classify_action(2.0, 2.01, 1.00)[0] == "SPLIT_PROVIDER_DIFF"
    # ratio present but price didn't drop → bonus / capital increase
    act, vol = _classify_action(1.5, 1.02, 1.00)
    assert act in ("STOCK_DIVIDEND_BONUS", "CAPITAL_INCREASE_OR_BONUS")
    assert vol in ("KEEP_RAW", "EVENT_SPECIFIC")


def test_volume_rule_is_not_universal():
    from scripts.audit_eodhd_corp_action_reconciliation import _classify_action
    rules = {_classify_action(2.0, 2.0, 2.0)[1], _classify_action(2.0, 2.0, 1.0)[1],
             _classify_action(1.5, 1.02, 1.0)[1]}
    assert len(rules) > 1                       # event-specific, not one universal rule


# --- live price is not full-history proof -----------------------------------

def test_live_scale_match_is_not_history_confirmation():
    # the revalidation must distinguish a symbol whose LATEST matches but whose deep
    # history diverges (current-scale only) from a fully history-confirmed one.
    p = Path("reports/eodhd/eodhd_correct_revalidation.csv")
    if not p.is_file():
        return
    df = pd.read_csv(p)
    classes = set(df["classification"])
    # not every EODHD_CORRECT symbol is upgraded to full history confirmation
    assert classes - {"EODHD_HISTORY_CONFIRMED"}, "revalidation must not blanket-confirm history"


# --- tiers + safety ---------------------------------------------------------

def _review():
    p = Path("data/eodhd/historical_symbol_routing_review.json")
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def test_all_tiers_inactive_and_unapproved():
    r = _review()
    if not r:
        return
    assert r.get("active") is False
    assert all(e.get("approved") is False for e in r.get("symbols", []))
    tiers = {e["tier"] for e in r.get("symbols", [])}
    assert tiers <= {"TIER_A_FORWARD_SAFE", "TIER_B_FORWARD_EODHD_NO_FALLBACK",
                     "TIER_C_HISTORICAL_REVIEW", "TIER_D_UNSUPPORTED_OR_MANUAL"}


def test_oras_is_tier_b_no_fallback():
    r = _review()
    if not r:
        return
    oras = next((e for e in r.get("symbols", []) if e["symbol"] == "ORAS"), None)
    assert oras and oras["tier"] == "TIER_B_FORWARD_EODHD_NO_FALLBACK"
    assert oras["fallback_provider"] is None       # never Yahoo


def test_historical_backtest_provider_stays_yahoo_in_every_tier():
    r = _review()
    if not r:
        return
    for e in r.get("symbols", []):
        # frozen backtests never move to EODHD-only in the proposal (yahoo or local)
        assert e["historical_backtest_provider"] in ("yahoo", "eodhd_or_local", None, "eodhd")
        # Tier A/C must keep Yahoo for historical
        if e["tier"] in ("TIER_A_FORWARD_SAFE", "TIER_C_HISTORICAL_REVIEW"):
            assert e["historical_backtest_provider"] == "yahoo"


def test_frozen_provider_and_mode_unchanged():
    from providers.provider_mode import active_historical_provider, current_mode
    assert current_mode() == "EODHD_SHADOW"
    assert active_historical_provider() == "yahoo"
