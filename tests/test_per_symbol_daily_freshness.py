"""Per-symbol daily-data freshness: eligibility, coverage and market-wide gating.

Context. On 2026-08-04 EODHD had published the 2026-08-03 EGX session for 6 of
241 symbols and 2026-07-30 for the rest. Every row was date-honest - a bounded
no-cache probe confirmed the provider simply had partial coverage. The first
fix blocked the whole scan on mixed sessions, which was correct but too coarse:
it hid the six genuinely current symbols. Freshness is a property of each
symbol, so the gate is now per symbol.

Nothing here runs a scan, contacts a provider, writes a cache, touches Rubix or
changes a threshold.
"""

from __future__ import annotations

import csv
import pathlib

import pytest

from core.daily_data_guard import (
    COVERAGE_SETTING_KEY,
    DEFAULT_MINIMUM_COVERAGE_PERCENT,
    INSUFFICIENT_CURRENT_COVERAGE,
    SymbolFreshness,
    classify_symbol_freshness,
    minimum_coverage_percent,
    summarize_universe_coverage,
)


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
ARCHIVED_RUN = "RUN_20260804_005327"

EXPECTED = "2026-08-03"          # Monday
PRIOR = "2026-07-30"             # Thursday - two trading sessions earlier
UNIVERSE = 241

#: Exactly what the archived run contained.
OBSERVED = {
    "2026-08-03": 6, "2026-07-30": 184, "2026-07-27": 1,
    "2026-07-09": 1, "2026-07-06": 1, "2014-03-03": 1,
}


def classify_many(distribution, expected=EXPECTED):
    results = []
    for date, count in distribution.items():
        for _ in range(count):
            results.append(classify_symbol_freshness(
                f"S{len(results)}.CA", date, expected))
    return results


# =========================================================================== #
# PER-SYMBOL FRESHNESS
# =========================================================================== #


def test_a_symbol_on_the_expected_session_is_current_and_eligible():
    result = classify_symbol_freshness("QNBE.CA", EXPECTED, EXPECTED)
    assert result.freshness_status is SymbolFreshness.CURRENT
    assert result.eligible_for_current_analysis is True
    assert result.outcome_status == "SUCCESS_CURRENT"
    assert result.trading_sessions_behind == 0
    assert result.exclusion_reason == ""


def test_a_symbol_behind_the_expected_session_is_stale_and_excluded():
    result = classify_symbol_freshness("SPIN.CA", PRIOR, EXPECTED)
    assert result.freshness_status is SymbolFreshness.STALE
    assert result.eligible_for_current_analysis is False
    assert result.outcome_status == "SKIPPED_STALE_DAILY_DATA"
    assert "2 trading sessions behind" in result.exclusion_reason


@pytest.mark.parametrize("value", ["", None])
def test_a_missing_candle_date_is_excluded(value):
    result = classify_symbol_freshness("X.CA", value, EXPECTED)
    assert result.freshness_status is SymbolFreshness.MISSING_DATE
    assert result.eligible_for_current_analysis is False
    assert result.outcome_status == "SKIPPED_MISSING_DAILY_DATE"


def test_an_uninterpretable_candle_date_is_excluded():
    result = classify_symbol_freshness("X.CA", "not-a-date", EXPECTED)
    assert result.freshness_status is SymbolFreshness.INVALID_DATE
    assert result.eligible_for_current_analysis is False


def test_a_future_candle_date_is_rejected_as_a_provenance_error():
    result = classify_symbol_freshness("X.CA", "2026-08-05", EXPECTED)
    assert result.freshness_status is SymbolFreshness.FUTURE_DATE
    assert result.eligible_for_current_analysis is False
    assert result.outcome_status == "SKIPPED_FUTURE_DAILY_DATE"
    assert "dated after" in result.exclusion_reason


def test_insufficient_history_is_excluded_independently_of_the_date():
    result = classify_symbol_freshness("X.CA", EXPECTED, EXPECTED,
                                       bars=40, minimum_bars=250)
    assert result.freshness_status is SymbolFreshness.INSUFFICIENT_HISTORY
    assert result.eligible_for_current_analysis is False


def test_an_unavailable_expected_session_makes_nothing_current():
    """A missing calendar is never permission to call data current."""

    result = classify_symbol_freshness("X.CA", EXPECTED, "")
    assert result.eligible_for_current_analysis is False
    assert result.freshness_status is SymbolFreshness.SESSION_MISMATCH


def test_the_expected_session_never_becomes_the_actual_session():
    result = classify_symbol_freshness("SPIN.CA", PRIOR, EXPECTED)
    assert result.actual_latest_session == PRIOR
    assert result.expected_session == EXPECTED
    assert result.actual_latest_session != result.expected_session


def test_distance_is_counted_in_trading_sessions_not_calendar_days():
    """EGX trades Sunday-Thursday.

    2026-07-30 (Thu) -> 2026-08-03 (Mon) is four calendar days but two trading
    sessions: Sunday 08-02 and Monday 08-03. Calendar distance would overstate
    staleness across every weekend.
    """

    result = classify_symbol_freshness("SPIN.CA", PRIOR, EXPECTED)
    assert result.trading_sessions_behind == 2

    # Thursday -> the immediately following Sunday is one session.
    assert classify_symbol_freshness(
        "X.CA", "2026-07-30", "2026-08-02").trading_sessions_behind == 1


def test_a_friday_or_saturday_gap_adds_no_trading_sessions():
    #  Sunday 2026-08-02 -> Monday 2026-08-03 is exactly one session.
    assert classify_symbol_freshness(
        "X.CA", "2026-08-02", "2026-08-03").trading_sessions_behind == 1


def test_the_result_carries_its_provenance():
    result = classify_symbol_freshness(
        "SPIN.CA", PRIOR, EXPECTED, source_provider="eodhd",
        source_mode="EODHD_CACHE", candle_identity="abc123",
        ohlcv={"Close": 15.64})
    row = result.as_row()
    assert row["Ticker"] == "SPIN.CA"
    assert row["ActualSession"] == PRIOR
    assert row["ExpectedSession"] == EXPECTED
    assert row["SourceProvider"] == "eodhd"
    assert row["SourceMode"] == "EODHD_CACHE"
    assert row["CandleIdentity"] == "abc123"
    assert row["EligibleForCurrentAnalysis"] is False
    assert result.source_latest_ohlcv["Close"] == 15.64


# =========================================================================== #
# MIXED-SESSION SCAN
# =========================================================================== #


def test_only_the_current_symbols_survive_the_gate():
    results = classify_many(OBSERVED)
    eligible = [r for r in results if r.eligible_for_current_analysis]
    assert len(results) == 194
    assert len(eligible) == 6
    assert all(r.actual_latest_session == EXPECTED for r in eligible)


def test_every_symbol_is_represented_in_the_audit_outcome():
    """A stale symbol dropped without a record is indistinguishable from one
    that was never attempted."""

    results = classify_many(OBSERVED)
    assert len(results) == sum(OBSERVED.values())
    assert all(r.outcome_status for r in results)
    excluded = [r for r in results if not r.eligible_for_current_analysis]
    assert len(excluded) == 188
    assert all(r.exclusion_reason for r in excluded)


def test_decisions_cannot_exceed_the_current_symbol_count():
    results = classify_many(OBSERVED)
    eligible = {r.symbol for r in results if r.eligible_for_current_analysis}
    # Any decision table built from eligible symbols is capped by construction.
    assert len(eligible) <= 6


def test_coverage_reports_the_incident_honestly():
    coverage = summarize_universe_coverage(classify_many(OBSERVED), EXPECTED,
                                           universe_total=UNIVERSE)
    assert coverage.universe_total == 241
    assert coverage.history_loaded == 194
    assert coverage.current == 6
    assert coverage.stale == 188
    assert coverage.current_percent == 2.5
    assert coverage.complete is False
    assert coverage.distribution["2026-07-30"] == 184
    assert coverage.distribution["2026-08-03"] == 6


def test_the_dominant_session_remains_the_prior_one():
    coverage = summarize_universe_coverage(classify_many(OBSERVED), EXPECTED,
                                           universe_total=UNIVERSE)
    dominant = max(coverage.distribution, key=lambda d: coverage.distribution[d])
    assert dominant == PRIOR
    assert coverage.expected_session == EXPECTED


# =========================================================================== #
# MARKET-WIDE COVERAGE
# =========================================================================== #


def test_low_coverage_blocks_market_wide_conclusions():
    coverage = summarize_universe_coverage(classify_many(OBSERVED), EXPECTED,
                                           universe_total=UNIVERSE)
    assert coverage.market_wide_allowed is False
    assert coverage.market_regime_label == INSUFFICIENT_CURRENT_COVERAGE
    message = coverage.blocked_message()
    assert "MARKET-WIDE SUMMARY BLOCKED" in message
    assert "6/241" in message and "2.5%" in message
    assert "Symbol-level results are available only for current symbols" in message


def test_no_bull_bear_or_sideways_claim_under_insufficient_coverage():
    coverage = summarize_universe_coverage(classify_many(OBSERVED), EXPECTED,
                                           universe_total=UNIVERSE)
    label = coverage.market_regime_label
    for forbidden in ("BULL", "BEAR", "SIDEWAYS"):
        assert forbidden not in label


def test_adequate_coverage_allows_market_wide_conclusions():
    coverage = summarize_universe_coverage(
        classify_many({EXPECTED: 200, PRIOR: 41}), EXPECTED, universe_total=UNIVERSE)
    assert coverage.current == 200
    assert coverage.current_percent >= DEFAULT_MINIMUM_COVERAGE_PERCENT
    assert coverage.market_wide_allowed is True
    assert coverage.market_regime_label == ""


def test_symbol_level_results_survive_a_market_wide_block():
    results = classify_many(OBSERVED)
    coverage = summarize_universe_coverage(results, EXPECTED, universe_total=UNIVERSE)
    assert coverage.market_wide_allowed is False
    # The six current symbols remain individually eligible regardless.
    assert len([r for r in results if r.eligible_for_current_analysis]) == 6


def test_the_partial_coverage_warning_is_explicit():
    coverage = summarize_universe_coverage(classify_many(OBSERVED), EXPECTED,
                                           universe_total=UNIVERSE)
    message = coverage.partial_message()
    assert "PARTIAL DAILY DATA COVERAGE" in message
    assert EXPECTED in message
    assert "excluded from BUY/WATCH/AVOID" in message


def test_the_coverage_gate_is_a_data_quality_setting_not_a_strategy_threshold():
    from config.settings_manager import DEFAULT_SETTINGS

    assert COVERAGE_SETTING_KEY in DEFAULT_SETTINGS
    assert minimum_coverage_percent() == DEFAULT_SETTINGS[COVERAGE_SETTING_KEY]
    # It lives outside every strategy block and is documented as data quality.
    import inspect

    import config.settings_manager as module

    source = inspect.getsource(module)
    index = source.index(COVERAGE_SETTING_KEY)
    preamble = source[max(0, index - 700):index]
    assert "DATA-QUALITY" in preamble
    assert "not a strategy threshold" in preamble


def test_an_out_of_range_or_broken_setting_falls_back_to_the_default():
    assert minimum_coverage_percent({COVERAGE_SETTING_KEY: 150.0}) == \
        DEFAULT_MINIMUM_COVERAGE_PERCENT
    assert minimum_coverage_percent({COVERAGE_SETTING_KEY: "nonsense"}) == \
        DEFAULT_MINIMUM_COVERAGE_PERCENT
    assert minimum_coverage_percent({}) == DEFAULT_MINIMUM_COVERAGE_PERCENT


# =========================================================================== #
# THE SCAN GATE SITS BEFORE THE DECISION ENGINE
# =========================================================================== #


def test_the_gate_precedes_indicators_and_the_decision_engine():
    """A stale symbol must never have a current score computed and then hidden."""

    import inspect

    from core import scanner

    source = inspect.getsource(scanner.scan_symbols)
    gate = source.index("_classify_symbol_freshness(")
    assert gate < source.index("calculate_indicators("), (
        "indicators are computed before the freshness gate")
    assert gate < source.index("decision_service.evaluate("), (
        "the decision engine runs before the freshness gate")
    # And the ineligible path leaves the loop without evaluating anything.
    tail = source[gate:source.index("calculate_indicators(")]
    assert "continue" in tail


def test_the_actual_date_comes_from_the_final_accepted_candle():
    import inspect

    from core import scanner

    source = inspect.getsource(scanner._latest_accepted_candle_date)
    assert "frame.index[len(frame) - 1]" in source
    # Checked as code: the helper documents that it ignores mtime and the
    # expected session, so a text search would match its own explanation.
    import ast

    tree = ast.parse(source)
    docstrings = {ast.get_docstring(node, clean=False)
                  for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.FunctionDef))}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in ("st_mtime", "getmtime", "now"), node.attr
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in docstrings:
                continue
            for token in ("mtime", "fetched_at", "expected"):
                assert token not in node.value


def test_the_scan_carries_typed_freshness_out():
    from services.swing_coverage_audit import ScanResults

    results = ScanResults([], freshness=[1, 2, 3], expected_session=EXPECTED)
    assert results.freshness == [1, 2, 3]
    assert results.expected_session == EXPECTED


# =========================================================================== #
# REPLAY OF THE ARCHIVED RUN
# =========================================================================== #


def test_replaying_the_archived_run_reproduces_the_documented_counts():
    archive = pathlib.Path(r"F:\EGX_AI_Trader") / "reports" / ARCHIVED_RUN
    if not (archive / "scan_results.csv").is_file():
        pytest.skip(f"{ARCHIVED_RUN} is not available on this machine")

    rows = list(csv.DictReader(
        open(archive / "scan_results.csv", encoding="utf-8-sig")))
    results = [
        classify_symbol_freshness(
            row.get("Ticker", ""),
            str(row.get("LastCompletedSession") or "")[:10],
            EXPECTED)
        for row in rows
    ]
    coverage = summarize_universe_coverage(results, EXPECTED, universe_total=UNIVERSE)

    assert len(results) == len(rows) == 194
    assert coverage.current == 6
    assert coverage.stale == 188
    assert coverage.market_wide_allowed is False
    assert {r.symbol for r in results if r.eligible_for_current_analysis} == {
        "CIRA.CA", "ETRS.CA", "FTNS.CA", "GBCO.CA", "MEPA.CA", "QNBE.CA"}


def test_the_archived_run_is_not_modified_by_replay():
    archive = pathlib.Path(r"F:\EGX_AI_Trader") / "reports" / ARCHIVED_RUN
    if not archive.is_dir():
        pytest.skip(f"{ARCHIVED_RUN} is not available on this machine")
    assert (archive / "INVALID_DATA_PROVENANCE.md").is_file(), (
        "the run must remain marked invalid as a run-level decision product")
    assert (archive / "scan_results.csv").is_file()


# =========================================================================== #
# BOUNDARIES
# =========================================================================== #


def test_no_yahoo_is_reachable_from_the_freshness_layer():
    import ast

    for path in (REPO_ROOT / "core" / "daily_data_guard.py",):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                assert "yahoo" not in node.id.lower()
            elif isinstance(node, ast.Attribute):
                assert "yahoo" not in node.attr.lower()


def test_strategy_thresholds_are_unchanged():
    from scalping_orb.strategy_config import OrbStrategyConfig

    config = OrbStrategyConfig()
    assert config.minimum_reward_risk == 1.5
    assert config.target_2_r_multiple == 2.0


def test_the_freshness_layer_touches_no_process_or_database():
    import inspect

    from core import daily_data_guard

    source = inspect.getsource(daily_data_guard)
    for forbidden in ("sqlite3", "subprocess", "requests", "urllib",
                      "rubix_live_market", "Popen"):
        assert forbidden not in source
