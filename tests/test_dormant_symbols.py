"""A listed symbol that does not trade is kept out of every scan.

EODHD's exchange list still names SIMO as active and prints it a zero-volume
placeholder bar every session, flat at 9.21; its last session with any volume
was 2014-03-03, which is also where Mubasher's record of it stops. It was
scanned and refused as 3,278 sessions stale on every run until 2026-09-24, and
it sat in the daily run's "without it" list every evening. It is registered in
``data/universe/dormant_symbols.csv`` and kept inactive, and a rebuild from
EODHD keeps it that way.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

import core.universe as universe
from core.universe import (
    DORMANT_FIELDNAMES,
    DORMANT_FILENAME,
    FIELDNAMES,
    UNIVERSE_SOURCE,
    UniverseUnavailable,
    active_symbols,
    read_dormant_registry,
)

REGISTRY = Path(UNIVERSE_SOURCE).with_name(DORMANT_FILENAME)


# --- the shipped registry ------------------------------------------------------

def test_simo_is_registered_with_its_last_traded_session():
    assert read_dormant_registry()["SIMO"] == "2014-03-03"


def test_every_dormant_symbol_is_inactive_and_out_of_the_scan():
    universe.clear_cache()
    dormant = read_dormant_registry()
    active = {symbol.split(".")[0].upper() for symbol in active_symbols()}
    assert dormant, "the registry should not be empty"
    assert not set(dormant) & active


def test_every_registered_row_carries_its_evidence():
    with REGISTRY.open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        assert row["evidence"].strip(), f"{row['symbol']} has no evidence"
        assert row["reviewed_on"].strip()


# --- the loader refuses to scan one --------------------------------------------

def _write(directory, rows, dormant=None):
    path = directory / "egx_universe.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(FIELDNAMES))
        writer.writeheader()
        for symbol, active in rows:
            writer.writerow({
                "canonical_symbol": symbol, "eodhd_symbol": f"{symbol}.EGX",
                "engine_symbol": f"{symbol}.CA", "company_name": f"{symbol} Company",
                "exchange": "EGX", "currency": "EGP", "instrument_type": "Common Stock",
                "isin": "", "rubix_symbol": "", "rubix_mapping_status": "",
                "is_active": "true" if active else "false",
                "source": "EODHD test", "source_as_of": "2026-09-24",
            })
    if dormant is not None:
        with (directory / DORMANT_FILENAME).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(DORMANT_FIELDNAMES))
            writer.writeheader()
            for symbol, last in dormant.items():
                writer.writerow({"symbol": symbol, "isin": "", "last_traded": last,
                                 "evidence": "test", "reviewed_on": "2026-09-24"})
    universe.clear_cache()
    return path


def test_an_active_dormant_symbol_is_refused(tmp_path):
    path = _write(tmp_path, [("SIMO", True), ("COMI", True)], {"SIMO": "2014-03-03"})
    with pytest.raises(UniverseUnavailable, match="dormant"):
        universe.load_universe(path)


def test_an_inactive_dormant_symbol_loads_and_is_not_scanned(tmp_path):
    path = _write(tmp_path, [("SIMO", False), ("COMI", True)], {"SIMO": "2014-03-03"})
    records = universe.load_universe(path)
    assert [r.canonical_symbol for r in records if r.is_active] == ["COMI"]


def test_no_registry_means_nothing_is_dormant(tmp_path):
    _write(tmp_path, [("COMI", True)])
    assert read_dormant_registry(tmp_path / "egx_universe.csv") == {}


def test_a_row_without_a_last_traded_date_is_refused(tmp_path):
    path = _write(tmp_path, [("COMI", True)], {"SIMO": ""})
    with pytest.raises(UniverseUnavailable, match="Malformed"):
        read_dormant_registry(path)


# --- a rebuild from EODHD keeps it out ------------------------------------------

def test_a_rebuild_keeps_a_dormant_symbol_inactive():
    """EODHD lists SIMO as active; the rebuild must not take its word for it."""
    from scripts import migrate_eodhd_241_universe as migrate

    rows = [{"Code": "SIMO", "Name": "Paper Middle East (Simo)", "Exchange": "EGX",
             "Currency": "EGP", "Type": "Common Stock", "Isin": "EGS36091C014"},
            {"Code": "COMI", "Name": "Commercial International Bank", "Exchange": "EGX",
             "Currency": "EGP", "Type": "Common Stock", "Isin": "EGS60121C018"}]
    records = migrate.build_universe_rows(
        rows, [], set(), set(), "2026-09-24T00:00:00+00:00",
        aliases={}, dormant={"SIMO": "2014-03-03"})
    by_symbol = {r["canonical_symbol"]: r for r in records}
    assert by_symbol["SIMO"]["is_active"] == "false"
    assert "dormant_since=2014-03-03" in by_symbol["SIMO"]["source"]
    assert by_symbol["COMI"]["is_active"] == "true"
