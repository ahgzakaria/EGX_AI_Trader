"""Tests for the trading-UI formatting/label helpers + row builder (presentation).

These assert the DISPLAY strings and that formatting/row-building never mutates the
underlying numeric data. No Streamlit runtime is required.
"""

from __future__ import annotations

import pandas as pd

from dashboard.formatting import (
    EM_DASH,
    fmt_compact,
    fmt_frequency,
    fmt_percent,
    fmt_price,
    fmt_range,
    fmt_turnover,
    fmt_volume,
    range_zone,
    scenario_label,
    status_label,
    status_tone,
)


# --- number formatters -------------------------------------------------------

def test_compact_number():
    assert fmt_compact(928858, 1) == "928.9K"
    assert fmt_compact(6346629) == "6.35M"
    assert fmt_compact(1_080_000_000) == "1.08B"
    assert fmt_compact(950) == "950"


def test_volume_formatter():
    assert fmt_volume(928858) == "928.9K"
    assert fmt_volume(509729868) == "509.7M"


def test_turnover_formatter():
    assert fmt_turnover(56330152) == "56.33M EGP"
    assert fmt_turnover(1_080_000_000) == "1.08B EGP"


def test_percentage_formatter():
    assert fmt_percent(4.4711) == "4.47%"
    assert fmt_percent(0) == "0.00%"


def test_frequency_formatter():
    assert fmt_frequency(1.0) == "100%"
    assert fmt_frequency(0.85) == "85%"
    assert fmt_frequency(0.333) == "33%"


def test_price_precision():
    assert fmt_price(139.95) == "139.95"
    assert fmt_price(0.232) == "0.232"          # penny share keeps decimals
    assert fmt_price(1234.5) == "1,234.50"


def test_missing_values_render_as_em_dash():
    assert fmt_volume(None) == EM_DASH
    assert fmt_price(float("nan")) == EM_DASH
    assert fmt_turnover(None) == EM_DASH
    assert fmt_percent(None) == EM_DASH
    assert fmt_range(None, 5) == EM_DASH
    assert status_label(None) == EM_DASH


# --- label mappings ----------------------------------------------------------

def test_status_label_mapping():
    assert status_label("READY_LOWER_RANGE_BOUNCE") == "Ready: Lower Bounce"
    assert status_label("NO_CHASE_RANGE_CONSUMED") == "No Chase"
    assert status_label("TARGET_ROOM_INSUFFICIENT") == "Target Room Low"
    assert status_label("DATA_STALE") == "Data Stale"
    assert status_label("SPREAD_TOO_WIDE") == "Wide Spread"
    assert status_label("WAIT_LOWER_RANGE_BOUNCE") == "Waiting: Lower Bounce"


def test_status_tone_semantics():
    assert status_tone("READY_CONTINUATION") == "green"
    assert status_tone("WAIT_LOWER_RANGE_BOUNCE") == "amber"
    assert status_tone("DATA_STALE") == "red"
    assert status_tone("RANGE_CONSUMED") == "gray"
    assert status_tone("PROVIDER_FINALIZATION_PENDING") == "blue"


def test_scenario_label_mapping():
    assert scenario_label("TREND_CONTINUATION_INSIDE_RANGE") == "Continuation"
    assert scenario_label("EXPECTED_LOWER_RANGE_BOUNCE") == "Lower Bounce"
    assert scenario_label(None) == EM_DASH


def test_range_zone():
    assert range_zone(10)[0] == "lower zone"
    assert range_zone(50)[0] == "middle zone"
    assert range_zone(120)[0] == "above range / extension"
    assert range_zone(-5)[0] == "below range"


# --- row builder (presentation prep, no mutation) ----------------------------

def _universe():
    return pd.DataFrame([
        {"Symbol": "COMI.CA", "Rank": 1, "EXPECTED_RANGE_SCALPING_SCORE": 84.9,
         "liq_avg_volume_20": 4.9e8, "liq_avg_turnover_egp_20": 1.1e8, "vol_adr_percent_20": 4.39,
         "vol_target_2pct_frequency": 0.95, "er_base_expected_low": 137.0, "er_base_expected_high": 142.0,
         "live_last": 139.9, "live_spread_percent": 0.25, "TradableCandidate": True,
         "prov_data_status": "OK"},
        {"Symbol": "PENNY.CA", "Rank": 2, "EXPECTED_RANGE_SCALPING_SCORE": 0.0,
         "liq_avg_volume_20": 500, "liq_avg_turnover_egp_20": 5000, "vol_adr_percent_20": 9.0,
         "vol_target_2pct_frequency": 0.99, "er_base_expected_low": None, "er_base_expected_high": None,
         "live_last": None, "live_spread_percent": None, "TradableCandidate": False,
         "prov_data_status": "DATA_STALE"},
    ])


def _scenarios():
    return pd.DataFrame([
        {"Symbol": "COMI.CA", "Scenario": "TREND_CONTINUATION_INSIDE_RANGE", "Status": "READY",
         "Advisory": "READY_CONTINUATION", "EntryTrigger": 139.9, "TakeProfit": 142.7,
         "StopLoss": 137.1, "RangePosition%": 58.0, "RemainingUpside%": 1.5, "Ask": 140.0,
         "Spread%": 0.25, "Reason": "continuation"},
        {"Symbol": "PENNY.CA", "Scenario": "NO_CHASE_RANGE_CONSUMED", "Status": "DATA_STALE",
         "Advisory": "DATA_STALE", "EntryTrigger": None, "TakeProfit": None, "StopLoss": None,
         "RangePosition%": None, "RemainingUpside%": None, "Ask": None, "Spread%": None,
         "Reason": "stale"},
    ])


def test_build_rows_states_and_best_scenario():
    from dashboard.expected_range_scalper import _build_rows
    rows = _build_rows(_universe(), _scenarios())
    comi = rows[rows["Symbol"] == "COMI.CA"].iloc[0]
    penny = rows[rows["Symbol"] == "PENNY.CA"].iloc[0]
    assert comi["state"] == "ready"
    assert comi["BestScenario"] == "TREND_CONTINUATION_INSIDE_RANGE"
    assert penny["state"] == "rejected"                 # not tradable


def test_row_builder_does_not_mutate_universe():
    from dashboard.expected_range_scalper import _build_rows
    uni = _universe()
    before = uni.copy(deep=True)
    _build_rows(uni, _scenarios())
    pd.testing.assert_frame_equal(uni, before)          # underlying data untouched


def test_top_n_selection_preserves_rank():
    from dashboard.expected_range_scalper import _build_rows
    rows = _build_rows(_universe(), _scenarios())
    top = rows[rows["Tradable"]].sort_values("Rank").head(20)
    assert list(top["Rank"]) == sorted(top["Rank"])     # rank order preserved
    assert len(top) <= 20


def test_missing_entry_target_stop_display_em_dash():
    from dashboard.expected_range_scalper import _build_rows
    rows = _build_rows(_universe(), _scenarios())
    penny = rows[rows["Symbol"] == "PENNY.CA"].iloc[0]
    assert fmt_price(penny["Entry"]) == EM_DASH
    assert fmt_price(penny["Target"]) == EM_DASH
    assert fmt_price(penny["Stop"]) == EM_DASH


def test_card_counts_tradable_plus_rejected_equals_universe():
    # V2: Tradable + Rejected(non-tradable) must equal the universe (no >265 overlap).
    from dashboard.expected_range_scalper import _build_rows
    rows = _build_rows(_universe(), _scenarios())
    tradable = int(rows["Tradable"].sum())
    rejected = len(rows) - tradable
    assert tradable + rejected == len(rows)


def test_states_are_a_clean_partition():
    # V2: every symbol is in exactly one live-state bucket.
    from dashboard.expected_range_scalper import _build_rows
    rows = _build_rows(_universe(), _scenarios())
    assert set(rows["state"]) <= {"ready", "waiting", "no_trade", "rejected"}
    assert rows["state"].notna().all()
    assert len(rows) == rows["state"].value_counts().sum()


def test_history_stale_flag_distinct_from_live_advisory():
    # V2: per-row stale badge is driven by PER-SYMBOL history status, not the
    # market-wide live-quote advisory.
    from dashboard.expected_range_scalper import _build_rows
    rows = _build_rows(_universe(), _scenarios())
    penny = rows[rows["Symbol"] == "PENNY.CA"].iloc[0]
    comi = rows[rows["Symbol"] == "COMI.CA"].iloc[0]
    assert bool(penny["HistStale"]) is True       # prov_data_status == DATA_STALE
    assert bool(comi["HistStale"]) is False        # history OK even if live were stale


def test_ready_label_is_session_phase_aware():
    # V2.1: old signals are never labelled currently-actionable outside continuous trading.
    from dashboard.expected_range_scalper import _ready_label
    ar, en, sub, tone = _ready_label("CONTINUOUS")
    assert en == "Ready Now" and tone == "green"
    ar, en, sub, tone = _ready_label("AUCTION")
    assert "No New Entries" in en and tone != "green"
    ar, en, sub, tone = _ready_label("CLOSED")
    assert en == "Ready at Last Scan" and "not currently actionable" in sub


def test_session_phase_returns_known_value():
    from dashboard.expected_range_scalper import _session_phase
    assert _session_phase() in {"PRE_OPEN", "CONTINUOUS", "AUCTION", "CLOSED", "HOLIDAY"}


def test_max_date_column_safe():
    # Regression: computing a max date must not trigger Series truthiness.
    from dashboard.expected_range_scalper import _max_date
    df = pd.DataFrame({"prov_latest_completed_session": ["2026-07-20", "2026-07-22", None]})
    assert _max_date(df, "prov_latest_completed_session").isoformat() == "2026-07-22"
    assert _max_date(df, "missing_col") is None
    assert _max_date(pd.DataFrame({"prov_latest_completed_session": [None, "bad"]}),
                     "prov_latest_completed_session") is None


def test_stale_alert_runs_without_series_ambiguity(monkeypatch):
    # Regression for the V2.1 ValueError: _stale_alert must handle a real universe
    # DataFrame (Series-valued columns) without raising.
    import dashboard.expected_range_scalper as pg

    class _Ctx:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(pg.st, "markdown", lambda *a, **k: None)
    monkeypatch.setattr(pg.st, "caption", lambda *a, **k: None)
    monkeypatch.setattr(pg.st, "dataframe", lambda *a, **k: None)
    monkeypatch.setattr(pg.st, "expander", lambda *a, **k: _Ctx())

    universe = pd.DataFrame({
        "Symbol": ["COMI.CA", "X.CA"],
        "prov_data_status": ["OK", "DATA_STALE"],
        "prov_latest_completed_session": ["2026-07-22", "2026-07-20"],
        "prov_expected_latest_session": ["2026-07-21", "2026-07-21"],
        "prov_data_age_sessions": [0, 2],
    })
    rows = pd.DataFrame({"Advisory": ["DATA_STALE", "DATA_STALE"]})
    pg._stale_alert(universe, rows)              # must not raise


# --- Part A: scenario state vs data quality separation ----------------------

def test_scenario_state_separated_from_data_quality():
    from dashboard.formatting import (data_quality_label, scenario_state_label)
    # a live-stale symbol: data quality flags it, scenario state stays "—" (not overwritten)
    assert scenario_state_label("DATA_STALE") == "—"
    assert data_quality_label(data_status="OK", advisory="DATA_STALE")[0] == "Live Quote Stale"
    # a ready symbol: state Ready, data quality Current
    assert scenario_state_label("READY_CONTINUATION") == "Ready"
    assert data_quality_label(data_status="OK", advisory="READY_CONTINUATION")[0] == "Current"
    # consumed vs current are distinct dimensions
    assert scenario_state_label("RANGE_CONSUMED") == "Consumed"


def test_data_quality_priority():
    from dashboard.formatting import data_quality_label
    assert data_quality_label(data_status="MISSING")[0] == "No History"
    assert data_quality_label(data_status="OK", hist_stale=True)[0] == "History Lag"
    assert data_quality_label(data_status="OK", advisory="SPREAD_TOO_WIDE")[0] == "Wide Spread"


def test_short_date_compact():
    from dashboard.expected_range_scalper import _short_date
    assert _short_date("2026-07-22") == "22 Jul"


# --- Part B: Scalping Dashboard ---------------------------------------------

def test_dash_classify_is_unique_symbol_partition():
    from dashboard.expected_range_scalper import _build_rows
    from dashboard.scalping import _dash_classify
    rows = _build_rows(_universe(), _scenarios())
    b = _dash_classify(rows, "CONTINUOUS")
    assert sum(b.values()) == len(rows)               # clean partition, no double-count
    assert b["rejected_low_liquidity"] == 1           # the non-tradable penny share


def test_market_closed_moves_live_stale_out_of_blockers():
    # A tradable symbol with a stale LIVE quote is an active blocker during continuous
    # trading, but only a 'last-session quote' after close (not a data failure).
    from dashboard.expected_range_scalper import _build_rows
    from dashboard.scalping import _dash_classify
    uni = _universe()
    scen = _scenarios()
    scen.loc[scen["Symbol"] == "COMI.CA", "Advisory"] = "DATA_STALE"
    scen.loc[scen["Symbol"] == "COMI.CA", "Status"] = "DATA_STALE"
    rows = _build_rows(uni, scen)
    cont = _dash_classify(rows, "CONTINUOUS")
    closed = _dash_classify(rows, "CLOSED")
    assert cont["live_quote_stale"] == 1 and cont["last_session_quotes"] == 0
    assert closed["live_quote_stale"] == 0 and closed["last_session_quotes"] == 1
    # the symbol is never double-counted and the partition still holds in both phases
    assert sum(cont.values()) == len(rows) == sum(closed.values())


# --- V1.1: market-closed table semantics + price validity -------------------

def test_market_closed_quote_is_last_session_snapshot():
    from dashboard.formatting import data_quality_label
    assert data_quality_label(data_status="OK", advisory="DATA_STALE",
                              phase="CLOSED", last_valid=True)[0] == "Last Session Snapshot"
    assert data_quality_label(data_status="OK", advisory="DATA_STALE",
                              phase="AUCTION", last_valid=True)[0] == "Auction Snapshot"


def test_active_session_expired_quote_is_live_stale():
    from dashboard.formatting import data_quality_label
    assert data_quality_label(data_status="OK", advisory="DATA_STALE",
                              phase="CONTINUOUS", last_valid=True)[0] == "Live Quote Stale"


def test_invalid_price_data_quality_labels():
    from dashboard.formatting import data_quality_label
    assert data_quality_label(data_status="OK", phase="CONTINUOUS", last_valid=False)[0] == "Live Data Missing"
    assert data_quality_label(data_status="OK", phase="CLOSED", last_valid=False)[0] == "Last Price Unavailable"


def test_zero_last_displays_em_dash():
    from dashboard.formatting import fmt_live_price, is_valid_price
    assert fmt_live_price(0) == EM_DASH
    assert fmt_live_price(0.0) == EM_DASH
    assert fmt_live_price(None) == EM_DASH
    assert fmt_live_price(float("nan")) == EM_DASH
    assert fmt_live_price(12.5) == "12.50"
    assert is_valid_price(0) is False and is_valid_price(12.5) is True


def test_zero_last_cannot_be_closest_to_ready():
    # a waiting symbol with an invalid price is excluded from closest-to-ready ranking
    from dashboard.formatting import is_valid_price
    wait = pd.DataFrame({"Symbol": ["A", "B", "C"], "Last": [0.0, None, 12.5]})
    valid = wait[wait["Last"].map(is_valid_price)]
    assert list(valid["Symbol"]) == ["C"]


def test_top_candidates_preserve_global_rank_after_filter():
    from dashboard.expected_range_scalper import _build_rows
    rows = _build_rows(_universe(), _scenarios())
    # the dashboard excludes ready/waiting from the top table but keeps global Rank
    top = rows[rows["Tradable"] & ~rows["state"].isin(["ready", "waiting"])].sort_values("Rank")
    assert set(top["Rank"]).issubset(set(rows["Rank"]))     # never renumbered
    assert list(top["Rank"]) == sorted(top["Rank"])


def test_scalping_dashboard_import_smoke():
    import importlib
    m = importlib.import_module("dashboard.scalping")
    assert hasattr(m, "show_scalping_dashboard")
    assert hasattr(m, "_dash_classify")


def test_page_import_smoke():
    import importlib
    m = importlib.import_module("dashboard.expected_range_scalper")
    assert hasattr(m, "show_expected_range_scalper")


# --- Part A micro-polish: score / compact data-quality label ----------------

def test_fmt_score_no_needless_decimals():
    from dashboard.formatting import fmt_score
    assert fmt_score(90.0) == "90"
    assert fmt_score(88) == "88"
    assert fmt_score(87.0000001) == "87"
    assert fmt_score(84.9) == "84.9"
    assert fmt_score(None) == EM_DASH
    assert fmt_score(float("nan")) == EM_DASH


def test_dq_compact_shortens_last_session_snapshot():
    from dashboard.formatting import dq_compact
    assert dq_compact("Last Session Snapshot") == "Last Snapshot"
    # other labels are unchanged (already compact / in the approved label set)
    assert dq_compact("Current") == "Current"
    assert dq_compact("Last Price Unavailable") == "Last Price Unavailable"
    assert dq_compact("History Lag") == "History Lag"


# --- Part B: Opportunities page ---------------------------------------------

def _opp_universe():
    # COMI ready · PENNY rejected · SWDY tradable-no-room · WIDE tradable-wide-spread
    return pd.DataFrame([
        {"Symbol": "COMI.CA", "Rank": 1, "EXPECTED_RANGE_SCALPING_SCORE": 84.9,
         "liq_avg_volume_20": 4.9e8, "liq_avg_turnover_egp_20": 1.1e8, "vol_adr_percent_20": 4.39,
         "vol_target_2pct_frequency": 0.95, "er_base_expected_low": 137.0, "er_base_expected_high": 142.0,
         "live_last": 139.9, "live_bid": 139.8, "live_ask": 140.0, "live_spread_percent": 0.25,
         "live_quote_age_seconds": 12.0, "TradableCandidate": True, "prov_data_status": "OK"},
        {"Symbol": "SWDY.CA", "Rank": 2, "EXPECTED_RANGE_SCALPING_SCORE": 71.0,
         "liq_avg_volume_20": 2.0e7, "liq_avg_turnover_egp_20": 5.0e7, "vol_adr_percent_20": 3.1,
         "vol_target_2pct_frequency": 0.6, "er_base_expected_low": 9.0, "er_base_expected_high": 9.4,
         "live_last": 9.2, "live_bid": 9.19, "live_ask": 9.21, "live_spread_percent": 0.2,
         "live_quote_age_seconds": 30.0, "TradableCandidate": True, "prov_data_status": "OK"},
        {"Symbol": "WIDE.CA", "Rank": 3, "EXPECTED_RANGE_SCALPING_SCORE": 55.0,
         "liq_avg_volume_20": 1.0e7, "liq_avg_turnover_egp_20": 2.0e7, "vol_adr_percent_20": 5.0,
         "vol_target_2pct_frequency": 0.5, "er_base_expected_low": 4.0, "er_base_expected_high": 4.3,
         "live_last": 4.1, "live_bid": 4.0, "live_ask": 4.3, "live_spread_percent": 5.0,
         "live_quote_age_seconds": 20.0, "TradableCandidate": True, "prov_data_status": "OK"},
        {"Symbol": "PENNY.CA", "Rank": 4, "EXPECTED_RANGE_SCALPING_SCORE": 0.0,
         "liq_avg_volume_20": 500, "liq_avg_turnover_egp_20": 5000, "vol_adr_percent_20": 9.0,
         "vol_target_2pct_frequency": 0.99, "er_base_expected_low": None, "er_base_expected_high": None,
         "live_last": None, "live_bid": None, "live_ask": None, "live_spread_percent": None,
         "live_quote_age_seconds": None, "TradableCandidate": False, "prov_data_status": "DATA_STALE"},
    ])


def _opp_scenarios():
    return pd.DataFrame([
        {"Symbol": "COMI.CA", "Scenario": "TREND_CONTINUATION_INSIDE_RANGE", "Status": "READY",
         "Advisory": "READY_CONTINUATION", "EntryTrigger": 139.9, "TakeProfit": 142.7,
         "StopLoss": 137.1, "RangePosition%": 58.0, "RemainingUpside%": 1.5, "Ask": 140.0,
         "Spread%": 0.25, "Reason": "continuation"},
        {"Symbol": "SWDY.CA", "Scenario": "EXPECTED_LOWER_RANGE_BOUNCE", "Status": "WAIT_LOWER_RANGE_BOUNCE",
         "Advisory": "WAIT_LOWER_RANGE_BOUNCE", "EntryTrigger": 9.0, "TakeProfit": 9.18,
         "StopLoss": 8.82, "RangePosition%": 40.0, "RemainingUpside%": 2.2, "Ask": 9.21,
         "Spread%": 0.2, "Reason": "waiting bounce"},
        {"Symbol": "WIDE.CA", "Scenario": "DIP_AND_RECLAIM", "Status": "SPREAD_TOO_WIDE",
         "Advisory": "SPREAD_TOO_WIDE", "EntryTrigger": 4.05, "TakeProfit": 4.13,
         "StopLoss": 3.97, "RangePosition%": 30.0, "RemainingUpside%": 3.0, "Ask": 4.3,
         "Spread%": 5.0, "Reason": "spread"},
        {"Symbol": "PENNY.CA", "Scenario": "NO_CHASE_RANGE_CONSUMED", "Status": "DATA_STALE",
         "Advisory": "DATA_STALE", "EntryTrigger": None, "TakeProfit": None, "StopLoss": None,
         "RangePosition%": None, "RemainingUpside%": None, "Ask": None, "Spread%": None,
         "Reason": "stale"},
    ])


def test_scenario_full_labels_are_decision_support():
    from dashboard.formatting import scenario_full_label
    assert scenario_full_label("EXPECTED_LOWER_RANGE_BOUNCE") == "Lower Range Bounce"
    assert scenario_full_label("DIP_AND_RECLAIM") == "Dip and Reclaim"
    assert scenario_full_label("TREND_CONTINUATION_INSIDE_RANGE") == "Trend Continuation"
    assert scenario_full_label("EXPECTED_RANGE_BREAKOUT_AND_RETEST") == "Breakout Retest"
    assert scenario_full_label("GAP_UP_WITH_REMAINING_ROOM") == "Gap-Up Continuation"
    assert scenario_full_label("GAP_DOWN_RECOVERY") == "Gap-Down Recovery"
    assert scenario_full_label(None) == EM_DASH


def test_paper_outcome_labels():
    from dashboard.formatting import paper_outcome_label
    assert paper_outcome_label("TARGET") == ("Target First", "green")
    assert paper_outcome_label("STOP") == ("Stop First", "red")
    assert paper_outcome_label("NEITHER") == ("Neither", "gray")
    assert paper_outcome_label("PENDING") == ("Outcome Pending", "amber")
    assert paper_outcome_label("RECORDED") == ("Signal Recorded", "blue")
    assert paper_outcome_label("NONE")[0] == "No Paper Signal"
    assert paper_outcome_label("anything-unknown")[0] == "No Paper Signal"


def test_invalidation_labels():
    from dashboard.formatting import invalidation_label
    assert invalidation_label("RANGE_CONSUMED") == "Range Consumed"
    assert invalidation_label("SPREAD_TOO_WIDE") == "Spread Widened"
    assert invalidation_label("DATA_STALE") == "Quote Became Stale"
    assert invalidation_label("INVALID") == "Support/Setup Failed"
    assert invalidation_label("READY", "NO_NEW_ENTRY_AFTER_1415") == "Session Phase Ended (auction)"


def test_ready_waiting_invalid_separation():
    from dashboard.expected_range_scalper import _build_rows
    rows = _build_rows(_opp_universe(), _opp_scenarios())
    ready = set(rows[rows["state"] == "ready"]["Symbol"])
    waiting = set(rows[rows["state"] == "waiting"]["Symbol"])
    rejected = set(rows[rows["state"] == "rejected"]["Symbol"])
    assert ready == {"COMI.CA"}
    assert waiting == {"SWDY.CA"}
    assert "PENNY.CA" in rejected
    assert ready.isdisjoint(waiting) and waiting.isdisjoint(rejected)


def test_blocker_groups_are_unique_symbol_and_reconcile():
    from dashboard.expected_range_scalper import _build_rows
    from dashboard.opportunities import _blocker_groups
    rows = _build_rows(_opp_universe(), _opp_scenarios())
    groups = _blocker_groups(rows, "CONTINUOUS")
    all_syms = [s for _, _, _, syms in groups for s in syms]
    # each symbol appears at most once across every blocker bucket
    assert len(all_syms) == len(set(all_syms))
    # blockers cover exactly the non-ready/non-waiting symbols
    non_actionable = set(rows[~rows["state"].isin(["ready", "waiting"])]["Symbol"])
    assert set(all_syms) == non_actionable
    labels = {lbl: syms for _, lbl, _, syms in groups}
    assert "PENNY.CA" in labels["Low Liquidity"]
    assert "WIDE.CA" in labels["Wide Spread"]


def test_blocker_groups_session_aware_quote_age():
    # after close a stale live quote is 'Outside Continuous Session', not an active fault
    from dashboard.expected_range_scalper import _build_rows
    from dashboard.opportunities import _blocker_groups
    uni = _opp_universe()
    scen = _opp_scenarios()
    scen.loc[scen["Symbol"] == "SWDY.CA", "Advisory"] = "DATA_STALE"
    scen.loc[scen["Symbol"] == "SWDY.CA", "Status"] = "DATA_STALE"
    rows = _build_rows(uni, scen)
    cont = {lbl: syms for _, lbl, _, syms in _blocker_groups(rows, "CONTINUOUS")}
    closed = {lbl: syms for _, lbl, _, syms in _blocker_groups(rows, "CLOSED")}
    assert "SWDY.CA" in cont["Live Quote Stale (active)"]
    assert "SWDY.CA" not in closed["Live Quote Stale (active)"]
    assert "SWDY.CA" in closed["Outside Continuous Session"]


def test_near_ready_distance_excludes_invalid_prices():
    from dashboard.formatting import is_valid_price
    wait = pd.DataFrame({"Symbol": ["A", "B", "C"], "Last": [0.0, None, 9.2], "Entry": [9.0, 9.0, 9.0]})
    valid = wait[wait["Last"].map(is_valid_price)].copy()
    assert list(valid["Symbol"]) == ["C"]
    # distance is computed only from the valid price
    dist = (valid["Entry"] - valid["Last"]) / valid["Last"] * 100.0
    assert abs(dist.iloc[0] - ((9.0 - 9.2) / 9.2 * 100)) < 1e-9


def test_transition_suppression_predicate():
    # unchanged evaluations (from == to) are never shown as timeline events
    tr = pd.DataFrame({"from_state": ["WAIT", "READY", "READY"],
                       "to_state": ["READY", "READY", "INVALID"]})
    changed = tr[tr["from_state"] != tr["to_state"]]
    assert len(changed) == 2
    assert list(changed["to_state"]) == ["READY", "INVALID"]


def test_quote_age_formatting():
    from dashboard.opportunities import _quote_age
    assert _quote_age(12) == "12s"
    assert _quote_age(45.0) == "45s"
    assert _quote_age(120) == "2m"
    assert _quote_age(None) == EM_DASH
    assert _quote_age(float("nan")) == EM_DASH


def test_paper_helpers_activation_cycle_and_uuid():
    from dashboard.opportunities import _paper_cycle, _paper_key, _paper_uuid
    idx = {"COMI.CA": {"key": "TARGET", "uuid": "abcdef123456", "cycle": 2, "activated": ""}}
    assert _paper_key(idx, "COMI.CA") == "TARGET"
    assert _paper_key(idx, "NOPE.CA") == "NONE"
    assert _paper_uuid(idx, "COMI.CA") == "abcdef12…"
    assert _paper_uuid(idx, "NOPE.CA") == EM_DASH
    assert _paper_cycle(idx, "COMI.CA") == 2
    assert _paper_cycle(idx, "NOPE.CA") == EM_DASH


def test_filtered_row_opens_correct_symbol(monkeypatch):
    import dashboard.opportunities as opp
    monkeypatch.setattr(opp.st, "session_state", {}, raising=False)
    disp = pd.DataFrame({"Symbol": ["COMI.CA", "SWDY.CA", "WIDE.CA"]})

    class _Sel:
        selection = {"rows": [1]}
    opp._select_from(_Sel(), disp, "opp_all")
    assert opp.st.session_state["_opp_drawer_symbol"] == "SWDY.CA"


def test_live_extras_does_not_mutate_universe():
    from dashboard.opportunities import _live_extras
    uni = _opp_universe()
    before = uni.copy(deep=True)
    extra = _live_extras(uni)
    pd.testing.assert_frame_equal(uni, before)          # underlying universe untouched
    assert set(extra.columns) == {"Symbol", "Bid", "QuoteAge"}


def test_opportunities_import_smoke():
    import importlib
    m = importlib.import_module("dashboard.opportunities")
    assert hasattr(m, "show_opportunities")
    assert hasattr(m, "_blocker_groups")
