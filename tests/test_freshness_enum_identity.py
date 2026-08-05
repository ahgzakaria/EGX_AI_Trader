"""Freshness classification must survive two live copies of its module.

The 2026-08-05 08:02 scan completed all 241 symbols and was refused
publication:

    audit reports 185 current rows but 166 decisions were exported
    | universe 241, daily-current 166, decisions 166, calculation errors 0

Every number was produced by the same freshness results. The audit reads
``outcome_status``, which is a dict lookup and works across module copies;
``summarize_universe_coverage`` and the decisions gate used ``is``, which does
not. Streamlit's reloader can leave two live copies of
``core.daily_data_guard``, and an enum member from one is never ``is`` a
member of the other - so 19 correct, current symbols were dropped from the
export while remaining SUCCESS_CURRENT in the audit.

The archive invariant caught it and published nothing, which is exactly right.
"""

from __future__ import annotations

import importlib.util

import pytest

import core.daily_data_guard as guard
from core.daily_data_guard import SymbolFreshness, summarize_universe_coverage

EXPECTED = "2026-08-04"


@pytest.fixture(scope="module")
def second_copy():
    """A second, independent module object for the same source."""

    spec = importlib.util.find_spec("core.daily_data_guard")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def classify(module, symbol, actual=EXPECTED):
    return module.classify_symbol_freshness(
        symbol, actual, EXPECTED, source_provider="EODHD",
        source_mode="CACHED", candle_identity=f"{symbol}|x", bars=400,
        holidays=())


def test_the_two_copies_really_are_distinct(second_copy):
    """Guards the fixture: if they were the same, nothing below proves anything."""

    assert second_copy is not guard
    assert second_copy.SymbolFreshness is not SymbolFreshness
    assert second_copy.SymbolFreshness.CURRENT is not SymbolFreshness.CURRENT


def test_a_cross_copy_member_still_compares_equal(second_copy):
    assert second_copy.SymbolFreshness.CURRENT == SymbolFreshness.CURRENT
    assert guard.FRESHNESS_OUTCOME[second_copy.SymbolFreshness.CURRENT] == \
        "SUCCESS_CURRENT"


def test_coverage_counts_every_current_symbol_across_module_copies(second_copy):
    """The exact production split: 166 from one copy, 19 from the other."""

    items = ([classify(guard, f"A{index:03d}") for index in range(166)]
             + [classify(second_copy, f"B{index:03d}") for index in range(19)]
             + [classify(guard, f"S{index:02d}", "2026-07-30") for index in range(10)])

    coverage = summarize_universe_coverage(items, EXPECTED,
                                           universe_total=len(items))
    audited_current = sum(1 for item in items
                          if item.outcome_status == "SUCCESS_CURRENT")

    assert audited_current == 185
    assert coverage.current == 185, "coverage undercounted the other module copy"
    assert coverage.stale == 10
    assert coverage.current == audited_current, \
        "coverage and the audit must never disagree about the same symbols"


def test_the_decisions_gate_exports_every_audited_current_symbol(second_copy,
                                                                tmp_path):
    """Drives the real export assembly with a mixed-copy batch."""

    import csv

    from core import scanner

    universe = ([f"A{index:03d}" for index in range(166)]
                + [f"B{index:03d}" for index in range(19)]
                + [f"S{index:02d}" for index in range(10)])
    freshness = ([classify(guard, f"A{index:03d}") for index in range(166)]
                 + [classify(second_copy, f"B{index:03d}") for index in range(19)]
                 + [classify(guard, f"S{index:02d}", "2026-07-30")
                    for index in range(10)])
    results = [{"Ticker": symbol, "Signal": "WATCH", "Score": 70,
                "Confidence": 75, "Price": 10.0, "FrozenSnapshotPrice": 10.0,
                "DecisionPriceSource": "eodhd_daily_close",
                "RubixOverlayApplied": False,
                "DailyFreshnessStatus": "CURRENT"}
               for symbol in universe[:185]]

    class _Experiment:
        run_id = "RUN_MIXED"
        run_dir = str(tmp_path)

    # Publishing at all is the assertion: the invariant refuses a mismatch.
    scanner._publish_versioned_exports(
        _Experiment(), results, freshness, universe, [], EXPECTED)

    with open(tmp_path / "scan_current_decisions.csv", newline="",
              encoding="utf-8") as handle:
        decisions = list(csv.DictReader(handle))
    with open(tmp_path / "scan_coverage_audit.csv", newline="",
              encoding="utf-8") as handle:
        audit = list(csv.DictReader(handle))

    current_rows = [row for row in audit if row["ScanOutcome"] == "SUCCESS_CURRENT"]
    assert len(decisions) == 185
    assert len(current_rows) == 185
    assert len(audit) == len(universe) == 195
    assert {row["Symbol"] for row in decisions} == set(universe[:185])


def test_overlay_permission_survives_a_cross_copy_status(second_copy):
    from datetime import datetime, timedelta, timezone

    from core.rubix_quote_freshness import classify_rubix_quote
    from services.analysis_freshness_service import SymbolFreshness as ServiceEnum

    assert second_copy.SymbolFreshness.CURRENT == ServiceEnum.CURRENT

    evaluated = datetime(2026, 8, 4, 11, 0, tzinfo=timezone.utc)
    assessment = classify_rubix_quote(
        "AALR", evaluated_at=evaluated, mapping_verified=True, quote_price=12.5,
        market_timestamp=evaluated - timedelta(seconds=5),
        receive_timestamp=evaluated - timedelta(seconds=4),
        permitted_session="2026-08-04")

    from core.rubix_quote_freshness import evaluate_overlay_permission

    permission = evaluate_overlay_permission(
        assessment,
        daily_symbol_current=(second_copy.SymbolFreshness.CURRENT
                              == ServiceEnum.CURRENT))
    assert permission.may_enter_decision_inputs


def test_no_module_compares_this_enum_by_identity():
    """`is` on a reloadable enum is the defect. Keep it gone."""

    import ast
    import pathlib

    offenders = []
    for path in sorted(pathlib.Path(".").glob("*/*.py")):
        if path.as_posix().startswith("tests/"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            if not any(isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops):
                continue
            for operand in node.comparators:
                target = operand
                while isinstance(target, ast.Attribute):
                    if isinstance(target.value, ast.Name) and \
                            target.value.id == "SymbolFreshness":
                        offenders.append(f"{path.as_posix()}:{node.lineno}")
                        break
                    target = target.value

    assert offenders == [], f"identity comparisons on SymbolFreshness: {offenders}"
