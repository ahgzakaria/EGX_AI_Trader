"""A discovery source can raise a closure for review and can never confirm one.

EGX's own hosts refuse an identified client: www.egx.com.eg and egx.com.eg reset
the connection for the sync's user-agent, and beta.egx.com.eg answers 200 with a
WAF rejection body. Presenting a browser string to defeat that is not something
this project does, so the official tier stays wired to EGX and reports itself
unreachable, while discovery runs against a source that accepts an honest client.

That split only holds if a discovered candidate can never become CONFIRMED, and
if a reachable discovery source never disguises an unreachable official one.
"""

import pytest

from core.calendar import egx_holiday_sync as sync
from core.calendar.egx_holiday_sync import (
    DISCOVERY_SOURCES,
    OFFICIAL_DOMAINS,
    OFFICIAL_SOURCES,
    Candidate,
    classify_confirmation,
)


class FakeService:
    def __init__(self, conflicts=False):
        self._conflicts = conflicts
        self.ingested = []

    def conflicts(self, day=None):
        return self._conflicts

    def ingest_discovery(self, record):
        self.ingested.append(record)
        return {"action": "new"}

    def read_sync_state(self):
        return {}

    def write_sync_state(self, state):
        self.state = state


def candidate(**kwargs):
    fields = {
        "date": "2026-08-27", "name_en": "EGX closed", "name_ar": "إجازة البورصة",
        "closure_type": "FULL_DAY", "ambiguous": False,
        "source_url": "https://www.mubasher.info/news/eg/now/latest",
        "source_title": "Mubasher — EGX news", "source_type": "THIRD_PARTY_DISCOVERY",
        "evidence": "تعطيل التداول بالبورصة يوم 27 أغسطس 2026",
        "confidence": 0.9,
    }
    fields.update(kwargs)
    return Candidate(**fields)


# --------------------------------------------------------------------------- #
# The tiers are separate by construction
# --------------------------------------------------------------------------- #

def test_no_discovery_source_sits_on_an_official_domain():
    for source in DISCOVERY_SOURCES:
        assert not any(domain in source["url"] for domain in OFFICIAL_DOMAINS), source


def test_no_discovery_source_claims_an_official_type():
    for source in DISCOVERY_SOURCES:
        assert source["source_type"] not in ("EGX_OFFICIAL", "EGX_TRADING_CALENDAR"), source


def test_the_official_tier_still_points_at_egx():
    assert OFFICIAL_SOURCES
    for source in OFFICIAL_SOURCES:
        assert any(domain in source["url"] for domain in OFFICIAL_DOMAINS), source


# --------------------------------------------------------------------------- #
# A discovered candidate can never confirm itself
# --------------------------------------------------------------------------- #

def test_a_discovery_candidate_is_never_confirmed():
    """Unambiguous, dated, explicit closure — and still only NEEDS_REVIEW."""

    assert classify_confirmation(candidate(), FakeService()) == "NEEDS_REVIEW"


def test_an_official_candidate_can_be_confirmed():
    """The counterpart: the policy is not simply refusing everything."""

    official = candidate(source_url="https://www.egx.com.eg/en/News.aspx",
                         source_type="EGX_TRADING_CALENDAR")
    assert classify_confirmation(official, FakeService()) == "CONFIRMED"


def test_an_official_domain_alone_is_not_enough():
    spoofed_type = candidate(source_url="https://www.egx.com.eg/en/News.aspx",
                             source_type="THIRD_PARTY_DISCOVERY")
    assert classify_confirmation(spoofed_type, FakeService()) == "NEEDS_REVIEW"


def test_an_official_type_alone_is_not_enough():
    wrong_domain = candidate(source_type="EGX_TRADING_CALENDAR")
    assert classify_confirmation(wrong_domain, FakeService()) == "NEEDS_REVIEW"


# --------------------------------------------------------------------------- #
# A reachable discovery source must not mask an unreachable official one
# --------------------------------------------------------------------------- #

def test_the_verdict_reports_the_official_tier_not_the_source_count(monkeypatch):
    service = FakeService()
    html = {source["url"]: "<html>nothing to see</html>" for source in DISCOVERY_SOURCES}

    summary = sync.run_sync(commit=False, svc=service, html_by_source=html)

    assert summary["verdict"] == "OFFICIAL_SOURCE_UNAVAILABLE"
    assert summary["official_sources_reachable"] == []
    assert summary["discovery_sources_reachable"] == [s["name"] for s in DISCOVERY_SOURCES]


def test_everything_unreachable_is_still_reported_as_such():
    summary = sync.run_sync(commit=False, svc=FakeService(), offline=True)
    assert summary["verdict"] == "CALENDAR_SOURCE_UNAVAILABLE"
    assert summary["official_sources_reachable"] == []
    assert summary["discovery_sources_reachable"] == []


def test_a_reachable_official_tier_verdicts_ok():
    html = {source["url"]: "<html>quiet day</html>"
            for source in OFFICIAL_SOURCES + DISCOVERY_SOURCES}
    summary = sync.run_sync(commit=False, svc=FakeService(), html_by_source=html)
    assert summary["verdict"] == "OK"
    assert len(summary["official_sources_reachable"]) == len(OFFICIAL_SOURCES)


def test_discovery_sources_are_actually_scanned():
    """The point of adding the tier: its pages reach the extractor."""

    announcement = (
        "<html><body>تعطيل التداول بالبورصة المصرية يوم الخميس 27 أغسطس 2026 "
        "بمناسبة إجازة رسمية</body></html>"
    )
    html = {source["url"]: announcement for source in DISCOVERY_SOURCES}
    service = FakeService()
    summary = sync.run_sync(commit=True, svc=service, html_by_source=html, year_hint=2026)

    assert summary["candidates"], "the discovery page produced no candidate"
    assert all(row["auto_verdict"] == "NEEDS_REVIEW" for row in summary["candidates"])
    assert all(row["status"] != "CONFIRMED" for row in service.ingested)
