"""The four symbol identifiers must stay separate and non-substitutable.

``engine_symbol`` (``COMI.CA``) is a LEGACY INTERNAL alias retained only so that
existing on-disk caches, archived runs and recorded paper trades stay readable.
It is not the official ticker, it does not decide universe membership, and it is
never used to build an EODHD request or a Rubix subscription key.

Also pins the fail-closed contract: a universe that cannot be trusted raises,
and never degrades to the retired list, the routing manifest, Yahoo or a
hard-coded basket.
"""

from __future__ import annotations

import csv
import inspect
from pathlib import Path

import pytest

import core.universe as universe
from core.universe import (
    ARCHIVED_LEGACY_SOURCE,
    ENGINE_SUFFIX,
    EODHD_SUFFIX,
    UNIVERSE_SOURCE,
    UniverseUnavailable,
    active_universe,
    lookup,
)
from providers.eodhd_historical_provider import EODHDHistoricalProvider  # noqa: F401
from providers.symbol_mapping import (
    to_egx_code,
    to_eodhd_symbol,
    to_rubix_subscription_symbol,
)


# --------------------------------------------------------------------------- #
# The four identifiers
# --------------------------------------------------------------------------- #

def test_the_four_identifiers_are_distinct_and_explicit():
    comi = lookup("COMI")
    assert comi.canonical_symbol == "COMI"
    assert comi.eodhd_symbol == "COMI.EGX"
    assert comi.engine_symbol == "COMI.CA"
    assert comi.rubix_symbol == "CASE~COMI"
    assert comi.has_verified_rubix_mapping


def test_ca_is_never_presented_as_the_official_eodhd_ticker():
    for record in active_universe():
        assert record.eodhd_symbol.endswith(EODHD_SUFFIX)
        assert ENGINE_SUFFIX not in record.eodhd_symbol
        assert record.eodhd_symbol == f"{record.canonical_symbol}{EODHD_SUFFIX}"


def test_eodhd_requests_use_only_the_egx_suffix():
    for spelling in ("COMI", "COMI.CA", "COMI.EGX"):
        assert to_eodhd_symbol(spelling) == "COMI.EGX"
    # The historical provider strips any inbound suffix before calling EODHD.
    source = inspect.getsource(EODHDHistoricalProvider)
    assert ".CA" not in source.replace("# strip any .CA / .EGX suffix", "")


def test_ca_does_not_determine_universe_membership():
    """Membership is the EODHD active list; the .CA alias is derived from it."""

    for record in active_universe():
        assert record.engine_symbol == f"{record.canonical_symbol}{ENGINE_SUFFIX}"
    # A retired symbol still HAS a .CA alias but is not a member.
    retired = [r for r in universe.inactive_universe()][0]
    assert retired.engine_symbol.endswith(ENGINE_SUFFIX)
    assert not universe.is_active(retired.engine_symbol)


def test_ca_is_not_used_to_construct_rubix_symbols():
    for record in active_universe():
        if record.rubix_symbol:
            assert ENGINE_SUFFIX not in record.rubix_symbol
            assert record.rubix_symbol == f"CASE~{record.canonical_symbol}"
    # The universe value is authoritative; the generic mapper is only a helper.
    assert to_rubix_subscription_symbol("COMI.CA") == "CASE~COMI"
    assert to_egx_code("COMI.CA") == "COMI"


def test_unverified_symbols_get_no_rubix_symbol_at_all():
    unmapped = [r for r in active_universe() if not r.has_verified_rubix_mapping]
    assert unmapped
    for record in unmapped:
        assert record.rubix_symbol == ""


def test_the_ui_shows_the_canonical_ticker_not_the_engine_alias():
    from core.symbols import load_approved_symbol_options
    from dashboard.formatting import symbol_option_label

    options = load_approved_symbol_options()
    comi = next(option for option in options if option.ticker == "COMI")
    assert comi.ticker == "COMI"                       # the returned VALUE
    assert ENGINE_SUFFIX not in comi.display_label     # the shown LABEL
    assert symbol_option_label("COMI") == comi.display_label
    # The legacy alias is still carried for cache/history compatibility.
    assert comi.source_symbol == "COMI.CA"


def test_historical_cache_keys_written_as_ca_remain_resolvable():
    """A record archived under COMI.CA still resolves to its universe entry."""

    for spelling in ("COMI.CA", "comi.ca", " COMI.CA "):
        record = lookup(spelling)
        assert record is not None
        assert record.canonical_symbol == "COMI"
    assert universe.display_label("COMI.CA") == universe.display_label("COMI")


# --------------------------------------------------------------------------- #
# Fail-closed
# --------------------------------------------------------------------------- #

def _write(path, rows, fieldnames=None):
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames or universe.FIELDNAMES))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _row(**overrides):
    row = {
        "canonical_symbol": "COMI", "eodhd_symbol": "COMI.EGX",
        "engine_symbol": "COMI.CA", "company_name": "Commercial International Bank",
        "exchange": "EGX", "currency": "EGP", "instrument_type": "Common Stock",
        "isin": "EGS60121C018", "rubix_symbol": "CASE~COMI",
        "rubix_mapping_status": "VERIFIED_FEED_OBSERVED", "is_active": "true",
        "source": "EODHD exchange-symbol-list/EGX", "source_as_of": "now",
    }
    row.update(overrides)
    return row


@pytest.fixture(autouse=True)
def _clear_universe_cache():
    universe.clear_cache()
    yield
    universe.clear_cache()


def test_a_missing_universe_raises(tmp_path):
    with pytest.raises(UniverseUnavailable, match="unreadable"):
        universe.load_universe(tmp_path / "gone.csv")


def test_an_empty_universe_raises(tmp_path):
    path = _write(tmp_path / "empty.csv", [])
    with pytest.raises(UniverseUnavailable, match="no rows"):
        universe.load_universe(path)


def test_a_malformed_universe_raises(tmp_path):
    path = tmp_path / "malformed.csv"
    path.write_text("not,the,right,columns\n1,2,3,4\n", encoding="utf-8")
    with pytest.raises(UniverseUnavailable, match="missing columns"):
        universe.load_universe(path)


def test_a_universe_with_no_active_row_raises(tmp_path):
    path = _write(tmp_path / "inactive.csv", [_row(is_active="false")])
    with pytest.raises(UniverseUnavailable, match="no active symbol"):
        universe.load_universe(path)


def test_duplicate_canonical_symbols_raise(tmp_path):
    path = _write(tmp_path / "dupe.csv", [_row(), _row()])
    with pytest.raises(UniverseUnavailable, match="Duplicate canonical symbol"):
        universe.load_universe(path)


def test_duplicate_eodhd_symbols_raise(tmp_path):
    path = _write(tmp_path / "dupe2.csv",
                  [_row(), _row(canonical_symbol="SWDY", engine_symbol="SWDY.CA")])
    with pytest.raises(UniverseUnavailable, match="Duplicate EODHD symbol"):
        universe.load_universe(path)


def test_a_blank_company_name_on_an_active_row_raises(tmp_path):
    path = _write(tmp_path / "noname.csv", [_row(company_name="  ")])
    with pytest.raises(UniverseUnavailable, match="no company name"):
        universe.load_universe(path)


def test_a_blank_canonical_symbol_raises(tmp_path):
    path = _write(tmp_path / "noticker.csv", [_row(canonical_symbol="")])
    with pytest.raises(UniverseUnavailable, match="Blank canonical_symbol"):
        universe.load_universe(path)


def test_an_active_row_without_the_egx_suffix_raises(tmp_path):
    path = _write(tmp_path / "badsuffix.csv", [_row(eodhd_symbol="COMI.CA")])
    with pytest.raises(UniverseUnavailable, match="official '.EGX' suffix"):
        universe.load_universe(path)


def test_an_active_row_outside_egx_raises(tmp_path):
    path = _write(tmp_path / "badexchange.csv", [_row(exchange="NYSE")])
    with pytest.raises(UniverseUnavailable, match="outside EGX"):
        universe.load_universe(path)


def test_no_loader_silently_substitutes_another_source():
    """Every degraded path raises; none returns a different universe."""

    source = inspect.getsource(universe)
    # The archived list is named only as a constant and in prose, never loaded.
    assert "legacy_symbols_265.csv" in source            # the archive constant
    assert "read_symbol_frame(ARCHIVED" not in source
    assert "historical_symbol_routing" not in source
    assert "yfinance" not in source

    for module_name in ("scalping_expected_range.frozen_watchlist",
                        "scalping_uptrend_pullback.frozen_watchlist"):
        module = __import__(module_name, fromlist=["validated_eodhd_symbols"])
        selector = inspect.getsource(module.validated_eodhd_symbols)
        # Membership comes from core.universe, not the routing manifest.
        assert "active_symbols()" in selector
        assert "manifest_path" not in selector.split('"""')[2]


def test_the_archived_legacy_list_is_present_but_inert():
    archive = Path(ARCHIVED_LEGACY_SOURCE)
    assert archive.is_file()
    assert not Path("data/symbols.csv").exists()
    # Nothing in the loaded universe was sourced from it.
    for record in active_universe():
        assert record.source.startswith("EODHD ")


def test_saved_historical_runs_can_still_read_their_own_recorded_universe(tmp_path):
    """A run archived its own symbol list; replay must still read that file."""

    archived = tmp_path / "symbols_used.csv"
    archived.write_text("Symbol\nCOMI.CA\nACRO.CA\nNULL.CA\n", encoding="utf-8")

    from core.universe import read_symbol_frame

    recorded = read_symbol_frame(archived)["Symbol"].tolist()
    assert recorded == ["COMI.CA", "ACRO.CA", "NULL.CA"]
    # Including a symbol the migration removed, which stays readable…
    assert universe.display_label("ACRO.CA") == "ACRO — Acrow Misr"
    # …without becoming eligible for a new entry. NULL reads back as the literal
    # ticker and is ineligible only because it is a registered alias of FTNS.
    assert universe.display_label("NULL.CA") == "NULL — Fitness Prime"
    assert universe.eligible_for_new_entry(recorded) == ("COMI.CA",)


def test_the_universe_file_shipped_is_exactly_the_activated_snapshot():
    """The live file must still match the snapshot the migration validated,
    less the codes registered as aliases of a live ticker and the listed
    symbols registered as dormant."""

    import json

    snapshots = sorted(Path(universe.SNAPSHOT_DIR).glob("eodhd_egx_active_*.json"))
    payload = json.loads(snapshots[-1].read_text(encoding="utf-8"))
    snapshot_codes = {row["Code"] for row in payload["rows"]}
    aliases = set(universe.read_alias_registry(UNIVERSE_SOURCE))
    dormant = set(universe.read_dormant_registry(UNIVERSE_SOURCE))

    active = {record.canonical_symbol for record in universe.active_universe(UNIVERSE_SOURCE)}
    assert aliases <= snapshot_codes
    assert dormant <= snapshot_codes
    assert active == snapshot_codes - aliases - dormant
    assert payload["count"] == 241
    assert len(active) == 241 - len(aliases) - len(dormant)
