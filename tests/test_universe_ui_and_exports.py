"""Ticker + company-name presentation across the pages, and export integrity.

Contract: dropdowns render ``TICKER — Full Company Name`` while still returning
the canonical ticker; tables keep ``الرمز`` and ``اسم السهم`` as SEPARATE columns
so filtering, sorting and export stay usable; and an exported file always
carries the COMPLETE company name even when a column is visually narrow.

Source-level assertions are used for the Streamlit pages so no browser session,
provider or database is required.
"""

from __future__ import annotations

import csv
import inspect
import json
from pathlib import Path

import pandas as pd
import pytest

from core.symbols import load_approved_symbol_options
from core.universe import active_universe, display_label
from dashboard.formatting import (
    NAME_COLUMN,
    SYMBOL_COLUMN,
    company_name,
    symbol_option_label,
    symbol_ticker,
    with_company_name_column,
)

ARABIC_NAME = "اسم السهم"
ARABIC_SYMBOL = "الرمز"


def _source(module_path):
    return Path(module_path).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Column headers
# --------------------------------------------------------------------------- #

def test_the_shared_headers_are_the_required_arabic_labels():
    assert SYMBOL_COLUMN == ARABIC_SYMBOL
    assert NAME_COLUMN == ARABIC_NAME


@pytest.mark.parametrize("page", [
    "dashboard/home.py",
    "dashboard/scalping.py",
    "dashboard/uptrend_pullback.py",
    "dashboard/opportunities.py",
    "dashboard/expected_range_scalper.py",
])
def test_table_pages_add_a_separate_company_name_column(page):
    source = _source(page)
    assert "NAME_COLUMN" in source, f"{page} never renders a company-name column"
    assert ("company_name" in source or "company_names" in source
            or "with_company_name_column" in source)


@pytest.mark.parametrize("page", [
    "dashboard/watchlist.py",
    "dashboard/forward_testing.py",
    "dashboard/run_history.py",
    "dashboard/decision_support.py",
])
def test_history_and_list_pages_enrich_their_symbol_column(page):
    assert "with_company_name_column" in _source(page)


@pytest.mark.parametrize("page", [
    "dashboard/home.py",
    "dashboard/scalping.py",
    "dashboard/uptrend_pullback.py",
    "dashboard/ai_stock_analysis.py",
    "dashboard/stock_details.py",
    "dashboard/decision_support.py",
])
def test_symbol_dropdowns_render_ticker_plus_name(page):
    source = _source(page)
    assert ("symbol_option_label" in source or "display_label" in source), page


def test_the_backtest_configuration_states_its_universe():
    source = _source("dashboard/settings.py")
    assert "universe_provenance" in source
    assert "active EODHD EGX symbols" in source


# --------------------------------------------------------------------------- #
# Dropdown contract
# --------------------------------------------------------------------------- #

def test_every_dropdown_option_is_ticker_dash_name_but_returns_the_ticker():
    options = load_approved_symbol_options()
    assert len(options) == 229      # 241 EODHD codes less 11 aliases and 1 dormant symbol
    for option in options:
        label = option.display_label
        assert label.startswith(f"{option.ticker} — ")
        assert label.endswith(option.english_name)
        # The value handed to strategy code carries no name and no suffix.
        assert "—" not in option.ticker
        assert "." not in option.ticker
        assert symbol_ticker(label) == option.ticker


def test_the_formatter_matches_the_universe_label_for_every_symbol():
    for record in active_universe():
        assert symbol_option_label(record.canonical_symbol) == record.display_label
        assert display_label(record.canonical_symbol) == record.display_label


# --------------------------------------------------------------------------- #
# Table contract
# --------------------------------------------------------------------------- #

def test_the_name_column_sits_next_to_the_ticker_and_leaves_it_untouched():
    frame = pd.DataFrame({ARABIC_SYMBOL: ["COMI", "GRCA"], "Score": [90, 80]})
    view = with_company_name_column(frame, ARABIC_SYMBOL)

    assert list(view.columns) == [ARABIC_SYMBOL, ARABIC_NAME, "Score"]
    assert list(view[ARABIC_SYMBOL]) == ["COMI", "GRCA"]
    # The original frame is not mutated — presentation never rewrites data.
    assert list(frame.columns) == [ARABIC_SYMBOL, "Score"]


def test_filtering_and_sorting_still_work_on_the_bare_ticker_column():
    frame = with_company_name_column(
        pd.DataFrame({ARABIC_SYMBOL: ["SWDY", "COMI", "GRCA"]}), ARABIC_SYMBOL)

    filtered = frame[frame[ARABIC_SYMBOL].str.startswith("CO")]
    assert list(filtered[ARABIC_SYMBOL]) == ["COMI"]
    assert list(frame.sort_values(ARABIC_SYMBOL)[ARABIC_SYMBOL]) == \
        ["COMI", "GRCA", "SWDY"]


def test_a_frame_without_the_symbol_column_is_returned_unchanged():
    frame = pd.DataFrame({"Other": [1, 2]})
    assert with_company_name_column(frame, ARABIC_SYMBOL) is frame
    empty = pd.DataFrame()
    assert with_company_name_column(empty, ARABIC_SYMBOL) is empty


# --------------------------------------------------------------------------- #
# Export integrity
# --------------------------------------------------------------------------- #

def test_no_page_truncates_a_company_name():
    """Column width is presentation; the VALUE must always be complete."""

    import re

    for page in sorted(Path("dashboard").glob("*.py")):
        for number, line in enumerate(page.read_text(encoding="utf-8").splitlines(), 1):
            if any(token in line for token in
                   ("company_name", "NAME_COLUMN", "display_label")):
                assert not re.search(r"\[:\s*\d+\]|\.str\.slice|shorten\(", line), \
                    f"{page}:{number} truncates a company name"


def test_an_exported_csv_keeps_the_complete_company_name(tmp_path):
    longest = max(active_universe(), key=lambda record: len(record.company_name))
    frame = with_company_name_column(
        pd.DataFrame({ARABIC_SYMBOL: [longest.canonical_symbol]}), ARABIC_SYMBOL)

    path = tmp_path / "export.csv"
    frame.to_csv(path, index=False, encoding="utf-8")

    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    assert rows[0][ARABIC_NAME] == longest.company_name
    assert len(rows[0][ARABIC_NAME]) == len(longest.company_name)


def test_an_exported_json_keeps_the_complete_company_name():
    longest = max(active_universe(), key=lambda record: len(record.company_name))
    payload = json.loads(json.dumps({
        "symbol": longest.canonical_symbol,
        "company_name": company_name(longest.canonical_symbol),
    }, ensure_ascii=False))
    assert payload["company_name"] == longest.company_name


def test_report_exports_of_a_removed_symbol_stay_readable(tmp_path):
    frame = with_company_name_column(
        pd.DataFrame({"Ticker": ["COMI.CA", "ACRO.CA", "MISR.CA"]}), "Ticker")
    path = tmp_path / "historical.csv"
    frame.to_csv(path, index=False, encoding="utf-8")

    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    assert [row["Ticker"] for row in rows] == ["COMI.CA", "ACRO.CA", "MISR.CA"]
    assert rows[1][ARABIC_NAME] == "Acrow Misr"
    assert rows[2][ARABIC_NAME] == "Historical / Inactive Symbol"


def test_open_and_historical_paper_trades_display_a_name():
    path = Path("data/paper_trades.csv")
    if not path.is_file():
        pytest.skip("no recorded paper trades in this workspace")

    from core.universe import read_symbol_frame

    trades = read_symbol_frame(path)
    view = with_company_name_column(trades, "Symbol")
    assert ARABIC_NAME in view.columns
    # Recorded symbols are never rewritten…
    assert list(view["Symbol"]) == list(trades["Symbol"])
    # …and every row resolves to a non-empty display name.
    assert all(str(value).strip() for value in view[ARABIC_NAME])
