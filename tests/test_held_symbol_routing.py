"""TIER_D symbols served for display only, and never by accident.

A thinly traded stock is exactly the one whose sessions the daily finalizer
rejects as partial, so its local seed can never advance. Routing it to that path
alone made "held for manual review" and "no obtainable data" the same outcome
while a complete current series sat unused.

The rule these tests pin: a held frame is served **only** to a caller that asked
for one, and it always says it is not fit for automatic use.
"""

import pandas as pd
import pytest

from core import research_router
from core.research_router import (
    TIER_D_HELD_FOR_REVIEW,
    ResearchDataUnavailable,
    get_current_research_history,
)


HELD = "HELD"
SESSIONS = pd.bdate_range("2025-01-01", periods=400)


def eodhd_frame():
    frame = pd.DataFrame({
        "Open": 10.0, "High": 10.5, "Low": 9.5, "Close": 10.0,
        "Adj Close": 10.0, "Volume": 1_000.0,
    }, index=SESSIONS)
    frame.attrs["volume_meta"] = {
        "volume_series": "RAW_EODHD", "volume_safe_for_lookback": True,
    }
    return frame


@pytest.fixture
def held_symbol(monkeypatch):
    """A TIER_D symbol whose local path fails and whose EODHD series is current."""

    monkeypatch.setattr(research_router, "symbol_tier",
                        lambda symbol: "TIER_D_UNSUPPORTED_OR_MANUAL")
    monkeypatch.setattr(research_router, "tier_map", lambda: {})
    monkeypatch.setattr(research_router, "eodhd_history",
                        lambda base, **kwargs: eodhd_frame())
    return HELD


def fail_local(status):
    def _fail(base, **kwargs):
        raise ResearchDataUnavailable(base, status, "no seed")
    return _fail


def stale_local(status):
    def _stale(base, **kwargs):
        frame = pd.DataFrame({
            "Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0,
            "Adj Close": 1.0, "Volume": 1.0,
        }, index=SESSIONS[:300])
        return frame, status, {"seed_latest_session": "2026-07-22",
                               "effective_latest_session": "2026-07-22",
                               "bridge_sessions_appended": 0, "conflict_count": 0}
    return _stale


# --------------------------------------------------------------------------- #
# Default deny
# --------------------------------------------------------------------------- #

def test_a_held_symbol_stays_blocked_by_default_when_the_seed_is_missing(held_symbol, monkeypatch):
    monkeypatch.setattr(research_router, "local_plus_rubix_history",
                        fail_local("DATA_UNAVAILABLE"))
    with pytest.raises(ResearchDataUnavailable):
        get_current_research_history(held_symbol, min_bars=20)


def test_a_held_symbol_stays_blocked_by_default_when_the_seed_is_stale(held_symbol, monkeypatch):
    monkeypatch.setattr(research_router, "local_plus_rubix_history",
                        stale_local("LOCAL_SEED_ONLY_STALE"))
    with pytest.raises(ResearchDataUnavailable):
        get_current_research_history(held_symbol, min_bars=20)


def test_the_block_keeps_its_original_status(held_symbol, monkeypatch):
    """The caller must still learn why the local path failed, not a new reason."""

    monkeypatch.setattr(research_router, "local_plus_rubix_history",
                        stale_local("LOCAL_SEED_ONLY_STALE"))
    with pytest.raises(ResearchDataUnavailable) as raised:
        get_current_research_history(held_symbol, min_bars=20)
    assert raised.value.status == "LOCAL_SEED_ONLY_STALE"


# --------------------------------------------------------------------------- #
# Explicit opt-in
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("local", [
    fail_local("DATA_UNAVAILABLE"),
    stale_local("LOCAL_SEED_ONLY_STALE"),
    stale_local("LOCAL_PLUS_RUBIX_STALE"),
])
def test_both_local_exits_can_serve_a_held_frame(held_symbol, monkeypatch, local):
    """The no-seed exit raises and the stale exit returns a state; cover both."""

    monkeypatch.setattr(research_router, "local_plus_rubix_history", local)
    frame = get_current_research_history(held_symbol, min_bars=20, allow_held=True)
    assert len(frame) == len(SESSIONS)
    assert frame.attrs["market_data"]["data_quality_status"] == TIER_D_HELD_FOR_REVIEW


def test_a_held_frame_declares_itself_unfit_for_automatic_use(held_symbol, monkeypatch):
    monkeypatch.setattr(research_router, "local_plus_rubix_history",
                        fail_local("DATA_UNAVAILABLE"))
    metadata = get_current_research_history(
        held_symbol, min_bars=20, allow_held=True).attrs["market_data"]

    assert metadata["automatic_use_permitted"] is False
    assert metadata["held_reason"]
    assert metadata["routing_tier"] == "TIER_D_UNSUPPORTED_OR_MANUAL"
    assert metadata["provider"] == "eodhd"
    assert metadata["yahoo_network_used"] is False


def test_a_normally_routed_frame_permits_automatic_use(monkeypatch):
    """The flag must default to True everywhere else, not only be absent."""

    monkeypatch.setattr(research_router, "symbol_tier",
                        lambda symbol: "TIER_A_FORWARD_SAFE")
    monkeypatch.setattr(research_router, "tier_map", lambda: {})
    monkeypatch.setattr(research_router, "eodhd_history",
                        lambda base, **kwargs: eodhd_frame())
    monkeypatch.setattr(research_router, "unresolved_action_date", lambda base: None)
    monkeypatch.setattr(research_router, "bridge_tail_blocked",
                        lambda action, last: True)

    metadata = get_current_research_history("OK", min_bars=20).attrs["market_data"]
    assert metadata["automatic_use_permitted"] is True
    assert metadata["held_reason"] is None


# --------------------------------------------------------------------------- #
# The gates that still apply
# --------------------------------------------------------------------------- #

def test_an_unresolved_corporate_action_still_blocks_a_held_symbol(held_symbol, monkeypatch):
    """Turnover is price times volume, so untrustworthy volume blocks display too."""

    unsafe = eodhd_frame()
    unsafe.attrs["volume_meta"] = {"volume_safe_for_lookback": False,
                                   "latest_action_in_lookback": "2026-08-20"}
    monkeypatch.setattr(research_router, "eodhd_history", lambda base, **kwargs: unsafe)
    monkeypatch.setattr(research_router, "local_plus_rubix_history",
                        fail_local("DATA_UNAVAILABLE"))

    with pytest.raises(ResearchDataUnavailable):
        get_current_research_history(held_symbol, min_bars=20, allow_held=True)


def test_an_eodhd_failure_re_raises_the_original_block(held_symbol, monkeypatch):
    def boom(base, **kwargs):
        raise RuntimeError("provider down")
    monkeypatch.setattr(research_router, "eodhd_history", boom)
    monkeypatch.setattr(research_router, "local_plus_rubix_history",
                        stale_local("LOCAL_SEED_ONLY_STALE"))

    with pytest.raises(ResearchDataUnavailable) as raised:
        get_current_research_history(held_symbol, min_bars=20, allow_held=True)
    assert raised.value.status == "LOCAL_SEED_ONLY_STALE"


def test_a_non_equity_symbol_is_excluded_even_with_the_opt_in(monkeypatch):
    monkeypatch.setattr(research_router, "symbol_tier",
                        lambda symbol: "TIER_D_UNSUPPORTED_OR_MANUAL")
    monkeypatch.setattr(research_router, "tier_map",
                        lambda: {"ETF": {"evidence_status": "non_equity"}})
    with pytest.raises(ResearchDataUnavailable) as raised:
        get_current_research_history("ETF", min_bars=20, allow_held=True)
    assert raised.value.status == research_router.EXCLUDED_NON_EQUITY
