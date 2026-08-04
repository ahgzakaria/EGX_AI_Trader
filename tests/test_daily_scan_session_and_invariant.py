"""The 2026-08-04 21:56 Daily Scan failure, pinned.

Two defects met in one run:

* every real 2026-08-04 candle was classified FUTURE_DATE, seven hours after
  the auction closed, because the scan asked a *provider* question ("what
  should EODHD have published?") where it needed an *exchange* question
  ("which session has finished?");
* eleven symbols were reported SUCCESS_CURRENT while only three decisions
  existed, so the archive invariant correctly refused to publish.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from core.daily_data_guard import SymbolFreshness, classify_symbol_freshness
from core.egx_session import (
    SETTLEMENT_GRACE_MINUTES,
    authoritative_completed_session,
    expected_latest_completed_session,
)
from services.daily_scan_export import (
    OUTCOME_CALCULATION_ERROR,
    OUTCOME_CURRENT,
    ArchiveInvariantError,
    build_export_metadata,
    coverage_audit_row,
    current_decision_row,
    publish_archive,
    validate_archive,
)

CAIRO = ZoneInfo("Africa/Cairo")
UTC = ZoneInfo("UTC")

# 2026-08-04 is a Tuesday; 08-02 Sunday, 08-06 Thursday, 08-07 Friday.
FAILURE_INSTANT = datetime(2026, 8, 4, 21, 56, tzinfo=CAIRO)


def at(hour, minute=0, day=4):
    return datetime(2026, 8, day, hour, minute, tzinfo=CAIRO)


# =========================================================================== #
# SESSION DATE
# =========================================================================== #


@pytest.mark.parametrize("moment, expected", [
    (at(9, 30), date(2026, 8, 3)),    # pre-open
    (at(12, 0), date(2026, 8, 3)),    # continuous
    (at(14, 14), date(2026, 8, 3)),   # last continuous minute
    (at(14, 15), date(2026, 8, 3)),   # auction opens
    (at(14, 24), date(2026, 8, 3)),   # auction running
    (at(14, 25), date(2026, 8, 3)),   # auction ends, grace starts
    (at(16, 24), date(2026, 8, 3)),   # one minute inside the grace
    (at(16, 25), date(2026, 8, 4)),   # grace elapsed
    (at(23, 59), date(2026, 8, 4)),   # late evening
])
def test_the_completed_session_follows_the_cairo_exchange_clock(moment, expected):
    assert authoritative_completed_session(moment, holidays=()) == expected


def test_the_grace_boundary_is_exactly_the_configured_minutes():
    assert SETTLEMENT_GRACE_MINUTES == 120
    # 14:25 auction end + 120 minutes = 16:25.
    assert authoritative_completed_session(at(16, 24), holidays=()) == date(2026, 8, 3)
    assert authoritative_completed_session(at(16, 25), holidays=()) == date(2026, 8, 4)


def test_a_shorter_configured_grace_moves_the_boundary():
    assert authoritative_completed_session(
        at(14, 30), holidays=(), grace_minutes=0) == date(2026, 8, 4)


def test_sunday_is_a_trading_session():
    assert authoritative_completed_session(at(21, 0, day=2), holidays=()) == date(2026, 8, 2)


@pytest.mark.parametrize("day, expected", [
    (7, date(2026, 8, 6)),   # Friday -> Thursday
    (8, date(2026, 8, 6)),   # Saturday -> Thursday
])
def test_the_weekend_falls_back_to_thursday(day, expected):
    assert authoritative_completed_session(at(21, 0, day=day), holidays=()) == expected


def test_a_holiday_is_never_a_completed_session():
    holiday = date(2026, 8, 4)
    assert authoritative_completed_session(
        at(21, 0), holidays=(holiday,)) == date(2026, 8, 3)


def test_the_utc_date_never_decides_the_session():
    """23:30 Cairo is already the next UTC day; the session must not move."""

    late = datetime(2026, 8, 4, 23, 30, tzinfo=CAIRO)
    assert late.astimezone(UTC).date() == date(2026, 8, 4)
    early = datetime(2026, 8, 5, 0, 30, tzinfo=CAIRO)      # 21:30 UTC on 08-04
    assert early.astimezone(UTC).date() == date(2026, 8, 4)

    assert authoritative_completed_session(late, holidays=()) == date(2026, 8, 4)
    # Just after Cairo midnight it is a new, unfinished day.
    assert authoritative_completed_session(early, holidays=()) == date(2026, 8, 4)


def test_the_provider_question_is_not_the_exchange_question():
    """The exact substitution that caused the incident.

    ``expected_latest_completed_session`` withholds today until the provider
    is known to have published. That is a correct answer to its own question
    and the wrong floor for freshness classification.
    """

    assert expected_latest_completed_session(
        FAILURE_INSTANT, holidays=()) == date(2026, 8, 3)
    assert authoritative_completed_session(
        FAILURE_INSTANT, holidays=()) == date(2026, 8, 4)


# =========================================================================== #
# THE INCIDENT CLASSIFICATION
# =========================================================================== #


def classify(actual, moment=FAILURE_INSTANT):
    return classify_symbol_freshness(
        "AALR", actual, authoritative_completed_session(moment, holidays=()),
        source_provider="EODHD", source_mode="CACHED",
        candle_identity="AALR|x", bars=400, holidays=(),
    )


def test_a_completed_same_day_candle_is_current_not_future():
    """The 181 symbols. At 21:56 Cairo an 08-04 candle is finished data."""

    result = classify(date(2026, 8, 4))

    assert result.freshness_status is SymbolFreshness.CURRENT
    assert result.freshness_status is not SymbolFreshness.FUTURE_DATE
    assert result.eligible_for_current_analysis
    assert result.trading_sessions_behind == 0


def test_a_genuinely_future_candle_is_still_refused():
    result = classify(date(2026, 8, 5))

    assert result.freshness_status is SymbolFreshness.FUTURE_DATE
    assert not result.eligible_for_current_analysis


def test_the_previous_session_becomes_stale_once_today_has_completed():
    """Honest consequence: a provider that has not published lags by one."""

    result = classify(date(2026, 8, 3))

    assert result.freshness_status is SymbolFreshness.STALE
    assert result.trading_sessions_behind == 1


def test_the_actual_candle_date_is_never_replaced_by_the_expected_one():
    result = classify(date(2026, 7, 30))

    assert result.actual_latest_session == "2026-07-30"
    assert result.expected_session == "2026-08-04"


def test_during_the_session_a_same_day_candle_is_still_future():
    """Before completion the contract must not flip the other way."""

    result = classify(date(2026, 8, 4), moment=at(12, 0))

    assert result.expected_session == "2026-08-03"
    assert result.freshness_status is SymbolFreshness.FUTURE_DATE


# =========================================================================== #
# EXPORT CONSISTENCY
# =========================================================================== #

UNIVERSE = [f"SYM{index:03d}" for index in range(241)]


class _Coverage:
    """Minimal stand-in for UniverseCoverage."""

    def __init__(self, current, universe=241, distribution=None):
        self.expected_session = "2026-08-04"
        self.universe_total = universe
        self.history_loaded = universe
        self.current = current
        self.stale = 0
        self.unavailable = universe - current
        self.distribution = distribution or {"2026-08-04": current}
        self.threshold_percent = 60.0
        self.market_wide_allowed = False


def result_row(symbol, decision):
    return {"Ticker": symbol, "Signal": decision, "Score": 70, "Confidence": 75,
            "Price": 10.0, "FrozenSnapshotPrice": 10.0,
            "DecisionPriceSource": "eodhd_daily_close",
            "RubixOverlayApplied": False, "DailyFreshnessStatus": "CURRENT"}


class _Freshness:
    def __init__(self, symbol, status="CURRENT", eligible=True):
        self.symbol = symbol
        self.expected_session = "2026-08-04"
        self.actual_latest_session = "2026-08-04"
        self.trading_sessions_behind = 0
        self.freshness_status = type("S", (), {"value": status})()
        self.eligible_for_current_analysis = eligible
        self.exclusion_reason = ""
        self.source_provider = "EODHD"
        self.source_mode = "CACHED"
        self.candle_identity = f"{symbol}|x"
        self.outcome_status = OUTCOME_CURRENT if eligible else "SKIPPED_STALE_DAILY_DATA"


def build_archive(decision_map, *, failed=(), run_id="RUN_TEST"):
    """Assemble decisions, audit and metadata the way the scanner does."""

    decisions, audit, counts = [], [], {}
    errors = 0
    for ordinal, symbol in enumerate(UNIVERSE):
        decision = decision_map.get(symbol)
        current = decision is not None or symbol in failed
        item = _Freshness(symbol, eligible=current) if current else None
        row = result_row(symbol, decision) if decision is not None else None
        if item is not None:
            outcome = item.outcome_status
            if outcome == OUTCOME_CURRENT and row is None:
                outcome = OUTCOME_CALCULATION_ERROR
                errors += 1
        else:
            outcome = "SKIPPED_STALE_DAILY_DATA"
        audit.append(coverage_audit_row(
            symbol, run_id=run_id, ordinal=ordinal, freshness=item,
            result_row=row, outcome=outcome,
            error=None if symbol not in failed else {
                "category": "SCAN", "code": "CALCULATION_ERROR",
                "message": "indicator failure"}))
        counts[outcome] = counts.get(outcome, 0) + 1
        if row is not None and item is not None:
            decisions.append(current_decision_row(row, run_id=run_id, freshness=item))

    coverage = _Coverage(len(decision_map) + len(failed))
    metadata = build_export_metadata(
        run_id=run_id, run_status="COMPLETED", generated_at_utc="t",
        evaluation_time_utc="t", coverage=coverage, decisions=decisions,
        outcome_counts=counts, calculation_errors=errors)
    return decisions, audit, metadata


ELEVEN = dict(
    [(f"SYM{i:03d}", "BUY") for i in range(3)]
    + [(f"SYM{i:03d}", "WATCH") for i in range(3, 8)]
    + [(f"SYM{i:03d}", "AVOID") for i in range(8, 11)]
)


def test_eleven_current_rows_export_eleven_decisions():
    decisions, audit, metadata = build_archive(ELEVEN)

    assert len(decisions) == 11
    assert sum(1 for row in audit if row["ScanOutcome"] == OUTCOME_CURRENT) == 11
    assert metadata["decision_count"] == 11
    assert metadata["daily_current_count"] == 11
    assert metadata["current_decision_count"] == 11
    assert metadata["decision_calculation_error_count"] == 0
    assert (metadata["buy_count"], metadata["watch_count"],
            metadata["avoid_count"]) == (3, 5, 3)
    assert validate_archive(decisions=decisions, audit=audit, metadata=metadata,
                            universe_symbols=UNIVERSE).ok


def test_watch_and_avoid_are_exported_not_only_buy():
    """scan_current_decisions.csv is not an actionable-BUY-only export."""

    decisions, _, _ = build_archive(ELEVEN)
    exported = {row["Decision"] for row in decisions}

    assert exported == {"BUY", "WATCH", "AVOID"}
    assert sum(1 for row in decisions if row["Decision"] == "WATCH") == 5
    assert sum(1 for row in decisions if row["Decision"] == "AVOID") == 3


def test_a_current_row_whose_decision_failed_is_not_success_current():
    decisions, audit, metadata = build_archive(
        {f"SYM{i:03d}": "BUY" for i in range(3)},
        failed={f"SYM{i:03d}" for i in range(3, 11)})

    failed_rows = [row for row in audit
                   if row["ScanOutcome"] == OUTCOME_CALCULATION_ERROR]

    assert len(decisions) == 3
    assert len(failed_rows) == 8
    for row in failed_rows:
        assert row["DailyFreshnessStatus"] == "CURRENT"
        assert not row["DecisionCalculated"]
        assert row["ScanOutcome"] != OUTCOME_CURRENT
        assert row["ErrorCategory"] == "SCAN"
    assert metadata["daily_current_count"] == 11
    assert metadata["current_decision_count"] == 3
    assert metadata["decision_calculation_error_count"] == 8
    assert validate_archive(decisions=decisions, audit=audit, metadata=metadata,
                            universe_symbols=UNIVERSE).ok


def test_the_audit_holds_one_row_for_every_universe_symbol():
    _, audit, _ = build_archive(ELEVEN)

    assert len(audit) == 241
    assert len({row["Symbol"] for row in audit}) == 241


def test_the_failed_job_shape_is_still_rejected():
    """11 audited SUCCESS_CURRENT against 3 decisions must never publish."""

    decisions, audit, metadata = build_archive(ELEVEN)
    # Re-create the defect: keep three decisions, leave eleven audited current.
    trimmed = decisions[:3]
    metadata = dict(metadata)

    verdict = validate_archive(decisions=trimmed, audit=audit, metadata=metadata,
                               universe_symbols=UNIVERSE)

    assert not verdict.ok
    assert any("11 current rows but 3 decisions" in v for v in verdict.violations)


def test_publication_is_refused_and_writes_nothing(tmp_path):
    decisions, audit, metadata = build_archive(ELEVEN)

    with pytest.raises(ArchiveInvariantError):
        publish_archive(tmp_path, decisions=decisions[:3], audit=audit,
                        metadata=metadata, universe_symbols=UNIVERSE)

    assert not list(tmp_path.glob("scan_*.csv"))


def test_a_valid_archive_publishes_atomically(tmp_path):
    decisions, audit, metadata = build_archive(ELEVEN)

    publish_archive(tmp_path, decisions=decisions, audit=audit,
                    metadata=metadata, universe_symbols=UNIVERSE)

    for name in ("scan_current_decisions.csv", "scan_coverage_audit.csv",
                 "scan_results.csv", "run_metadata.json"):
        assert (tmp_path / name).exists(), name
    decisions_text = (tmp_path / "scan_current_decisions.csv").read_bytes()
    assert (tmp_path / "scan_results.csv").read_bytes() == decisions_text


def test_metadata_separates_data_coverage_from_decision_coverage():
    _, _, metadata = build_archive(
        {f"SYM{i:03d}": "BUY" for i in range(3)},
        failed={f"SYM{i:03d}" for i in range(3, 11)})

    assert metadata["daily_current_coverage_percent"] == pytest.approx(4.6, abs=0.05)
    assert metadata["current_decision_coverage_percent"] == pytest.approx(1.2, abs=0.05)
    assert metadata["daily_current_coverage_percent"] != \
        metadata["current_decision_coverage_percent"]


# =========================================================================== #
# FAILED-ARCHIVE UI PROVENANCE
# =========================================================================== #


class _Widget:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __getattr__(self, name):
        def _call(*args, **kwargs):
            return _Widget()
        return _call


class _FakeSt:
    """Records what the failed-archive panel actually renders."""

    def __init__(self):
        self.errors, self.captions, self.codes = [], [], []
        self.markdowns = []
        self.empty_states = []

    def error(self, message):
        self.errors.append(str(message))

    def caption(self, message):
        self.captions.append(str(message))

    def code(self, message, **kwargs):
        self.codes.append(str(message))

    def markdown(self, message, **kwargs):
        self.markdowns.append(str(message))

    def columns(self, spec):
        count = spec if isinstance(spec, int) else len(spec)
        return [_Widget() for _ in range(count)]

    def __getattr__(self, name):
        def _call(*args, **kwargs):
            return _Widget()
        return _call


class _Snapshot:
    scan_id = "SCAN_TEST"
    state = "FAILED"
    total = 241
    completed = 241
    success = 11
    skipped = 46
    failed = 184
    status_breakdown = {"SKIPPED_FUTURE_DAILY_DATE": 181, "SUCCESS": 11}


class _Job:
    def __init__(self, error):
        self.sanitized_error = error

    def progress(self):
        return _Snapshot()


FAILED_ERROR = (
    "ArchiveInvariantError: archive not published; audit reports 11 current "
    "rows but 3 decisions were exported | expected completed session "
    "2026-08-04; dominant observed session 2026-08-04; universe 241, "
    "daily-current 11, decisions 3, calculation errors 0, dated audit rows 184"
)


def render_failure(monkeypatch, job):
    from dashboard import home

    fake = _FakeSt()
    monkeypatch.setattr(home, "st", fake)
    monkeypatch.setattr(home, "status_bar", lambda items: fake.captions.append(
        " | ".join(f"{label}={value}" for label, value, *_ in items)))
    rendered = home._render_failed_archive_provenance(job)
    return rendered, fake


def test_a_failed_archive_is_not_reported_as_no_market_scan(monkeypatch):
    from dashboard import home

    rendered, fake = render_failure(monkeypatch, _Job(FAILED_ERROR))
    text = " ".join(fake.errors + fake.captions + fake.codes + fake.markdowns)

    assert rendered is True, "the empty state must not be reached"
    assert home.ARCHIVE_FAILURE_HEADLINE in text
    assert "No market scan yet" not in text


def test_the_failure_panel_shows_the_exact_invariant_error(monkeypatch):
    _, fake = render_failure(monkeypatch, _Job(FAILED_ERROR))

    assert any("11 current rows but 3 decisions were exported" in code
               for code in fake.codes)


def test_the_failure_panel_shows_sessions_counts_and_status(monkeypatch):
    _, fake = render_failure(monkeypatch, _Job(FAILED_ERROR))
    text = " ".join(fake.captions)

    assert "Expected completed session=" in text
    assert "Archive status=FAILED VALIDATION" in text
    assert "Decisions published=none" in text
    assert "dominant observed session 2026-08-04" in text
    assert "dated audit rows 184" in text
    assert "no dated candle" not in text


def test_the_failure_panel_shows_the_typed_outcome_distribution(monkeypatch):
    _, fake = render_failure(monkeypatch, _Job(FAILED_ERROR))

    assert any("SKIPPED_FUTURE_DAILY_DATE" in item and "181" in item
               for item in fake.markdowns)


def test_an_unrelated_failure_does_not_claim_an_archive_problem(monkeypatch):
    rendered, _ = render_failure(
        monkeypatch, _Job("ProviderError: EODHD unreachable"))

    assert rendered is False


def test_no_job_falls_through_to_the_normal_empty_state(monkeypatch):
    rendered, _ = render_failure(monkeypatch, None)

    assert rendered is False


# =========================================================================== #
# THE REAL SCANNER ASSEMBLY
# =========================================================================== #
#
# The fixtures above build an archive the way the scanner does. These drive
# `_publish_versioned_exports` itself, so a change to the production assembly
# cannot pass by leaving the test's own copy of the logic intact.


class _RealFreshness:
    """What `classify_symbol_freshness` returns, for the scanner's consumption."""

    def __init__(self, symbol, current=True):
        self.symbol = symbol
        self.expected_session = "2026-08-04"
        self.actual_latest_session = "2026-08-04" if current else "2026-07-30"
        self.trading_sessions_behind = 0 if current else 3
        self.freshness_status = (SymbolFreshness.CURRENT if current
                                 else SymbolFreshness.STALE)
        self.eligible_for_current_analysis = current
        self.exclusion_reason = "" if current else "stale"
        self.source_provider = "EODHD"
        self.source_mode = "CACHED"
        self.candle_identity = f"{symbol}|x"
        self.outcome_status = (OUTCOME_CURRENT if current
                               else "SKIPPED_STALE_DAILY_DATA")


class _Experiment:
    def __init__(self, run_dir):
        self.run_id = "RUN_REAL"
        self.run_dir = str(run_dir)


def publish_via_scanner(tmp_path, decision_map, failed=()):
    """Drive the production export assembly end to end."""

    from core import scanner

    results = [result_row(symbol, decision)
               for symbol, decision in decision_map.items()]
    current = set(decision_map) | set(failed)
    freshness = [_RealFreshness(symbol, current=symbol in current)
                 for symbol in UNIVERSE]
    failures = [{"Symbol": symbol, "Error": "indicator failure",
                 "Status": "CALCULATION_ERROR"} for symbol in failed]
    scanner._publish_versioned_exports(
        _Experiment(tmp_path), results, freshness, UNIVERSE, failures,
        "2026-08-04")
    import csv
    import json

    def rows(name):
        with open(tmp_path / name, newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    from services.run_metadata_service import load_run_metadata
    metadata = load_run_metadata(tmp_path).export
    return rows("scan_current_decisions.csv"), rows("scan_coverage_audit.csv"), metadata


def test_the_scanner_exports_buy_watch_and_avoid(tmp_path):
    decisions, audit, metadata = publish_via_scanner(tmp_path, ELEVEN)

    assert len(decisions) == 11
    assert len(audit) == 241
    counts = {name: sum(1 for row in decisions if row["Decision"] == name)
              for name in ("BUY", "WATCH", "AVOID")}
    assert counts == {"BUY": 3, "WATCH": 5, "AVOID": 3}
    assert metadata["daily_current_count"] == 11
    assert metadata["current_decision_count"] == 11
    assert metadata["decision_calculation_error_count"] == 0


def test_the_scanner_marks_a_failed_decision_as_a_calculation_error(tmp_path):
    """The exact 11-current / 3-decision shape, now internally consistent."""

    decisions, audit, metadata = publish_via_scanner(
        tmp_path, {f"SYM{i:03d}": "BUY" for i in range(3)},
        failed={f"SYM{i:03d}" for i in range(3, 11)})

    errors = [row for row in audit
              if row["ScanOutcome"] == OUTCOME_CALCULATION_ERROR]
    successes = [row for row in audit if row["ScanOutcome"] == OUTCOME_CURRENT]

    assert len(decisions) == 3
    assert len(errors) == 8
    assert len(successes) == 3, "SUCCESS_CURRENT must promise a decision row"
    for row in errors:
        assert row["DailyFreshnessStatus"] == "CURRENT"
        assert row["DecisionCalculated"] == "False"
    assert metadata["daily_current_count"] == 11
    assert metadata["current_decision_count"] == 3
    assert metadata["decision_calculation_error_count"] == 8


def test_the_scanner_publishes_a_compatibility_alias(tmp_path):
    publish_via_scanner(tmp_path, ELEVEN)

    assert (tmp_path / "scan_results.csv").read_bytes() == \
        (tmp_path / "scan_current_decisions.csv").read_bytes()


def test_the_dashboard_consults_the_failure_panel_before_the_empty_state():
    """Pins the call site, not just the panel function.

    Without this, deleting the call from `show_dashboard` leaves every panel
    test passing while the user sees "No market scan yet" again.
    """

    import ast
    import inspect

    from dashboard import home

    tree = ast.parse(inspect.getsource(home.show_dashboard))
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and getattr(node.func, "id", "") == "_render_failed_archive_provenance"]
    empty = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and getattr(node.func, "id", "") == "empty_state"]

    assert calls, "show_dashboard never consults the failed-archive panel"
    assert empty, "the empty state should still exist for a genuinely fresh app"
    assert calls[0].lineno < empty[0].lineno, \
        "the empty state is reached before the failure panel"


def test_undated_symbols_never_occupy_a_session_bucket():
    """A distribution of observed sessions cannot contain "unknown".

    An unknown bucket broke the archive's distribution invariant and could
    win the dominant-session vote, so a run full of dated rows printed
    "Unknown - no dated candle".
    """

    from core.daily_data_guard import summarize_universe_coverage

    dated = [classify(date(2026, 8, 4)) for _ in range(3)]
    undated = [classify_symbol_freshness(
        f"NO{index}", None, "2026-08-04", source_provider="EODHD",
        source_mode="CACHED", bars=0, holidays=()) for index in range(5)]

    coverage = summarize_universe_coverage(dated + undated, "2026-08-04",
                                           universe_total=8)

    assert "unknown" not in coverage.distribution
    assert sum(coverage.distribution.values()) == 3
    assert coverage.distribution == {"2026-08-04": 3}
    assert coverage.unavailable == 5, "they are counted, just not as a session"
    assert max(coverage.distribution, key=coverage.distribution.get) == "2026-08-04"


def test_the_scanner_publishes_a_universe_with_undated_symbols(tmp_path):
    """The replay shape: dated and undated symbols in one archive."""

    from core import scanner
    from core.daily_data_guard import summarize_universe_coverage

    universe = [f"SYM{index:03d}" for index in range(20)]
    freshness = []
    for index, symbol in enumerate(universe):
        actual = date(2026, 8, 4) if index < 12 else None
        freshness.append(classify_symbol_freshness(
            symbol, actual, "2026-08-04", source_provider="EODHD",
            source_mode="CACHED", candle_identity=f"{symbol}|x",
            bars=400 if actual else 0, holidays=()))
    rows = [result_row(symbol, "WATCH") for symbol in universe[:12]]

    scanner._publish_versioned_exports(
        _Experiment(tmp_path), rows, freshness, universe, [], "2026-08-04")

    import json
    from services.run_metadata_service import load_run_metadata
    metadata = load_run_metadata(tmp_path).export
    coverage = summarize_universe_coverage(freshness, "2026-08-04",
                                           universe_total=len(universe))

    assert metadata["dominant_observed_session"] == "2026-08-04"
    assert sum(coverage.distribution.values()) == 12
    assert metadata["daily_current_count"] == 12
    assert metadata["current_decision_count"] == 12
