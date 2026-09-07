"""Daily candle provenance: the displayed date must be the source date.

Observed 2026-08-04: the Daily Dashboard headlined "Latest completed candle
2026-08-03" above a table of Thursday 2026-07-30 closes.

Evidence gathered before writing these tests:

* every layer agreed per symbol - the raw no-cache EODHD response, the raw
  cache file and the scan row all carried 2026-07-30 with the same OHLCV for
  SPIN/ORHD/ACAP/ABUK, so nothing was relabelled;
* a bounded authenticated probe classified those four RAW_STALE (EODHD
  returned 07-28, 07-29, 07-30 and no 08-02/08-03) while QNBE and CIRA came
  back RAW_CURRENT with both - EODHD's EGX coverage was partial;
* the banner computed ``max(session_dates)``, so 6 of 194 rows named the
  session for a table that was 184/194 one session behind.

No test here contacts a provider, writes a cache, touches Rubix or changes a
threshold.
"""

from __future__ import annotations

import pathlib

import pytest

from core.daily_data_guard import (
    RUBIX_STALE_HEADLINE,
    DailyDataVerdict,
    evaluate_daily_data,
    evaluate_quote_freshness,
)
from dashboard.home import session_coverage
from dashboard.scan_status_panel import UNKNOWN_CANDLE, scan_status_view


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
AFFECTED_RUN = "RUN_20260804_005327"

#: The distribution actually observed in the affected run.
OBSERVED_DISTRIBUTION = {
    "2014-03-03": 1, "2026-07-06": 1, "2026-07-09": 1,
    "2026-07-27": 1, "2026-07-30": 184, "2026-08-03": 6,
}
EXPECTED_SESSION = "2026-08-03"
ACTUAL_SESSION = "2026-07-30"


def rows_for(distribution):
    rows = []
    for date, count in distribution.items():
        for index in range(count):
            rows.append({
                "Ticker": f"S{len(rows)}.CA",
                "LastCompletedSession": f"{date}T14:30:00+03:00",
                "CompletedSessionClose": 10.0 + index,
                "Price": 10.0 + index,
                "CompletedSessionProvider": "eodhd",
            })
    return rows


# =========================================================================== #
# THE OBSERVED INCIDENT
# =========================================================================== #


def test_the_headline_reports_the_session_the_rows_actually_carry():
    """max() let 6 rows name the session for 194."""

    coverage = session_coverage(rows_for(OBSERVED_DISTRIBUTION))
    assert coverage["dominant"] == ACTUAL_SESSION
    assert coverage["newest"] == "2026-08-03"
    assert coverage["dominant"] != coverage["newest"], (
        "this fixture must reproduce the mixed-session incident")
    assert coverage["dominant_rows"] == 184
    assert coverage["total"] == 194
    assert coverage["mixed"] is True


def test_the_banner_metadata_publishes_the_dominant_session_not_the_newest():
    """The exact line that produced the false headline.

    ``_observed_metadata`` feeds the banner. It used to publish
    ``max(session_dates)``, so six 2026-08-03 rows named the session for a
    table where 184 of 194 rows were 2026-07-30.
    """

    from dashboard.home import _observed_metadata

    metadata = _observed_metadata(rows_for(OBSERVED_DISTRIBUTION))
    assert metadata["latest_completed_candle"] == ACTUAL_SESSION
    assert metadata["latest_completed_candle"] != "2026-08-03"
    coverage = metadata["session_coverage"]
    assert coverage["newest"] == "2026-08-03"
    assert coverage["mixed"] is True


def test_the_expected_session_can_never_replace_the_actual_candle_date():
    """The banner is labelled "Latest completed candle" - it must not print a
    date the data does not support."""

    class _Progress:
        state = "COMPLETED"
        rubix_overlay_available = 200
        total = 241
        rubix_batch_status = "RUBIX_BATCH_OK"
        eodhd_refresh_successes = 0

    view = scan_status_view(_Progress(), expected_session=EXPECTED_SESSION,
                            result_metadata={"latest_completed_candle": ""})
    assert view["latest_completed_candle"] == UNKNOWN_CANDLE
    assert EXPECTED_SESSION not in view["latest_completed_candle"]


def test_the_actual_candle_date_is_shown_when_known():
    class _Progress:
        state = "COMPLETED"
        rubix_overlay_available = 200
        total = 241
        rubix_batch_status = "RUBIX_BATCH_OK"
        eodhd_refresh_successes = 0

    view = scan_status_view(_Progress(), expected_session=EXPECTED_SESSION,
                            result_metadata={"latest_completed_candle": ACTUAL_SESSION})
    assert view["latest_completed_candle"] == ACTUAL_SESSION


def test_an_in_progress_scan_claims_no_candle_date():
    class _Progress:
        state = "PREPARING_RUBIX"
        rubix_overlay_available = 0
        total = 241
        rubix_batch_status = ""
        eodhd_refresh_successes = 0

    view = scan_status_view(_Progress(), expected_session=EXPECTED_SESSION)
    assert EXPECTED_SESSION not in view["latest_completed_candle"]


# =========================================================================== #
# FAIL-CLOSED RULE
# =========================================================================== #


def test_the_observed_incident_blocks_analysis():
    decision = evaluate_daily_data(
        session_coverage(rows_for(OBSERVED_DISTRIBUTION)), EXPECTED_SESSION)
    assert decision.verdict is DailyDataVerdict.MIXED_SESSION_DATES
    assert decision.blocked is True
    assert decision.analysis_allowed is False
    assert "DAILY DATA DATE MISMATCH" in decision.message
    assert f"Expected completed session: {EXPECTED_SESSION}" in decision.message
    assert f"Actual EODHD candle date: {ACTUAL_SESSION}" in decision.message
    assert "Analysis is blocked" in decision.message


def test_a_uniformly_stale_scan_blocks_analysis():
    coverage = session_coverage(rows_for({ACTUAL_SESSION: 200}))
    decision = evaluate_daily_data(coverage, EXPECTED_SESSION)
    assert decision.verdict is DailyDataVerdict.STALE_BEHIND_EXPECTED
    assert decision.blocked is True
    assert f"Actual EODHD candle date: {ACTUAL_SESSION}" in decision.message


def test_a_current_uniform_scan_is_allowed():
    coverage = session_coverage(rows_for({EXPECTED_SESSION: 200}))
    decision = evaluate_daily_data(coverage, EXPECTED_SESSION)
    assert decision.verdict is DailyDataVerdict.CURRENT
    assert decision.blocked is False


def test_a_displayed_date_that_disagrees_with_the_rows_blocks():
    coverage = session_coverage(rows_for({ACTUAL_SESSION: 200}))
    decision = evaluate_daily_data(coverage, ACTUAL_SESSION,
                                   displayed_session=EXPECTED_SESSION)
    assert decision.verdict is DailyDataVerdict.DISPLAYED_DATE_MISMATCH
    assert decision.blocked is True


def test_no_dated_candle_blocks_rather_than_assuming():
    decision = evaluate_daily_data(session_coverage([]), EXPECTED_SESSION)
    assert decision.verdict is DailyDataVerdict.NO_DATED_CANDLE
    assert decision.blocked is True


def test_a_missing_expected_session_never_unblocks_a_mixed_scan():
    """An unavailable calendar must not become permission to proceed."""

    decision = evaluate_daily_data(
        session_coverage(rows_for(OBSERVED_DISTRIBUTION)), "")
    assert decision.blocked is True


# =========================================================================== #
# RELABELLING AND METADATA
# =========================================================================== #


def test_old_ohlc_relabelled_with_a_newer_date_is_detected():
    """A 07-30 bar carrying an 08-03 label must not pass as current."""

    rows = rows_for({ACTUAL_SESSION: 100})
    for row in rows[:50]:                      # relabel half, keep their OHLCV
        row["LastCompletedSession"] = f"{EXPECTED_SESSION}T14:30:00+03:00"
    decision = evaluate_daily_data(session_coverage(rows), EXPECTED_SESSION)
    assert decision.blocked is True
    assert decision.verdict is DailyDataVerdict.MIXED_SESSION_DATES


def test_cache_metadata_cannot_advance_freshness_on_its_own():
    """Only row session dates count - never a refresh time or file mtime."""

    coverage = session_coverage(rows_for({ACTUAL_SESSION: 200}))
    decision = evaluate_daily_data(coverage, EXPECTED_SESSION)
    assert decision.blocked is True
    # Checked as executable code: the module *documents* that it ignores mtime
    # and refresh markers, so a text search would match its own explanation.
    import ast
    import inspect

    from core import daily_data_guard

    tree = ast.parse(inspect.getsource(daily_data_guard))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)
    forbidden = ("mtime", "st_mtime", "fetched_at", "refreshed_at", "now")
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in forbidden, f"the guard reads .{node.attr}"
        elif isinstance(node, ast.Name):
            assert node.id not in forbidden, f"the guard reads {node.id}"
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in docstrings:
                continue
            for token in ("mtime", "fetched_at", "refreshed_at"):
                assert token not in node.value, f"the guard keys on {token}"


# =========================================================================== #
# LIVE OVERLAY FRESHNESS
# =========================================================================== #


def test_a_quote_from_an_earlier_session_is_stale():
    freshness = evaluate_quote_freshness("2026-08-03T11:30:09+00:00", ACTUAL_SESSION)
    assert freshness.fresh is False
    assert freshness.may_overwrite_close is False
    assert RUBIX_STALE_HEADLINE in freshness.message
    assert "Last quote session: 2026-08-03" in freshness.message


def test_a_quote_from_the_displayed_session_is_fresh():
    freshness = evaluate_quote_freshness(f"{ACTUAL_SESSION}T14:30:00+03:00",
                                         ACTUAL_SESSION)
    assert freshness.fresh is True
    assert freshness.may_overwrite_close is True


def test_freshness_is_decided_by_session_not_by_elapsed_seconds():
    """The incident recorded age 16034 s as FRESH.

    Hours of age within one session are fine; one second across a session
    boundary is not. Only the session decides.
    """

    same_session_but_old = evaluate_quote_freshness(
        f"{ACTUAL_SESSION}T10:00:00+03:00", ACTUAL_SESSION)
    assert same_session_but_old.fresh is True

    next_session = evaluate_quote_freshness("2026-08-03T10:00:00+03:00",
                                            ACTUAL_SESSION)
    assert next_session.fresh is False


def test_an_undated_quote_is_never_treated_as_current():
    freshness = evaluate_quote_freshness("", ACTUAL_SESSION)
    assert freshness.fresh is False
    assert "unknown" in freshness.message


# =========================================================================== #
# THE AFFECTED RUN
# =========================================================================== #


def test_the_affected_run_is_marked_invalid_and_preserved():
    run = REPO_ROOT / "reports" / AFFECTED_RUN
    if not run.exists():
        pytest.skip(f"{AFFECTED_RUN} is not present in this checkout")
    marker = run / "INVALID_DATA_PROVENANCE.md"
    assert marker.is_file(), "the affected run carries no invalidity marker"
    text = marker.read_text(encoding="utf-8")
    assert "INVALID_DATA_PROVENANCE" in text
    assert "NOT FOR DECISION USE" in text
    # Preserved, not rewritten.
    assert (run / "scan_results.csv").is_file()
    assert (run / "run_metadata.json").is_file()


# =========================================================================== #
# BOUNDARIES
# =========================================================================== #


def test_no_yahoo_is_reachable_from_the_guard_or_the_banner():
    """Checked as executable code, not as text.

    scan_status_panel documents a previous yahoo mislabel in its prose, so a
    plain text search would match the very explanation of why yahoo is not
    used. Identifiers and runtime literals are what matter.
    """

    import ast

    for path in (REPO_ROOT / "core" / "daily_data_guard.py",
                 REPO_ROOT / "dashboard" / "scan_status_panel.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    docstrings.add(doc)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                assert "yahoo" not in node.id.lower(), path.name
            elif isinstance(node, ast.Attribute):
                assert "yahoo" not in node.attr.lower(), path.name
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in docstrings:
                    continue
                assert "yahoo" not in node.value.lower(), (
                    f"{path.name} carries a runtime yahoo literal")


def test_thresholds_are_unchanged():
    from scalping_orb.strategy_config import OrbStrategyConfig

    config = OrbStrategyConfig()
    assert config.minimum_reward_risk == 1.5
    assert config.target_2_r_multiple == 2.0


def test_the_guard_touches_no_provider_cache_or_process():
    import inspect

    from core import daily_data_guard

    source = inspect.getsource(daily_data_guard)
    for forbidden in ("requests", "urllib", "sqlite3", "subprocess", "open(",
                      "eodhd_client", "rubix_live_market"):
        assert forbidden not in source, f"the guard reaches for {forbidden}"


# --- a second supplier for the same tail --------------------------------------
#
# On 2026-09-07 the collector stopped eight minutes into the session, no Rubix
# bar could be built from 29 captured minutes, and every symbol fell back to
# plain EODHD: eodhd_plus_rubix went from 187 of 222 symbols the session before
# to zero, and the history stopped a day short of the last completed session.
#
# The export holds that session. It is the exchange's official auction close
# rather than one built from minutes this project captured, and on 2026-09-06
# where both exist the volumes agree exactly while the closes differ by 0.15%
# at the median. So it goes second, and it is named when it is used.

def _frame(dates, close=10.0):
    import pandas as pd

    index = pd.to_datetime(dates)
    return pd.DataFrame(
        {"Open": close, "High": close, "Low": close, "Close": close,
         "Adj Close": close, "Volume": 1000.0},
        index=index)


def test_the_export_fills_only_sessions_rubix_did_not(monkeypatch):
    import pandas as pd

    from core import local_rubix_history as history

    monkeypatch.setattr(history, "default_bridge_cache", lambda: object())
    monkeypatch.setattr(history, "_bridge_rows_after", lambda *a, **k: [
        {"session_date": pd.Timestamp("2026-09-06"), "Open": 1.0, "High": 1.0,
         "Low": 1.0, "Close": 1.0, "Adj Close": 1.0, "Volume": 5.0}])
    monkeypatch.setattr(history, "_export_rows_after", lambda *a, **k: [
        # the same session Rubix already supplied, and one it did not
        {"session_date": pd.Timestamp("2026-09-06"), "Open": 9.0, "High": 9.0,
         "Low": 9.0, "Close": 9.0, "Adj Close": 9.0, "Volume": 99.0},
        {"session_date": pd.Timestamp("2026-09-07"), "Open": 2.0, "High": 2.0,
         "Low": 2.0, "Close": 2.0, "Adj Close": 2.0, "Volume": 7.0}])

    frame, prov = history.append_bridge_bars(_frame(["2026-09-03"]), "COMI.CA")

    assert prov["bridge_sessions_appended"] == 2
    assert frame.loc[pd.Timestamp("2026-09-06"), "Close"] == 1.0, (
        "Rubix owns the session it supplied; the export must not restate it")
    assert frame.loc[pd.Timestamp("2026-09-07"), "Close"] == 2.0
    assert prov["bridge_supplements"] == ("2026-09-07",)
    assert "MUBASHER_EXPORT_BRIDGE" in prov["bridge_provider"]


def test_a_tail_rubix_covered_alone_names_only_rubix(monkeypatch):
    import pandas as pd

    from core import local_rubix_history as history

    monkeypatch.setattr(history, "default_bridge_cache", lambda: object())
    monkeypatch.setattr(history, "_bridge_rows_after", lambda *a, **k: [
        {"session_date": pd.Timestamp("2026-09-06"), "Open": 1.0, "High": 1.0,
         "Low": 1.0, "Close": 1.0, "Adj Close": 1.0, "Volume": 5.0}])
    monkeypatch.setattr(history, "_export_rows_after", lambda *a, **k: [
        {"session_date": pd.Timestamp("2026-09-06"), "Open": 9.0, "High": 9.0,
         "Low": 9.0, "Close": 9.0, "Adj Close": 9.0, "Volume": 99.0}])

    _, prov = history.append_bridge_bars(_frame(["2026-09-03"]), "COMI.CA")
    assert prov["bridge_supplements"] == ()
    assert prov["bridge_provider"] == history.RUBIX_DAILY_BRIDGE, (
        "naming a supplement that supplied nothing hides which source acted")


def test_the_router_reports_the_export_in_the_provider_name():
    """A second source must not be invisible in the provenance."""

    import inspect

    from core import research_router

    source = inspect.getsource(research_router)
    assert "eodhd_plus_rubix_plus_export" in source
    assert 'bridge_md.get("bridge_supplements")' in source
