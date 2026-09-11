"""EODHD lists a ticker literally spelled ``NULL`` (Fitness Prime).

It is inactive — a registered alias of FTNS, whose name it carries — but its row
stays in the universe file and saved runs still name it. pandas' default NA
tokens include ``NULL``, ``NA``, ``N/A``, ``NaN`` and ``None``, so a plain
``pd.read_csv`` silently converts that ticker into NaN — and any ``dropna()``
downstream then deletes the row outright. These tests pin the literal string
through every production reader, the CSV/JSON round trip, the selectors and the
UI formatter.

No network call, no provider, no production database.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pandas as pd
import pytest

import core.universe as universe
from core.symbols import (
    SYMBOL_SOURCE,
    load_active_symbols,
    load_approved_symbol_options,
    load_symbols,
)
from core.universe import (
    UNIVERSE_SOURCE,
    active_symbols,
    company_name,
    display_label,
    is_active,
    lookup,
    read_symbol_frame,
)
from dashboard.formatting import (
    NAME_COLUMN,
    company_name as ui_company_name,
    symbol_option_label,
    symbol_ticker,
    with_company_name_column,
)

NULL = "NULL"
NULL_NAME = "Fitness Prime"


# --------------------------------------------------------------------------- #
# The authoritative universe
# --------------------------------------------------------------------------- #

def test_null_survives_in_the_authoritative_universe_as_an_alias():
    record = lookup(NULL)
    assert record is not None, "the EODHD ticker NULL was lost"
    assert record.canonical_symbol == NULL
    # Inactive because it is a registered alias, not because it was read as NaN.
    assert not record.is_active
    assert NULL not in active_symbols()
    assert NULL in universe.read_alias_registry()
    assert universe.live_symbol(NULL) == "FTNS"
    assert is_active("FTNS")


def test_null_carries_the_official_egx_suffix():
    record = lookup(NULL)
    assert record.eodhd_symbol == "NULL.EGX"
    assert record.engine_symbol == "NULL.CA"
    assert record.company_name == NULL_NAME


def test_company_name_lookup_for_null_succeeds():
    assert company_name(NULL) == NULL_NAME
    assert company_name("NULL.EGX") == NULL_NAME
    assert company_name("NULL.CA") == NULL_NAME
    assert ui_company_name(NULL) == NULL_NAME


def test_ui_formatting_displays_null_with_its_company_name():
    assert symbol_option_label(NULL) == "NULL — Fitness Prime"
    assert display_label(NULL) == "NULL — Fitness Prime"
    # The value handed back to strategy code is still the bare canonical ticker.
    assert symbol_ticker("NULL — Fitness Prime") == NULL


# --------------------------------------------------------------------------- #
# Readers
# --------------------------------------------------------------------------- #

def test_the_na_safe_reader_preserves_null_in_the_universe_file():
    frame = read_symbol_frame(UNIVERSE_SOURCE)
    rows = frame[frame["canonical_symbol"] == NULL]
    assert len(rows) == 1
    assert rows["canonical_symbol"].iloc[0] == NULL
    assert rows["eodhd_symbol"].iloc[0] == "NULL.EGX"
    assert int(frame["canonical_symbol"].isna().sum()) == 0


def test_a_plain_pandas_read_would_have_destroyed_null():
    """Documents the hazard this guard exists for — do not 'simplify' it away."""

    naive = pd.read_csv(UNIVERSE_SOURCE)
    assert naive["canonical_symbol"].isna().sum() == 1
    assert NULL not in set(naive["canonical_symbol"].dropna())


def test_the_na_safe_reader_still_infers_numeric_columns():
    frame = read_symbol_frame(io.StringIO("sym,qty,price\nNULL,3,1.5\nCOMI,,2.5\n"))
    assert list(frame["sym"]) == [NULL, "COMI"]
    assert frame["price"].dtype.kind == "f"
    assert frame["qty"].isna().iloc[1]        # an EMPTY cell is still missing


@pytest.mark.parametrize("loader", [load_symbols, load_active_symbols])
def test_symbol_loaders_leave_null_out_only_as_a_registered_alias(loader, tmp_path):
    symbols = loader(SYMBOL_SOURCE)
    assert "NULL.CA" not in symbols
    assert "FTNS.CA" in symbols
    assert len(symbols) == 230      # 241 EODHD codes less 11 registered aliases

    # The same file with NULL's row marked active loads the literal ticker.
    text = Path(UNIVERSE_SOURCE).read_text(encoding="utf-8-sig")
    alias_row = ",false,EODHD exchange-symbol-list/EGX?alias_of=FTNS,"
    assert text.count(alias_row) == 1
    flipped = tmp_path / "egx_universe.csv"
    flipped.write_text(text.replace(alias_row, ",true,EODHD exchange-symbol-list/EGX,"),
                       encoding="utf-8")
    assert "NULL.CA" in loader(flipped)


def test_approved_symbol_options_offer_fitness_prime_once():
    options = load_approved_symbol_options()
    assert not [option for option in options if option.ticker == NULL]
    match = [option for option in options if option.english_name == NULL_NAME]
    assert [option.ticker for option in match] == ["FTNS"]
    assert match[0].display_label == "FTNS — Fitness Prime"


def test_a_ticker_only_csv_containing_null_is_not_dropped(tmp_path):
    source = tmp_path / "symbols.csv"
    source.write_text("Ticker\nCOMI.CA\nNULL.CA\nSWDY.CA\n", encoding="utf-8")
    assert load_symbols(source) == ["COMI.CA", "NULL.CA", "SWDY.CA"]

    bare = tmp_path / "bare.csv"
    bare.write_text("Ticker\nCOMI\nNULL\nSWDY\n", encoding="utf-8")
    assert load_symbols(bare) == ["COMI", NULL, "SWDY"]


# --------------------------------------------------------------------------- #
# Round trip and exports
# --------------------------------------------------------------------------- #

def test_csv_write_read_round_trip_preserves_null(tmp_path):
    path = tmp_path / "universe.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(universe.FIELDNAMES))
        writer.writeheader()
        writer.writerow({
            "canonical_symbol": NULL, "eodhd_symbol": "NULL.EGX",
            "engine_symbol": "NULL.CA", "company_name": NULL_NAME,
            "exchange": "EGX", "currency": "EGP",
            "instrument_type": "Common Stock", "isin": "",
            "rubix_symbol": "", "rubix_mapping_status": "UNVERIFIED_NO_FEED_OBSERVATION",
            "is_active": "true", "source": "EODHD test", "source_as_of": "now",
        })

    universe.clear_cache()
    try:
        records = universe.load_universe(path)
    finally:
        universe.clear_cache()
    assert [record.canonical_symbol for record in records] == [NULL]
    assert records[0].company_name == NULL_NAME
    assert records[0].display_label == "NULL — Fitness Prime"

    assert read_symbol_frame(path)["canonical_symbol"].iloc[0] == NULL


def test_pandas_round_trip_through_a_report_export_preserves_null(tmp_path):
    path = tmp_path / "scan_results.csv"
    pd.DataFrame({"Ticker": ["COMI", NULL], "Score": [90, 80]}).to_csv(path, index=False)

    assert "NULL" in path.read_text(encoding="utf-8")
    restored = read_symbol_frame(path)
    assert list(restored["Ticker"]) == ["COMI", NULL]


def test_json_export_emits_the_string_null_not_a_json_null():
    payload = json.dumps({"symbol": NULL, "name": company_name(NULL)})
    assert '"symbol": "NULL"' in payload
    assert '"symbol": null' not in payload
    assert json.loads(payload)["symbol"] == NULL


def test_the_saved_eodhd_snapshot_stored_null_as_a_string():
    snapshots = sorted(Path(universe.SNAPSHOT_DIR).glob("eodhd_egx_active_*.json"))
    assert snapshots
    raw = snapshots[-1].read_text(encoding="utf-8")
    assert '"Code": "NULL"' in raw
    assert '"Code": null' not in raw
    rows = json.loads(raw)["rows"]
    assert [row for row in rows if row["Code"] == NULL][0]["Name"] == NULL_NAME


def test_migration_audit_csvs_kept_null_as_a_literal_ticker():
    base = Path("reports/audits/universe")
    for name in ("eodhd_241_snapshot.csv", "universe_added.csv",
                 "universe_mapping_changes.csv"):
        rows = list(csv.DictReader(
            (base / name).read_text(encoding="utf-8-sig").splitlines()))
        assert any(row["canonical_symbol"] == NULL for row in rows), name


# --------------------------------------------------------------------------- #
# Table display
# --------------------------------------------------------------------------- #

def test_a_table_shows_null_with_its_company_name():
    frame = pd.DataFrame({"Ticker": ["COMI", NULL], "Score": [90, 80]})
    view = with_company_name_column(frame, "Ticker")
    assert list(view["Ticker"]) == ["COMI", NULL]
    assert view.loc[1, NAME_COLUMN] == NULL_NAME


def test_a_genuinely_missing_symbol_is_not_stringified_into_a_fake_ticker():
    """NaN is missing; the STRING 'NULL' is a security. They must not converge."""

    for missing in (None, float("nan"), pd.NA, pd.NaT):
        assert universe.canonical(missing) == ""
        assert symbol_ticker(missing) == ""
        assert ui_company_name(missing) == "—"
    assert universe.canonical(NULL) == NULL


def test_selectors_and_the_watch_key_list_still_see_null():
    """A ticker literally named NULL must survive every string path.

    The subscription-plan builder this used to go through went with the
    collector on 2026-09-10; the rule is the universe's, so it is asserted
    where it lives. NULL is not a verified feed symbol, so no key is emitted
    for it -- and that has to be because it was never verified, not because
    something read the name as a null.
    """

    from core.universe import rubix_subscription_symbols

    keys = rubix_subscription_symbols()
    assert "CASE~NULL" not in keys
    assert all(isinstance(key, str) and key for key in keys)

