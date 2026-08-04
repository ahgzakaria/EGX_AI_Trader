"""The Daily Dashboard coverage/exclusion panel.

A completed 241-symbol scan rendered its results and then took the whole page
down on the exclusion table:

    TypeError: with_company_name_column() got an unexpected keyword argument
    'ticker_column'

The helper's parameter is ``symbol_column`` and every other caller in the
dashboard passes it positionally. Only this site invented a name, so nothing
caught it until a scan produced excluded symbols to render.
"""

from __future__ import annotations

import inspect

import pandas as pd
import pytest

from core.daily_data_guard import classify_symbol_freshness
from dashboard.formatting import NAME_COLUMN, with_company_name_column

EXPECTED_SESSION = "2026-08-04"


# =========================================================================== #
# THE HELPER CONTRACT
# =========================================================================== #


def test_the_canonical_parameter_is_symbol_column():
    parameters = list(inspect.signature(with_company_name_column).parameters)

    assert parameters[:2] == ["frame", "symbol_column"]
    assert "ticker_column" not in parameters


def test_the_obsolete_keyword_is_rejected_loudly():
    """The exact production failure, pinned.

    ``ticker_column`` is intentionally unsupported: adding an alias would keep
    two names alive for one concept. A typo must raise, not be absorbed.
    """

    frame = pd.DataFrame({"Ticker": ["AALR", "COMI"]})

    with pytest.raises(TypeError, match="ticker_column"):
        with_company_name_column(frame, ticker_column="Ticker")


def test_every_dashboard_call_site_uses_the_canonical_api():
    """Guards every call site, not only the one that crashed.

    Inspected as syntax, not as text: a prose match would fire on this test's
    own explanation and on the comment that documents the fix, which is a
    guard that reports itself rather than the code.
    """

    import ast
    import pathlib

    allowed = set(inspect.signature(with_company_name_column).parameters)
    offenders = []
    for path in sorted(pathlib.Path("dashboard").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name != "with_company_name_column":
                continue
            for keyword in node.keywords:
                if keyword.arg not in allowed:
                    offenders.append(f"{path.name}:{node.lineno} {keyword.arg}=")
            if not node.keywords and len(node.args) < 2:
                offenders.append(f"{path.name}:{node.lineno} no symbol column")

    assert offenders == [], f"non-canonical call sites: {offenders}"


def test_the_helper_preserves_rows_order_and_symbols():
    frame = pd.DataFrame({"Ticker": ["COMI", "AALR", "ETRS"], "Keep": [1, 2, 3]})

    view = with_company_name_column(frame, "Ticker")

    assert list(view["Ticker"]) == ["COMI", "AALR", "ETRS"]
    assert list(view["Keep"]) == [1, 2, 3]
    assert len(view) == 3
    assert NAME_COLUMN in view.columns


def test_the_helper_does_not_mutate_the_caller_frame():
    frame = pd.DataFrame({"Ticker": ["COMI"]})

    with_company_name_column(frame, "Ticker")

    assert NAME_COLUMN not in frame.columns


def test_an_empty_frame_is_returned_unchanged():
    empty = pd.DataFrame({"Ticker": []})

    assert with_company_name_column(empty, "Ticker") is empty


def test_an_unmapped_symbol_keeps_its_row():
    frame = pd.DataFrame({"Ticker": ["NOT_A_REAL_SYMBOL"]})

    view = with_company_name_column(frame, "Ticker")

    assert len(view) == 1
    assert view["Ticker"].iloc[0] == "NOT_A_REAL_SYMBOL"
    assert view[NAME_COLUMN].iloc[0]      # a placeholder, never a crash


# =========================================================================== #
# THE PANEL, AGAINST THE REAL AUDIT SCHEMA
# =========================================================================== #


def excluded_rows(count=10):
    """Freshness results shaped exactly as the scanner produces them."""

    from datetime import date

    results = []
    for index in range(count):
        results.append(classify_symbol_freshness(
            f"STL{index:02d}", date(2026, 7, 30), EXPECTED_SESSION,
            source_provider="EODHD", source_mode="CACHED",
            candle_identity=f"STL{index:02d}|x", bars=400, holidays=()))
    return results


def test_the_production_audit_schema_enriches_cleanly():
    """`SymbolFreshnessResult.as_row()` is what the panel actually renders."""

    frame = pd.DataFrame([item.as_row() for item in excluded_rows()])

    view = with_company_name_column(frame, "Ticker")

    assert len(view) == 10, "ten stale rows must remain ten"
    assert list(view["Ticker"]) == [f"STL{index:02d}" for index in range(10)]
    for field in ("ExpectedSession", "ActualSession", "TradingSessionsBehind",
                  "FreshnessStatus", "EligibleForCurrentAnalysis",
                  "FreshnessOutcome", "ExclusionReason", "SourceProvider",
                  "SourceMode"):
        assert field in view.columns, f"{field} was dropped by enrichment"
    assert set(view["FreshnessStatus"]) == {"STALE"}
    assert not any(view["EligibleForCurrentAnalysis"])


# =========================================================================== #
# THE PANEL GUARD
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
    def __init__(self):
        self.warnings, self.frames = [], []

    def warning(self, message):
        self.warnings.append(str(message))

    def dataframe(self, frame, **kwargs):
        self.frames.append(frame)

    def __getattr__(self, name):
        def _call(*args, **kwargs):
            return _Widget()
        return _call


def test_a_mapping_failure_degrades_to_bare_symbols(monkeypatch):
    """Rows survive a broken lookup; the evidence is never hidden."""

    from dashboard import home

    fake = _FakeSt()
    monkeypatch.setattr(home, "st", fake)

    def broken(frame, symbol_column, **kwargs):
        raise LookupError("universe unavailable")

    monkeypatch.setattr(home, "with_company_name_column", broken)
    frame = pd.DataFrame([item.as_row() for item in excluded_rows()])

    view = home._with_company_names(frame, "Ticker")

    assert len(view) == 10, "a name lookup must never drop a withheld symbol"
    assert list(view["Ticker"]) == list(frame["Ticker"])
    assert fake.warnings == [home.COMPANY_LOOKUP_WARNING]


def test_a_programming_error_is_never_swallowed(monkeypatch):
    """A wrong argument name must keep failing loudly - that was the defect."""

    from dashboard import home

    monkeypatch.setattr(home, "st", _FakeSt())

    def wrong_signature(frame, symbol_column, **kwargs):
        raise TypeError("unexpected keyword argument 'ticker_column'")

    monkeypatch.setattr(home, "with_company_name_column", wrong_signature)

    with pytest.raises(TypeError):
        home._with_company_names(pd.DataFrame({"Ticker": ["AALR"]}), "Ticker")


def test_the_happy_path_enriches_without_warning(monkeypatch):
    from dashboard import home

    fake = _FakeSt()
    monkeypatch.setattr(home, "st", fake)
    frame = pd.DataFrame([item.as_row() for item in excluded_rows()])

    view = home._with_company_names(frame, "Ticker")

    assert NAME_COLUMN in view.columns
    assert fake.warnings == []
    assert len(view) == 10
