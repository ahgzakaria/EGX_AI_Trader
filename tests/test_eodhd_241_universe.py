"""Contract tests for the EODHD 241 active-ticker operational universe.

These pin the migration: the authoritative snapshot's shape, the display
contract (``TICKER — Full Company Name`` with a canonical-ticker value), the
separation of ticker and company-name table columns, the removal of the retired
265-symbol list from every runtime fallback chain, and the read-only survival of
historical records.

No network call, no provider, no strategy calculation, no production database.
"""

from __future__ import annotations

import csv
import inspect
from pathlib import Path

import pandas as pd
import pytest

import core.universe as universe
from core.symbols import (
    RETIRED_SYMBOL_SOURCES,
    SYMBOL_SOURCE,
    load_active_symbols,
    load_approved_symbol_options,
    load_symbols,
)
from core.universe import (
    ARCHIVED_LEGACY_SOURCE,
    EODHD_SUFFIX,
    RUBIX_VERIFIED,
    UNIVERSE_SOURCE,
    UniverseUnavailable,
    active_symbols,
    active_universe,
    display_label,
    eligible_for_new_entry,
    inactive_universe,
    is_active,
    lookup,
    rubix_subscription_symbols,
    universe_provenance,
)
from dashboard.formatting import (
    NAME_COLUMN,
    SYMBOL_COLUMN,
    company_name,
    symbol_option_label,
    with_company_name_column,
)

#: EODHD lists 241 codes; 5 of them duplicate a live ticker and are registered
#: as aliases in data/universe/symbol_aliases.csv.
EXPECTED_ACTIVE = 236


# --------------------------------------------------------------------------- #
# Authoritative snapshot
# --------------------------------------------------------------------------- #

def test_migration_snapshot_yields_exactly_the_expected_active_symbols():
    assert len(active_universe()) == EXPECTED_ACTIVE
    assert len(active_symbols()) == EXPECTED_ACTIVE
    assert universe_provenance()["active_count"] == EXPECTED_ACTIVE
    assert universe_provenance()["source"].startswith("EODHD ")


def test_every_active_symbol_carries_a_full_company_name():
    missing = [r.canonical_symbol for r in active_universe() if not r.company_name.strip()]
    assert missing == []
    # A ticker-only universe is exactly what this migration removed.
    assert all(record.company_name != record.canonical_symbol
               for record in active_universe())


def test_canonical_and_eodhd_symbols_are_unique():
    records = active_universe()
    canonical = [record.canonical_symbol for record in records]
    eodhd = [record.eodhd_symbol for record in records]
    assert len(set(canonical)) == len(canonical)
    assert len(set(eodhd)) == len(eodhd)
    # No duplicate survives once the provider suffix is removed either.
    stripped = [symbol[: -len(EODHD_SUFFIX)] for symbol in eodhd]
    assert len(set(stripped)) == len(stripped)


def test_active_symbols_use_the_official_egx_suffix_not_ca():
    for record in active_universe():
        assert record.eodhd_symbol == f"{record.canonical_symbol}{EODHD_SUFFIX}"
        assert not record.eodhd_symbol.endswith(".CA")
        assert record.exchange == "EGX"
        assert record.currency
        assert record.instrument_type


def test_universe_metadata_is_retained_per_record():
    comi = lookup("COMI")
    assert comi is not None
    assert comi.company_name
    assert comi.exchange == "EGX"
    assert comi.currency == "EGP"
    assert comi.instrument_type
    assert comi.isin                       # ISIN retained when EODHD supplies one


def test_a_raw_timestamped_snapshot_was_captured_for_audit():
    snapshots = sorted(Path(universe.SNAPSHOT_DIR).glob("eodhd_egx_active_*.json"))
    assert snapshots, "no timestamped raw EODHD snapshot was saved"


# --------------------------------------------------------------------------- #
# Display contract
# --------------------------------------------------------------------------- #

def test_ui_formatter_produces_ticker_then_full_company_name():
    assert symbol_option_label("COMI") == "COMI — Commercial International Bank-Egypt (CIB)"
    assert symbol_option_label("GRCA") == "GRCA — Grand Investment Capital"
    assert symbol_option_label("GOUR") == "GOUR — Gourmet Egypt.Com Foods"
    # Every accepted spelling resolves to the same label.
    assert symbol_option_label("COMI.CA") == symbol_option_label("COMI")
    assert symbol_option_label("COMI.EGX") == symbol_option_label("COMI")
    assert symbol_option_label("CASE~COMI") == symbol_option_label("COMI")


def test_dropdown_values_remain_canonical_tickers():
    options = load_approved_symbol_options()
    assert len(options) == EXPECTED_ACTIVE
    comi = next(option for option in options if option.ticker == "COMI")
    # The LABEL carries the name; the VALUE strategy code receives does not.
    assert comi.display_label == "COMI — Commercial International Bank-Egypt (CIB)"
    assert comi.ticker == "COMI"
    assert "—" not in comi.ticker
    assert comi.eodhd_symbol == "COMI.EGX"


def test_tables_expose_ticker_and_company_name_as_separate_columns():
    frame = pd.DataFrame({SYMBOL_COLUMN: ["COMI", "GRCA"], "Score": [90, 80]})
    view = with_company_name_column(frame, SYMBOL_COLUMN)

    assert list(view.columns) == [SYMBOL_COLUMN, NAME_COLUMN, "Score"]
    # The ticker column is untouched, so filtering and export still work on it.
    assert list(view[SYMBOL_COLUMN]) == ["COMI", "GRCA"]
    assert view.loc[0, NAME_COLUMN] == "Commercial International Bank-Egypt (CIB)"


def test_a_historical_ticker_only_record_still_displays_a_name():
    # ACRO left the exchange; its archived EODHD name is still resolvable.
    assert not is_active("ACRO")
    assert display_label("ACRO") == "ACRO — Acrow Misr"
    assert company_name("ACRO.CA") == "Acrow Misr"


def test_a_ticker_with_no_archived_name_renders_the_inactive_label():
    assert display_label("MISR") == "MISR — Historical / Inactive Symbol"
    assert display_label("NEVER_SEEN") == "NEVER_SEEN — Historical / Inactive Symbol"


# --------------------------------------------------------------------------- #
# The old 265 list is not an operational fallback
# --------------------------------------------------------------------------- #

def test_the_retired_265_list_is_not_present_in_the_runtime_tree():
    assert not Path("data/symbols.csv").exists()
    archive = Path(ARCHIVED_LEGACY_SOURCE)
    assert archive.is_file(), "the old universe must stay archived for audit"
    rows = list(csv.DictReader(archive.read_text(encoding="utf-8-sig").splitlines()))
    assert len(rows) == 265


@pytest.mark.parametrize("retired", sorted(RETIRED_SYMBOL_SOURCES))
def test_loading_the_retired_universe_raises_instead_of_falling_back(retired):
    for loader in (load_symbols, load_active_symbols, load_approved_symbol_options):
        with pytest.raises(UniverseUnavailable):
            loader(retired)


def test_a_missing_authoritative_universe_is_an_explicit_error(tmp_path):
    universe.clear_cache()
    with pytest.raises(UniverseUnavailable):
        universe.load_universe(tmp_path / "absent.csv")


def test_a_universe_missing_a_company_name_is_rejected(tmp_path):
    path = tmp_path / "broken.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(universe.FIELDNAMES))
        writer.writeheader()
        writer.writerow({"canonical_symbol": "COMI", "eodhd_symbol": "COMI.EGX",
                         "engine_symbol": "COMI.CA", "company_name": "",
                         "exchange": "EGX", "currency": "EGP",
                         "instrument_type": "Common Stock", "isin": "",
                         "rubix_symbol": "", "rubix_mapping_status": "",
                         "is_active": "true", "source": "x", "source_as_of": "x"})
    universe.clear_cache()
    with pytest.raises(UniverseUnavailable):
        universe.load_universe(path)


def test_the_default_symbol_source_is_the_authoritative_universe():
    assert SYMBOL_SOURCE == UNIVERSE_SOURCE
    assert len(load_symbols()) == EXPECTED_ACTIVE
    assert len(load_active_symbols()) == EXPECTED_ACTIVE


# --------------------------------------------------------------------------- #
# Selectors
# --------------------------------------------------------------------------- #

def test_range_bound_selector_receives_only_active_eodhd_symbols():
    from scalping_expected_range.frozen_watchlist import validated_eodhd_symbols

    symbols = validated_eodhd_symbols()
    assert symbols == tuple(sorted(active_symbols()))
    assert len(symbols) == EXPECTED_ACTIVE
    assert not set(symbols) & {r.canonical_symbol for r in inactive_universe()}


def test_uptrend_pullback_selector_receives_only_active_eodhd_symbols():
    from scalping_uptrend_pullback.frozen_watchlist import validated_eodhd_symbols

    symbols = validated_eodhd_symbols()
    assert symbols == tuple(sorted(active_symbols()))
    assert len(symbols) == EXPECTED_ACTIVE


def test_removed_symbols_can_never_receive_a_new_entry():
    removed = [record.canonical_symbol for record in inactive_universe()]
    assert removed, "the migration removed symbols; they must be represented"
    assert eligible_for_new_entry(removed) == ()
    for symbol in removed:
        assert not is_active(symbol)
        assert not is_active(f"{symbol}.CA")
    mixed = ["COMI", removed[0], "SWDY"]
    assert eligible_for_new_entry(mixed) == ("COMI", "SWDY")


# --------------------------------------------------------------------------- #
# Rubix mapping and exit monitoring
# --------------------------------------------------------------------------- #

def test_rubix_mappings_are_explicit_and_never_suffix_guessed():
    for record in active_universe():
        if record.rubix_symbol:
            assert record.rubix_mapping_status == RUBIX_VERIFIED
            assert record.rubix_symbol == f"CASE~{record.canonical_symbol}"
        else:
            # No verified observation ⇒ NO fabricated key.
            assert record.rubix_mapping_status != RUBIX_VERIFIED

    # The plan builder that consumed these went with the collector on
    # 2026-09-10. The rule it enforced is a property of the universe file, so
    # it is asserted against the file directly: every key that exists is
    # verified and derived, and an unverified symbol contributes none.
    keys = rubix_subscription_symbols()
    assert all(key.startswith("CASE~") for key in keys)
    unverified = {record.canonical_symbol for record in active_universe()
                  if not record.has_verified_rubix_mapping}
    assert not any(f"CASE~{symbol}" in keys for symbol in unverified)


def test_a_removed_symbol_with_an_open_position_stays_monitorable():
    removed = next(record for record in inactive_universe()
                   if record.has_verified_rubix_mapping)

    baseline = rubix_subscription_symbols()
    assert removed.rubix_symbol not in baseline

    with_exit = rubix_subscription_symbols([f"{removed.canonical_symbol}.CA"])
    assert removed.rubix_symbol in with_exit
    assert len(with_exit) == len(baseline) + 1

    # Monitoring an exit never makes the symbol eligible for a NEW entry.
    assert eligible_for_new_entry([removed.canonical_symbol]) == ()


# --------------------------------------------------------------------------- #
# History is preserved
# --------------------------------------------------------------------------- #

def test_saved_historical_runs_remain_readable_and_are_enriched_on_display():
    frame = pd.DataFrame({"Ticker": ["COMI.CA", "ACRO.CA", "MISR.CA"]})
    view = with_company_name_column(frame, "Ticker")

    # The recorded values are never rewritten…
    assert list(view["Ticker"]) == ["COMI.CA", "ACRO.CA", "MISR.CA"]
    # …only a display column is added.
    assert list(view[NAME_COLUMN]) == [
        "Commercial International Bank-Egypt (CIB)",
        "Acrow Misr",
        "Historical / Inactive Symbol",
    ]


def test_recorded_paper_trades_are_untouched_by_the_migration():
    path = Path("data/paper_trades.csv")
    if not path.is_file():
        pytest.skip("no recorded paper trades in this workspace")
    frame = pd.read_csv(path)
    assert not frame.empty
    # Every recorded symbol still renders a label, active or not.
    for symbol in frame["Symbol"].astype(str):
        assert display_label(symbol).startswith(universe.canonical(symbol))


# --------------------------------------------------------------------------- #
# No Yahoo route into the universe
# --------------------------------------------------------------------------- #

def test_no_yahoo_source_participates_in_universe_membership():
    modules = (universe, __import__("core.symbols", fromlist=["x"]))
    for module in modules:
        source = inspect.getsource(module).lower()
        assert "yahoo" not in source or "no yahoo" in source
        assert "yfinance" not in source

    text = Path(UNIVERSE_SOURCE).read_text(encoding="utf-8-sig").lower()
    assert "yahoo" not in text
    for record in active_universe():
        assert record.source.startswith("EODHD ")
