"""One instrument, one active ticker.

EODHD lists some EGX companies under two codes: the live ticker and a second
code — a copy of its series (``AUTO`` for ``GBCO``) or the ticker the company
traded under before a rename (``ARVA`` for ``AMII``). Both were active, so every
universe scan counted the company twice — on 2026-09-10 the forward-test book
recorded a signal for AUTO.CA and GBCO.CA on each of the last 11 sessions. The
second codes are registered in ``data/universe/symbol_aliases.csv`` and kept
inactive.

The last test compares the recent EODHD series of every active pair. It reads
the local EODHD cache only — ``max_live_calls=0`` refuses any network call — and
skips where that cache is absent, as in a fresh worktree. Set
``EODHD_CACHE_DIR`` to run it against a populated cache elsewhere.
"""

from __future__ import annotations

import csv
import itertools
import os
from pathlib import Path

import pytest

import core.universe as universe
from core.universe import (
    ALIAS_FIELDNAMES,
    ALIAS_FILENAME,
    FIELDNAMES,
    UNIVERSE_SOURCE,
    UniverseUnavailable,
    active_symbols,
    is_active,
    live_symbol,
    lookup,
    read_alias_registry,
)

REGISTRY = Path(UNIVERSE_SOURCE).with_name(ALIAS_FILENAME)

#: Bars compared per symbol: its most recent ones.
TWIN_WINDOW = 60
#: Common traded sessions a pair needs before it can be judged.
TWIN_MIN_COMMON = 10
#: Share of common traded sessions with the same close and the same volume.
#: Measured on the 2026-09-11 cache over all 241 EODHD codes, exactly seven
#: pairs match on 100% of their common sessions and all seven are registered:
#: the five copies (57-60 sessions), AMII/ARVA (34) and SEIG/SEIGA (15). The
#: closest other pairs are HBCO/NULL at 16/29 (0.55) and FTNS/NULL at 13/29
#: (0.45); no further pair matches on a single session. EDBM, PIOH and SRWA
#: have no traded bar in the window, so they pair with nothing.
TWIN_MIN_SHARE = 0.9
#: Two feeds of one instrument can disagree by a few shares on a large print:
#: ORMT against OIH by at most 8 shares on prints of up to 192,407,552.
VOLUME_TOLERANCE = 1e-5

#: Price twins that are NOT registered aliases, because the evidence does not
#: settle which ticker is the copy. The test fails if one stops being a twin, so
#: an entry is removed once it is resolved rather than left to go stale. Empty
#: since 2026-09-11, when AMII/ARVA and SEIG/SEIGA were resolved and registered.
UNRESOLVED_TWINS = {}


# --------------------------------------------------------------------------- #
# The shipped registry
# --------------------------------------------------------------------------- #

def test_every_registered_alias_is_inactive_and_resolves_to_an_active_ticker():
    aliases = read_alias_registry()
    assert aliases, "the alias registry is empty"
    for alias, live in aliases.items():
        assert lookup(alias) is not None, alias
        assert not is_active(alias)
        assert is_active(live)
        assert live_symbol(alias) == live
        assert live_symbol(f"{alias}.CA") == live
    assert not set(aliases) & set(active_symbols())


def test_gb_corp_is_scanned_once_under_its_live_ticker():
    symbols = active_symbols()
    assert "GBCO" in symbols
    assert "AUTO" not in symbols
    assert live_symbol("AUTO.EGX") == "GBCO"
    assert live_symbol("GBCO") == "GBCO"


def test_a_renamed_company_is_scanned_once_under_its_current_ticker():
    symbols = active_symbols()
    for retired, current in (("ARVA", "AMII"), ("EDBM", "CRST"), ("PIOH", "ASPI"),
                             ("SRWA", "CNFN")):
        assert current in symbols and retired not in symbols
        assert lookup(retired).isin == lookup(current).isin
        assert live_symbol(retired) == current


def test_no_rubix_key_is_guessed_for_a_renamed_ticker():
    from core.universe import rubix_subscription_symbols

    keys = rubix_subscription_symbols()
    # The feed stopped pricing ARVA on 2026-07-28 and has never carried AMII.
    assert "CASE~ARVA" not in keys
    assert "CASE~AMII" not in keys
    # An open ARVA position would still be monitored under the key it traded on.
    assert "CASE~ARVA" in rubix_subscription_symbols(open_position_symbols=("ARVA.CA",))


def test_each_registry_row_carries_the_alias_isin_and_its_evidence():
    rows = list(csv.DictReader(REGISTRY.read_text(encoding="utf-8-sig").splitlines()))
    assert rows
    for row in rows:
        assert lookup(row["alias_symbol"]).isin == row["isin"]
        assert row["evidence"].strip()
        assert row["reviewed_on"].strip()


def test_the_active_universe_is_the_eodhd_list_less_its_aliases():
    from scripts.migrate_eodhd_241_universe import latest_snapshot

    snapshot = latest_snapshot("active")
    if not snapshot:
        pytest.skip("no saved EODHD snapshot in this checkout")
    listed = {universe.canonical(row.get("Code")) for row in snapshot["rows"]}
    assert set(active_symbols()) == listed - set(read_alias_registry())


def test_a_rebuild_from_eodhd_keeps_an_alias_inactive():
    from scripts.migrate_eodhd_241_universe import build_universe_rows

    rows = [
        {"Code": "AUTO", "Name": "GB Corp", "Exchange": "EGX", "Currency": "EGP",
         "Type": "Common Stock", "Isin": "EGS673T1C012"},
        {"Code": "GBCO", "Name": "GB Corp.", "Exchange": "EGX", "Currency": "EGP",
         "Type": "Common Stock", "Isin": None},
    ]
    records = build_universe_rows(rows, [], (), frozenset({"GBCO"}), "2026-09-11",
                                  aliases={"AUTO": "GBCO"})
    by_symbol = {record["canonical_symbol"]: record for record in records}
    assert by_symbol["GBCO"]["is_active"] == "true"
    assert by_symbol["AUTO"]["is_active"] == "false"
    assert by_symbol["AUTO"]["source"].endswith("?alias_of=GBCO")


# --------------------------------------------------------------------------- #
# Loader enforcement
# --------------------------------------------------------------------------- #

def _write_universe(directory, rows, aliases=None):
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
                "source": "EODHD test", "source_as_of": "2026-09-11",
            })
    if aliases is not None:
        with (directory / ALIAS_FILENAME).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(ALIAS_FIELDNAMES))
            writer.writeheader()
            for alias, live in aliases.items():
                writer.writerow({"alias_symbol": alias, "live_symbol": live, "isin": "",
                                 "evidence": "test", "reviewed_on": "2026-09-11"})
    universe.clear_cache()
    return path


def test_an_active_alias_is_refused(tmp_path):
    path = _write_universe(tmp_path, [("AUTO", True), ("GBCO", True)], {"AUTO": "GBCO"})
    with pytest.raises(UniverseUnavailable, match="AUTO"):
        universe.load_universe(path)


def test_an_alias_of_a_ticker_that_is_not_active_is_refused(tmp_path):
    path = _write_universe(tmp_path, [("AUTO", False), ("GBCO", False), ("COMI", True)],
                           {"AUTO": "GBCO"})
    with pytest.raises(UniverseUnavailable, match="GBCO"):
        universe.load_universe(path)


def test_a_chained_alias_is_refused(tmp_path):
    path = _write_universe(tmp_path, [("AAA", False), ("BBB", False), ("CCC", True)],
                           {"AAA": "BBB", "BBB": "CCC"})
    with pytest.raises(UniverseUnavailable, match="BBB"):
        universe.load_universe(path)


def test_an_inactive_alias_loads_and_resolves(tmp_path):
    path = _write_universe(tmp_path, [("AUTO", False), ("GBCO", True)], {"AUTO": "GBCO"})
    assert universe.active_symbols(path) == ("GBCO",)
    assert universe.live_symbol("AUTO.CA", path) == "GBCO"
    assert universe.lookup("AUTO", path) is not None      # history still resolves it


def test_a_universe_without_a_registry_has_no_aliases(tmp_path):
    path = _write_universe(tmp_path, [("COMI", True)])
    assert universe.read_alias_registry(path) == {}
    assert universe.live_symbol("COMI", path) == "COMI"


# --------------------------------------------------------------------------- #
# Price twins across the active universe
# --------------------------------------------------------------------------- #

def _recent_traded_bars(client, symbol):
    """``{date: (close, volume)}`` over the last bars, traded sessions only."""

    from providers.eodhd_client import EODHDError

    try:
        raw = client.eod(f"{symbol}.EGX", order="a", cache_ttl_seconds=None)
    except EODHDError:
        return None
    rows = [row for row in raw or () if isinstance(row, dict) and row.get("close")]
    return {row["date"]: (float(row["close"]), float(row.get("volume") or 0))
            for row in rows[-TWIN_WINDOW:] if float(row.get("volume") or 0) > 0}


def _twin(left, right):
    """``(same, common)`` when two series are near-identical, else ``None``."""

    common = set(left) & set(right)
    if len(common) < TWIN_MIN_COMMON:
        return None
    same = 0
    for date in common:
        (close_l, volume_l), (close_r, volume_r) = left[date], right[date]
        if (close_l == close_r
                and abs(volume_l - volume_r) <= VOLUME_TOLERANCE * max(volume_l, volume_r)):
            same += 1
    return (same, len(common)) if same / len(common) >= TWIN_MIN_SHARE else None


def test_twin_detection_tolerates_share_rounding_but_not_a_different_close():
    left = {f"2026-09-{day:02d}": (30.0 + day, 1_000_000.0 * day) for day in range(1, 13)}
    assert _twin(left, {d: (c, v + 4) for d, (c, v) in left.items()}) == (12, 12)
    assert _twin(left, {d: (c + 0.01, v) for d, (c, v) in left.items()}) is None
    assert _twin(left, dict(list(left.items())[:TWIN_MIN_COMMON - 1])) is None


@pytest.fixture(scope="module")
def recent_series():
    from providers.eodhd_client import CACHE_DIR, EODHDClient

    cache = Path(os.environ.get("EODHD_CACHE_DIR") or CACHE_DIR)
    if not cache.is_dir():
        pytest.skip(f"no EODHD cache at {cache}")
    client = EODHDClient(cache_dir=cache, max_live_calls=0)
    series = {symbol: _recent_traded_bars(client, symbol) for symbol in active_symbols()}
    series = {symbol: bars for symbol, bars in series.items() if bars}
    if not series:
        pytest.skip(f"the EODHD cache at {cache} holds no active symbol")
    return series


def test_no_two_active_symbols_share_a_near_identical_recent_price_series(recent_series):
    twins = {}
    for left, right in itertools.combinations(sorted(recent_series), 2):
        found = _twin(recent_series[left], recent_series[right])
        if found:
            twins[(left, right)] = found

    unexpected = {pair: f"{same}/{common}" for pair, (same, common) in twins.items()
                  if pair not in UNRESOLVED_TWINS}
    assert not unexpected, (
        f"active symbols whose last {TWIN_WINDOW} EODHD bars match on close and "
        f"volume (matching/common sessions): {unexpected}. Register the copy in "
        f"{REGISTRY} once the live ticker is established."
    )
    settled = sorted(set(UNRESOLVED_TWINS) - set(twins))
    assert not settled, f"no longer price twins; remove from UNRESOLVED_TWINS: {settled}"
