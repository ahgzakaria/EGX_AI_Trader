"""The routing tiers only reach production through an explicit promotion.

The review file describes itself as PROPOSED and INACTIVE with every entry
`approved: false`, and was nevertheless the only file the research router read —
so regenerating it changed which provider serves which symbol immediately. These
tests pin the gate that closes that, and pin the fallback that keeps a project
with nothing promoted working exactly as it did.
"""

import json

import pytest

from core import research_router
from scripts.promote_eodhd_routing_tiers import diff, promote


def write(path, symbols, **top):
    payload = {"schema_version": 1, "symbols": symbols}
    payload.update(top)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def entry(symbol, tier, **extra):
    row = {"symbol": symbol, "tier": tier, "approved": False}
    row.update(extra)
    return row


@pytest.fixture
def paths(tmp_path, monkeypatch):
    review = tmp_path / "review.json"
    active = tmp_path / "active.json"
    monkeypatch.setattr(research_router, "REVIEW_PATH", review)
    monkeypatch.setattr(research_router, "ACTIVE_PATH", active)
    monkeypatch.setattr(research_router, "_TIER_CACHE", {"map": {}, "mtime": None})
    return review, active


# --------------------------------------------------------------------------- #
# Which file wins
# --------------------------------------------------------------------------- #

def test_the_review_is_used_when_nothing_has_been_promoted(paths):
    """A project that has never promoted must not lose its routing."""

    review, _ = paths
    write(review, [entry("COMI", "TIER_A_FORWARD_SAFE")], active=False)

    assert research_router.tier_source() == review
    assert research_router.symbol_tier("COMI") == "TIER_A_FORWARD_SAFE"


def test_the_promoted_file_wins_once_it_exists(paths):
    review, active = paths
    write(review, [entry("COMI", "TIER_D_UNSUPPORTED_OR_MANUAL")], active=False)
    write(active, [entry("COMI", "TIER_A_FORWARD_SAFE", approved=True)], active=True)

    assert research_router.tier_source() == active
    assert research_router.symbol_tier("COMI") == "TIER_A_FORWARD_SAFE"


def test_regenerating_the_review_no_longer_reaches_production(paths):
    """The hazard this closes: a rebuilt proposal used to be live immediately."""

    review, active = paths
    write(active, [entry("COMI", "TIER_A_FORWARD_SAFE", approved=True)], active=True)
    write(review, [entry("COMI", "TIER_A_FORWARD_SAFE")], active=False)
    assert research_router.symbol_tier("COMI") == "TIER_A_FORWARD_SAFE"

    # A regeneration that would have demoted the symbol.
    write(review, [entry("COMI", "TIER_D_UNSUPPORTED_OR_MANUAL")], active=False)
    research_router._TIER_CACHE["mtime"] = None
    assert research_router.symbol_tier("COMI") == "TIER_A_FORWARD_SAFE"


def test_an_unknown_symbol_still_defaults_to_held(paths):
    review, _ = paths
    write(review, [entry("COMI", "TIER_A_FORWARD_SAFE")], active=False)
    assert research_router.symbol_tier("NOPE") == "TIER_D_UNSUPPORTED_OR_MANUAL"


def test_switching_files_invalidates_the_cache(paths):
    """The cache is keyed on the path as well as the mtime."""

    review, active = paths
    write(review, [entry("COMI", "TIER_C_HISTORICAL_REVIEW")], active=False)
    assert research_router.symbol_tier("COMI") == "TIER_C_HISTORICAL_REVIEW"

    write(active, [entry("COMI", "TIER_B_FORWARD_EODHD_NO_FALLBACK", approved=True)],
          active=True)
    assert research_router.symbol_tier("COMI") == "TIER_B_FORWARD_EODHD_NO_FALLBACK"


def test_no_file_at_all_yields_an_empty_map(paths):
    assert research_router.tier_map() == {}
    assert research_router.symbol_tier("COMI") == "TIER_D_UNSUPPORTED_OR_MANUAL"


# --------------------------------------------------------------------------- #
# The promotion itself
# --------------------------------------------------------------------------- #

def test_the_diff_names_what_promoting_would_change():
    review = {"A": {"tier": "TIER_A_FORWARD_SAFE"}, "NEW": {"tier": "TIER_B_FORWARD_EODHD_NO_FALLBACK"}}
    active = {"A": {"tier": "TIER_C_HISTORICAL_REVIEW"}, "GONE": {"tier": "TIER_A_FORWARD_SAFE"}}

    added, removed, changed = diff(review, active)
    assert added == ["NEW"]
    assert removed == ["GONE"]
    assert changed == ["A"]


def test_promoting_marks_every_entry_approved():
    data = {"schema_version": 1, "generated_at": "2026-08-27",
            "symbols": [entry("COMI", "TIER_A_FORWARD_SAFE"),
                        entry("TMGH", "TIER_C_HISTORICAL_REVIEW")]}
    promoted = promote({}, data, "operator")

    assert promoted["active"] is True
    assert all(row["approved"] is True for row in promoted["symbols"])
    assert all(row["approved_by"] == "operator" for row in promoted["symbols"])
    assert promoted["review_generated_at"] == "2026-08-27"


def test_promoting_preserves_every_tier_verbatim():
    """Promotion records approval; it must never re-decide a tier."""

    data = {"schema_version": 1, "symbols": [
        entry("COMI", "TIER_A_FORWARD_SAFE", evidence_status="clean"),
        entry("JUFO", "TIER_D_UNSUPPORTED_OR_MANUAL", evidence_status="insufficient"),
    ]}
    promoted = promote({}, data, "operator")
    by_symbol = {row["symbol"]: row for row in promoted["symbols"]}

    assert by_symbol["COMI"]["tier"] == "TIER_A_FORWARD_SAFE"
    assert by_symbol["JUFO"]["tier"] == "TIER_D_UNSUPPORTED_OR_MANUAL"
    assert by_symbol["JUFO"]["evidence_status"] == "insufficient"


def test_the_promoted_document_says_it_is_the_authority():
    promoted = promote({}, {"symbols": [entry("COMI", "TIER_A_FORWARD_SAFE")]}, "operator")
    assert "APPROVED" in promoted["description"]
    assert "authority" in promoted["description"]
