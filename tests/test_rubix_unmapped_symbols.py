"""Behaviour of an active EODHD symbol that has no VERIFIED Rubix mapping.

The 241-symbol migration admitted 16 tickers that the Rubix `CASE` feed has
never been observed carrying. They are full members of the operational universe
— historical EODHD Daily selection includes them — but no ``CASE~TICKER`` key is
ever fabricated for them.

These tests pin the contract:

* a mapped candidate evaluates normally;
* an unmapped candidate stays a historical candidate and reports the distinct
  ``RUBIX_MAPPING_UNAVAILABLE`` state, never a generic collector failure;
* it is excluded from the Rubix subscription and from the feed query;
* it can never inherit another symbol's live quote;
* the UI explains it in Arabic and English.

No network call, no Rubix connection, no production database.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from core.universe import active_universe, lookup
from providers.rubix_subscription import build_rubix_subscription_plan
from scalping_expected_range.frozen_watchlist import StoredWatchlist
from scalping_expected_range.live_readiness import (
    LIVE_DATA_UNAVAILABLE,
    RUBIX_MAPPING_UNAVAILABLE,
    LiveEntryReadinessEngine,
    RubixBatchSnapshot,
    RubixQuote,
    RubixSymbolSnapshot,
    _has_rubix_mapping,
)

UTC = timezone.utc
TARGET = date(2026, 7, 28)
CONTINUOUS_TIME = datetime(2026, 7, 28, 8, 0, tzinfo=UTC)
WATCHLIST_ID = "FHW-unmapped-contract"

#: A real active symbol WITH a verified feed observation.
MAPPED = "RAYA"
#: A real active symbol admitted by the migration with NO feed observation.
UNMAPPED = "AGIG"


@pytest.fixture(autouse=True)
def _fixture_symbols_match_the_real_universe():
    assert _has_rubix_mapping(MAPPED), f"{MAPPED} should have a verified mapping"
    assert not _has_rubix_mapping(UNMAPPED), f"{UNMAPPED} should be unmapped"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

def _member(symbol, rank):
    return {
        "watchlist_id": WATCHLIST_ID, "symbol": symbol, "historical_rank": rank,
        "scoreable_rank": rank, "displayed_candidate": True,
        "historical_score": 90.0 - rank, "primary_historical_score": 90.0 - rank,
        "movement_potential_score": 88.0, "range_stability_score": 82.0,
        "upper_zone_consistency_score": 80.0, "lower_zone_consistency_score": 75.0,
        "combined_zone_consistency_score": 78.0, "zone_confidence_label": "HIGH",
        "liquidity_score": 85.0, "median_daily_range": 5.0,
        "normal_range_lower": 3.5, "normal_range_upper": 6.0,
        "range_hit_2pct_frequency": 0.8, "median_upper_excursion": 5.0,
        "median_lower_excursion": -2.0, "valid_sessions_primary": 60,
        "valid_sessions_recent": 30, "primary_readiness_status": "READY",
        "recent_confirmation_status": "CONFIRMED", "recent_penalty": 0.0,
        "eligibility_status": "HARD_ELIGIBLE", "exclusion_reason": None,
        "source": "EODHD_DAILY", "source_fingerprint": "sha256:" + symbol.lower() * 8,
        "latest_session": "2026-07-27", "data_cutoff": "2026-07-27",
        "metric_version": "DAILY_HISTORICAL_SELECTION_V2",
        "config_version": "DAILY_HISTORICAL_SELECTION_CONFIG_V2",
        "historical_explanation": "deterministic",
    }


def _record():
    members = (_member(MAPPED, 1), _member(UNMAPPED, 2))
    return StoredWatchlist(
        {"watchlist_id": WATCHLIST_ID, "status": "READY",
         "target_session_date": TARGET.isoformat(),
         "historical_data_cutoff": "2026-07-27",
         "eligible_count": 2, "displayed_count": 2, "top_n": 2},
        members,
    )


def _quote(symbol, price):
    stamp = CONTINUOUS_TIME - timedelta(seconds=15)
    return RubixQuote(symbol, price, price - 0.05, price + 0.05,
                      1_000_000, 0.2, stamp, stamp + timedelta(seconds=1))


def _snapshot(*, error_code=None):
    """A feed snapshot that contains ONLY the mapped symbol, as production does."""

    by_symbol = {MAPPED: RubixSymbolSnapshot(MAPPED, _quote(MAPPED, 100.2), ())}
    return RubixBatchSnapshot(
        TARGET.isoformat(), (MAPPED,), by_symbol, "2026-07-28T08:00:00+00:00",
        1, 2, 3.0, error_code=error_code,
        error_detail="collector down" if error_code else None,
    )


class _StubReader:
    """Records what the engine asked the feed for; never opens a database."""

    def __init__(self, snapshot=None):
        self.snapshot = snapshot or _snapshot()
        self.requested = None

    def load(self, symbols, *, target_session_date, evaluated_at):
        self.requested = tuple(symbols)
        return self.snapshot


def _engine(snapshot=None):
    return LiveEntryReadinessEngine(_StubReader(snapshot))


def _results(snapshot=None):
    batch = _engine().evaluate(
        _record(), evaluated_at=CONTINUOUS_TIME,
        live_snapshot=snapshot or _snapshot())
    return {item.symbol: item for item in batch.results}


# --------------------------------------------------------------------------- #
# Candidate with a verified mapping
# --------------------------------------------------------------------------- #

def test_a_candidate_with_a_verified_mapping_evaluates_normally():
    item = _results()[MAPPED]
    assert item.live_state != RUBIX_MAPPING_UNAVAILABLE
    assert item.current_price == 100.2
    assert item.data_quality_status != "NOT_QUERIED_SESSION_PHASE"


# --------------------------------------------------------------------------- #
# Candidate without a mapping
# --------------------------------------------------------------------------- #

def test_an_unmapped_candidate_is_kept_in_the_results():
    results = _results()
    assert set(results) == {MAPPED, UNMAPPED}
    # It keeps its frozen historical rank and EODHD Daily evidence.
    assert results[UNMAPPED].historical_rank == 2
    assert results[UNMAPPED].historical_score == 88.0


def test_an_unmapped_candidate_reports_the_explicit_mapping_state():
    item = _results()[UNMAPPED]
    assert item.live_state == RUBIX_MAPPING_UNAVAILABLE
    assert item.live_state != LIVE_DATA_UNAVAILABLE      # not a collector failure
    assert item.readiness_score is None


def test_the_unmapped_reason_is_explicit_and_names_eodhd_availability():
    item = _results()[UNMAPPED]
    reason = " ".join(item.explanations).lower()
    assert "no verified rubix mapping" in reason
    assert "historical eodhd daily data is available" in reason
    assert "not a collector failure" in reason
    gate = [g for g in item.hard_gates if g.name == "rubix_mapping"]
    assert gate and gate[0].passed is False
    assert "suffix-guessed" in gate[0].detail


def test_an_unmapped_candidate_never_claims_live_data_is_connected():
    item = _results()[UNMAPPED]
    assert item.current_price is None
    assert item.current_spread_percent is None
    assert item.quote_age_seconds is None
    assert item.data_quality_status == "NOT_QUERIED_SESSION_PHASE"


def test_an_unmapped_candidate_cannot_inherit_another_symbols_quote():
    """The feed snapshot holds only MAPPED's quote at price 100.2."""

    results = _results()
    assert results[MAPPED].current_price == 100.2
    assert results[UNMAPPED].current_price != 100.2
    assert results[UNMAPPED].current_price is None
    assert results[UNMAPPED].symbol == UNMAPPED


def test_only_mapped_symbols_are_queried_from_the_feed():
    reader = _StubReader()
    LiveEntryReadinessEngine(reader).evaluate(
        _record(), evaluated_at=CONTINUOUS_TIME)
    assert reader.requested == (MAPPED,)


def test_a_real_collector_failure_stays_distinct_from_an_unmapped_symbol():
    results = _results(_snapshot(error_code="RUBIX_DATABASE_UNAVAILABLE"))
    # The mapped symbol reports the genuine outage…
    assert results[MAPPED].live_state == LIVE_DATA_UNAVAILABLE
    # …while the unmapped one still reports its own, different reason.
    assert results[UNMAPPED].live_state == RUBIX_MAPPING_UNAVAILABLE


# --------------------------------------------------------------------------- #
# Subscription planning
# --------------------------------------------------------------------------- #

def test_no_guessed_case_ticker_is_ever_emitted():
    plan = build_rubix_subscription_plan()
    unmapped = {r.canonical_symbol for r in active_universe()
                if not r.has_verified_rubix_mapping}
    assert unmapped, "the migration admitted symbols with no feed observation"
    for symbol in unmapped:
        assert f"CASE~{symbol}" not in plan.subscriptions
        assert lookup(symbol).rubix_symbol == ""
    assert set(plan.unmapped) == unmapped


def test_a_mixed_plan_subscribes_the_mapped_and_reports_the_unmapped():
    plan = build_rubix_subscription_plan(source=[f"{MAPPED}.CA", f"{UNMAPPED}.CA"])
    assert plan.subscriptions == (f"CASE~{MAPPED}",)
    assert plan.unmapped == (UNMAPPED,)
    assert plan.invalid == ()          # unmapped is a reported gap, not an error
    assert plan.batches == ((f"CASE~{MAPPED}",),)


def test_the_plan_report_surfaces_the_unmapped_symbols():
    report = build_rubix_subscription_plan(
        source=[f"{MAPPED}.CA", f"{UNMAPPED}.CA"]).as_report()
    assert report["unmapped_symbols"] == [UNMAPPED]
    assert report["valid_subscriptions"] == 1
    assert report["invalid_mappings"] == []


def test_an_unmapped_symbol_does_not_break_the_collector_as_an_error():
    """`rubix_collector_supervisor` aborts on plan.invalid; it must stay empty."""

    plan = build_rubix_subscription_plan()
    assert plan.invalid == ()
    assert len(plan.subscriptions) == sum(
        1 for r in active_universe() if r.has_verified_rubix_mapping)


# --------------------------------------------------------------------------- #
# UI messaging
# --------------------------------------------------------------------------- #

def test_the_scalping_ui_explains_the_missing_mapping_in_arabic_and_english():
    from dashboard.scalping import _LIVE_STATE_AR, _blocking_reason, _rubix_status_label

    item = SimpleNamespace(live_state=RUBIX_MAPPING_UNAVAILABLE,
                           data_quality_status="NOT_QUERIED_SESSION_PHASE",
                           no_chase_reason=None, invalidation_condition=None,
                           explanations=())
    assert "No Rubix mapping" in _rubix_status_label(item)
    assert "لا يوجد ربط Rubix" in _rubix_status_label(item)

    reason = _blocking_reason(item)
    assert "Not a collector failure" in reason
    assert "ليست مشكلة في المجمِّع" in reason

    label = _LIVE_STATE_AR[RUBIX_MAPPING_UNAVAILABLE]
    assert "تاريخي فقط" in label and "No live mapping" in label


def test_the_uptrend_ui_explains_the_missing_mapping_in_arabic_and_english():
    from dashboard.uptrend_pullback import _block_reason, _live_label, _rubix_label

    item = SimpleNamespace(live_state=RUBIX_MAPPING_UNAVAILABLE,
                           data_quality_status="NOT_QUERIED_NO_RUBIX_MAPPING",
                           no_chase_reason=None, invalidation_condition=None,
                           explanations=())
    assert "No Rubix mapping" in _rubix_label(item)
    assert "تاريخي فقط" in _live_label(item)
    assert "Not a collector failure" in _block_reason(item)


def test_the_uptrend_engine_reports_the_same_explicit_state():
    from scalping_uptrend_pullback.live_readiness import (
        RUBIX_MAPPING_UNAVAILABLE as UPTREND_STATE,
    )

    assert UPTREND_STATE == RUBIX_MAPPING_UNAVAILABLE
