"""ORB Shadow orchestrator — unattended workflow tests.

Research only. No test starts Rubix, opens a websocket, authenticates, reaches
the network, installs a scheduled task, or reads the production database.
Sources are temporary synthetic SQLite files shaped like the real `quotes`
table, and every clock-dependent decision is driven by injected instants.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from scalping_orb.config import OrbDataConfig
from scalping_orb.repository import SCHEMA_VERSION, OrbResearchRepository
from scalping_orb.shadow_calendar import (
    CalendarAvailability,
    ShadowTradingCalendar,
    TradingDayStatus,
    load_trading_calendar,
)
from scalping_orb.shadow_orchestrator import (
    LEGAL_TRANSITIONS,
    TERMINAL_STATES,
    FullSessionCriteria,
    LeaseUnavailable,
    OrchestratorFailure,
    OrchestratorHealthSample,
    OrchestratorState,
    OrchestratorTransition,
    OrchestratorVerdict,
    assert_legal_transition,
    lease_scope_for,
    machine_identity,
    new_instance_id,
)
from scripts.run_orb_shadow_orchestrator import (
    ShadowOrchestrator,
    parse_args,
    show_status,
)


CAIRO = ZoneInfo("Africa/Cairo")
DAY = date(2026, 8, 4)          # Tuesday
FRIDAY = date(2026, 8, 7)
CONFIG = OrbDataConfig()

SOURCE_SCHEMA = """
CREATE TABLE quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL, last_price REAL, bid REAL, ask REAL, volume REAL,
    market_timestamp TEXT NOT NULL, received_at TEXT NOT NULL,
    exchange TEXT, sequence INTEGER, change_percent REAL,
    has_feed_timestamp INTEGER NOT NULL DEFAULT 0
);
"""


def at(hour, minute, second=0, day=DAY):
    return datetime.combine(day, time(hour, minute, second), tzinfo=CAIRO).astimezone(
        timezone.utc
    )


def synthetic_source(path, tickers=("AAA", "BBB"), minutes=40, day=DAY):
    rows = []
    for index in range(minutes):
        moment = at(10, 0, day=day) + timedelta(minutes=index)
        for offset, ticker in enumerate(tickers):
            price = 10.0 + 0.01 * index + 0.1 * offset
            rows.append(
                (ticker, price, price - 0.01, price + 0.01, 1000.0 * (index + 1),
                 moment.isoformat(), (moment + timedelta(seconds=1)).isoformat(),
                 "CASE", 0.0)
            )
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SOURCE_SCHEMA)
        connection.executemany(
            """INSERT INTO quotes (ticker,last_price,bid,ask,volume,market_timestamp,
               received_at,exchange,sequence,change_percent,has_feed_timestamp)
               VALUES (?,?,?,?,?,?,?,?,NULL,?,1)""",
            rows,
        )
        connection.commit()
    finally:
        connection.close()
    return Path(path)


@pytest.fixture
def source(tmp_path):
    return synthetic_source(tmp_path / "rubix_synthetic.db")


def make_args(tmp_path, source, **overrides):
    argv = [
        "--rubix-db-path", str(source),
        "--research-root", str(tmp_path / "research"),
        "--session-date", overrides.pop("session_date", DAY).isoformat(),
    ]
    for key, value in overrides.items():
        flag = "--" + key.replace("_", "-")
        if value is True:
            argv.append(flag)
        elif value is not False and value is not None:
            argv.extend([flag, str(value)])
    return parse_args(argv)


#: A calendar that is available and deliberately simple.
OPEN_CALENDAR = ShadowTradingCalendar(holidays=frozenset(), source="TEST")


def orchestrator(tmp_path, source, *, calendar=OPEN_CALENDAR, **overrides):
    args = make_args(tmp_path, source, **overrides)
    instance = ShadowOrchestrator(args)
    object.__setattr__(instance, "calendar", calendar)
    instance.calendar_decision = calendar.classify(instance.session_date)
    return instance


# =========================================================================== #
# TRADING CALENDAR
# =========================================================================== #


def test_a_regular_weekday_is_a_trading_day():
    decision = OPEN_CALENDAR.classify(DAY)
    assert decision.status is TradingDayStatus.TRADING_DAY
    assert decision.is_trading_day


@pytest.mark.parametrize("day", [date(2026, 8, 7), date(2026, 8, 8)])  # Fri, Sat
def test_weekends_are_not_trading_days(day):
    decision = OPEN_CALENDAR.classify(day)
    assert decision.status is TradingDayStatus.WEEKEND
    assert not decision.is_trading_day


def test_a_configured_holiday_is_not_a_trading_day():
    calendar = ShadowTradingCalendar(holidays=frozenset({DAY}), source="TEST")
    assert calendar.classify(DAY).status is TradingDayStatus.CONFIGURED_HOLIDAY


def test_an_exceptional_closure_is_reported_distinctly_from_a_holiday():
    calendar = ShadowTradingCalendar(
        exceptional_closures=frozenset({DAY}), source="TEST"
    )
    decision = calendar.classify(DAY)
    assert decision.status is TradingDayStatus.EXCEPTIONAL_CLOSURE
    assert not decision.is_trading_day


def test_a_special_trading_day_opens_a_day_the_rules_would_close():
    calendar = ShadowTradingCalendar(
        holidays=frozenset({FRIDAY}),
        special_trading_days=frozenset({FRIDAY}),
        source="TEST",
    )
    decision = calendar.classify(FRIDAY)
    assert decision.status is TradingDayStatus.SPECIAL_TRADING_DAY
    assert decision.is_trading_day


def test_a_missing_calendar_fails_closed():
    calendar = ShadowTradingCalendar(
        availability=CalendarAvailability.CALENDAR_LOAD_FAILED,
        unavailable_reason="boom",
    )
    decision = calendar.classify(DAY)
    assert decision.status is TradingDayStatus.CALENDAR_UNAVAILABLE
    assert not decision.is_trading_day, "must never assume the market is open"


def test_an_empty_shared_calendar_is_treated_as_unavailable():
    """An empty holiday set is indistinguishable from a failed load."""

    calendar = load_trading_calendar(holidays=None, require_holidays=True)
    if calendar.available:
        assert calendar.holidays, "available calendars must carry holidays"
    else:
        assert calendar.classify(DAY).status is TradingDayStatus.CALENDAR_UNAVAILABLE


def test_an_explicitly_empty_calendar_is_allowed():
    calendar = load_trading_calendar(holidays=frozenset())
    assert calendar.available
    assert calendar.classify(DAY).status is TradingDayStatus.TRADING_DAY


def test_unreadable_overrides_fail_closed(tmp_path):
    bad = tmp_path / "overrides.json"
    bad.write_text("{not json", encoding="utf-8")
    calendar = load_trading_calendar(overrides_path=bad)
    assert not calendar.available
    assert "unreadable" in (calendar.unavailable_reason or "")


def test_the_calendar_identity_is_stable_and_content_derived():
    a = ShadowTradingCalendar(holidays=frozenset({DAY}), source="TEST")
    b = ShadowTradingCalendar(holidays=frozenset({DAY}), source="TEST")
    c = ShadowTradingCalendar(holidays=frozenset({FRIDAY}), source="TEST")
    assert a.identity == b.identity
    assert a.identity != c.identity


def test_the_orchestrator_skips_a_non_trading_day(tmp_path, source):
    calendar = ShadowTradingCalendar(holidays=frozenset({DAY}), source="TEST")
    result = orchestrator(tmp_path, source, calendar=calendar).run()
    assert result["state"] == OrchestratorState.SKIPPED_NON_TRADING_DAY.value
    assert result["calendar_status"] == TradingDayStatus.CONFIGURED_HOLIDAY.value


def test_the_orchestrator_refuses_when_the_calendar_is_unavailable(tmp_path, source):
    calendar = ShadowTradingCalendar(
        availability=CalendarAvailability.CALENDAR_LOAD_FAILED,
        unavailable_reason="no calendar",
    )
    result = orchestrator(tmp_path, source, calendar=calendar).run()
    assert result["state"] == OrchestratorState.SKIPPED_NON_TRADING_DAY.value
    assert result["calendar_status"] == TradingDayStatus.CALENDAR_UNAVAILABLE.value
    assert any(f["code"] == "CALENDAR_UNAVAILABLE" for f in result["failures"])


def test_a_weekday_is_never_silently_assumed_to_be_open():
    """The whole point of the boundary: no branch returns TRADING_DAY blind."""

    unavailable = ShadowTradingCalendar(
        availability=CalendarAvailability.CALENDAR_LOAD_FAILED
    )
    for day in (DAY, FRIDAY, date(2026, 8, 5)):
        assert not unavailable.classify(day).is_trading_day


def test_calendar_availability_distinguishes_its_failure_modes():
    """A valid empty calendar is not the same problem as a failed load."""

    assert load_trading_calendar(holidays=frozenset()).availability is (
        CalendarAvailability.VALID_EMPTY_CALENDAR
    )
    assert load_trading_calendar(
        holidays=frozenset({date(2026, 1, 1)})
    ).availability is CalendarAvailability.AVAILABLE


def test_a_valid_empty_calendar_is_usable_and_a_failed_load_is_not():
    valid_empty = ShadowTradingCalendar(
        availability=CalendarAvailability.VALID_EMPTY_CALENDAR, source="TEST"
    )
    assert valid_empty.available
    assert valid_empty.classify(DAY).status is TradingDayStatus.TRADING_DAY

    for broken in (
        CalendarAvailability.CALENDAR_LOAD_FAILED,
        CalendarAvailability.CALENDAR_MALFORMED,
    ):
        calendar = ShadowTradingCalendar(availability=broken, source="TEST")
        assert not calendar.available
        assert calendar.classify(DAY).status is TradingDayStatus.CALENDAR_UNAVAILABLE


def test_a_malformed_override_file_is_reported_as_malformed(tmp_path):
    bad = tmp_path / "overrides.json"
    bad.write_text("{not json", encoding="utf-8")
    calendar = load_trading_calendar(overrides_path=bad)
    assert calendar.availability is CalendarAvailability.CALENDAR_MALFORMED
    assert calendar.classify(DAY).availability is (
        CalendarAvailability.CALENDAR_MALFORMED
    )


def test_an_unattested_empty_shared_calendar_stays_a_failed_load():
    """Ambiguity resolves to failure, never to 'the market is open'."""

    calendar = load_trading_calendar(require_holidays=True)
    if not calendar.holidays:
        assert calendar.availability is CalendarAvailability.CALENDAR_LOAD_FAILED
        assert not calendar.available


def test_allow_empty_calendar_turns_ambiguity_into_a_valid_empty_calendar():
    calendar = load_trading_calendar(
        require_holidays=False, allow_empty_calendar=True
    )
    if not calendar.holidays:
        assert calendar.availability is CalendarAvailability.VALID_EMPTY_CALENDAR
        assert calendar.available


def test_the_availability_is_part_of_the_calendar_identity():
    a = ShadowTradingCalendar(availability=CalendarAvailability.AVAILABLE, source="T")
    b = ShadowTradingCalendar(
        availability=CalendarAvailability.VALID_EMPTY_CALENDAR, source="T"
    )
    assert a.identity != b.identity


# =========================================================================== #
# CLOCK AND SLEEP INJECTION
# =========================================================================== #


class FakeClock:
    """A clock the test drives. Never advances on its own."""

    def __init__(self, start):
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now = self.now + timedelta(seconds=seconds)


def test_a_future_session_never_calls_real_time_sleep(tmp_path, source, monkeypatch):
    """Regression: a mutation once reached the real wait loop.

    Four pytest processes then slept toward a future market open until they
    were killed. Real `time.sleep` is banned outright for the duration of this
    test, so reaching it fails loudly instead of hanging.
    """

    import scripts.run_orb_shadow_orchestrator as module

    def forbidden(_seconds):
        raise AssertionError("real time.sleep was called during a test")

    monkeypatch.setattr(module.time_module, "sleep", forbidden)

    clock = FakeClock(at(9, 0))          # an hour before the start lead
    args = make_args(tmp_path, source)
    instance = ShadowOrchestrator(args, clock=clock, sleeper=clock.sleep)
    object.__setattr__(instance, "calendar", OPEN_CALENDAR)
    instance.calendar_decision = OPEN_CALENDAR.classify(DAY)

    assert instance.pre_session_checks() is True
    assert instance.wait_for_start() is True
    assert clock.sleeps, "the fake sleeper should have been used"
    assert instance.state is OrchestratorState.LIVE_SHADOW_STARTING


def test_the_wait_loop_is_bounded_when_the_clock_never_advances(tmp_path, source):
    """A stopped clock must fail, not spin forever."""

    frozen = FakeClock(at(9, 0))
    args = make_args(tmp_path, source, maximum_wait_iterations=5)
    instance = ShadowOrchestrator(
        args, clock=frozen, sleeper=lambda _s: None  # clock never advances
    )
    object.__setattr__(instance, "calendar", OPEN_CALENDAR)
    instance.calendar_decision = OPEN_CALENDAR.classify(DAY)
    assert instance.pre_session_checks() is True
    assert instance.wait_for_start() is False
    assert instance.state is OrchestratorState.SESSION_FAILED
    assert any(f.failure_code == "WAIT_LOOP_EXCEEDED" for f in instance.failures)


def test_a_stop_request_interrupts_the_wait_cleanly(tmp_path, source):
    import scripts.run_orb_shadow_orchestrator as module

    clock = FakeClock(at(9, 0))
    args = make_args(tmp_path, source)
    instance = ShadowOrchestrator(args, clock=clock, sleeper=clock.sleep)
    object.__setattr__(instance, "calendar", OPEN_CALENDAR)
    instance.calendar_decision = OPEN_CALENDAR.classify(DAY)
    assert instance.pre_session_checks() is True

    module._STOP["value"] = True
    module._STOP["reason"] = "SIGNAL_2"
    try:
        assert instance.wait_for_start() is False
    finally:
        module._STOP["value"] = False
        module._STOP["reason"] = ""
    assert instance.state is OrchestratorState.SESSION_FAILED


def test_the_clock_and_sleeper_default_to_the_real_ones(tmp_path, source):
    """Production still uses the real clock; injection is a test seam only."""

    import scripts.run_orb_shadow_orchestrator as module

    instance = ShadowOrchestrator(make_args(tmp_path, source))
    assert instance._clock is module._utc_now
    assert instance._sleeper is module.time_module.sleep


# =========================================================================== #
# RESEARCH ROOT CONTAINMENT
# =========================================================================== #


def test_the_research_path_stays_under_its_root(tmp_path, source):
    instance = orchestrator(tmp_path, source)
    root = (tmp_path / "research").resolve()
    assert instance.research_db_path.parent == root


def test_a_traversing_research_root_cannot_escape(tmp_path, source):
    """`..` inside the root resolves before containment is checked."""

    escaping = tmp_path / "research" / ".." / ".." / "outside"
    args = make_args(tmp_path, source)
    args.research_root = str(escaping)
    instance = ShadowOrchestrator.__new__(ShadowOrchestrator)
    instance.args = args
    instance.session_date = DAY
    resolved = instance._research_db_path()
    # The root itself resolves to 'outside'; the file must still sit inside it.
    assert resolved.parent == escaping.resolve()
    assert resolved.name == f"orb_full_shadow_{DAY.isoformat()}.db"


def test_the_generated_filename_is_never_operator_supplied(tmp_path, source):
    """Only the root is configurable; the filename comes from the session date."""

    instance = orchestrator(tmp_path, source)
    assert instance.research_db_path.name == f"orb_full_shadow_{DAY.isoformat()}.db"
    assert "/" not in instance.research_db_path.name
    assert "\\" not in instance.research_db_path.name


# =========================================================================== #
# STATE MACHINE
# =========================================================================== #


def test_no_state_represents_a_trade():
    forbidden = ("BUY", "SELL", "ORDER", "POSITION", "TRADE", "FILL", "PNL")
    for state in OrchestratorState:
        for word in forbidden:
            assert word not in state.value, state.value


def test_an_illegal_transition_raises():
    with pytest.raises(ValueError, match="illegal orchestrator transition"):
        assert_legal_transition(
            OrchestratorState.SCHEDULED, OrchestratorState.REPORT_COMPLETE
        )


def test_no_transition_may_leave_a_terminal_state():
    for state in TERMINAL_STATES:
        with pytest.raises(ValueError, match="terminal"):
            assert_legal_transition(state, OrchestratorState.PRE_SESSION_CHECK)


def test_every_non_terminal_state_has_a_declared_exit():
    for state in OrchestratorState:
        if state in TERMINAL_STATES:
            continue
        assert state in LEGAL_TRANSITIONS, state.value
        assert LEGAL_TRANSITIONS[state], state.value


def test_full_verdict_requires_every_criterion():
    partial = FullSessionCriteria()
    assert partial.verdict() is OrchestratorVerdict.PARTIAL_SHADOW_SESSION
    assert partial.failures()

    complete = FullSessionCriteria(
        **{name: True for name in FullSessionCriteria.__dataclass_fields__}
    )
    assert complete.all_met
    assert complete.verdict() is OrchestratorVerdict.FULL_SHADOW_SESSION_OBSERVED


def test_a_failed_live_lane_can_never_be_full():
    complete = FullSessionCriteria(
        **{name: True for name in FullSessionCriteria.__dataclass_fields__}
    )
    assert complete.verdict(live_failed=True) is (
        OrchestratorVerdict.FAILED_SHADOW_SESSION
    )


def test_a_single_unmet_criterion_prevents_full():
    fields = {name: True for name in FullSessionCriteria.__dataclass_fields__}
    fields["opening_range_observed_live"] = False
    criteria = FullSessionCriteria(**fields)
    assert criteria.verdict() is OrchestratorVerdict.PARTIAL_SHADOW_SESSION
    assert criteria.failures() == ("opening_range_observed_live",)


def test_there_is_no_override_that_forces_full():
    import inspect

    signature = inspect.signature(FullSessionCriteria.verdict)
    for name in signature.parameters:
        assert "force" not in name.lower()
        assert "override" not in name.lower()


# =========================================================================== #
# SINGLE INSTANCE
# =========================================================================== #


def _lease_args(repository, instance_id, *, now, seconds=300, pid=1234):
    return dict(
        lease_scope=lease_scope_for(DAY, "sid"), instance_id=instance_id,
        orchestrator_run_id=f"run-{instance_id[:6]}", session_date=DAY,
        process_id=pid, machine_identity=machine_identity(),
        now=now, lease_seconds=seconds,
    )


def test_the_first_instance_acquires_the_lease(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    now = at(9, 40)
    holder = repository.acquire_orchestrator_lease(**_lease_args(repository, "a" * 32, now=now))
    assert holder.is_active(now)


def test_a_second_instance_is_refused(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    now = at(9, 40)
    repository.acquire_orchestrator_lease(**_lease_args(repository, "a" * 32, now=now))
    with pytest.raises(LeaseUnavailable, match="is held by instance"):
        repository.acquire_orchestrator_lease(
            **_lease_args(repository, "b" * 32, now=now + timedelta(seconds=5))
        )


def test_an_active_lease_cannot_be_stolen(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    now = at(9, 40)
    repository.acquire_orchestrator_lease(
        **_lease_args(repository, "a" * 32, now=now, seconds=600)
    )
    for offset in (1, 100, 599):
        with pytest.raises(LeaseUnavailable):
            repository.acquire_orchestrator_lease(
                **_lease_args(repository, "b" * 32, now=now + timedelta(seconds=offset))
            )


def test_a_stale_lease_is_recovered_safely(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    now = at(9, 40)
    repository.acquire_orchestrator_lease(
        **_lease_args(repository, "a" * 32, now=now, seconds=300)
    )
    later = now + timedelta(seconds=301)
    holder = repository.acquire_orchestrator_lease(
        **_lease_args(repository, "b" * 32, now=later, seconds=300)
    )
    assert holder.instance_id == "b" * 32
    assert holder.is_active(later)


def test_a_heartbeat_extends_the_lease(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    now = at(9, 40)
    scope = lease_scope_for(DAY, "sid")
    repository.acquire_orchestrator_lease(
        **_lease_args(repository, "a" * 32, now=now, seconds=300)
    )
    mid = now + timedelta(seconds=200)
    assert repository.heartbeat_orchestrator_lease(
        scope, "a" * 32, now=mid, lease_seconds=300
    )
    # Originally expiring at +300; the heartbeat pushed it to +500.
    with pytest.raises(LeaseUnavailable):
        repository.acquire_orchestrator_lease(
            **_lease_args(repository, "b" * 32, now=now + timedelta(seconds=350))
        )


def test_a_heartbeat_from_a_displaced_holder_fails(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    now = at(9, 40)
    scope = lease_scope_for(DAY, "sid")
    repository.acquire_orchestrator_lease(
        **_lease_args(repository, "a" * 32, now=now, seconds=100)
    )
    later = now + timedelta(seconds=200)
    repository.acquire_orchestrator_lease(
        **_lease_args(repository, "b" * 32, now=later, seconds=300)
    )
    assert not repository.heartbeat_orchestrator_lease(
        scope, "a" * 32, now=later, lease_seconds=300
    )


def test_concurrent_acquisition_through_separate_connections(tmp_path):
    """Two independent repository objects, as two processes would have."""

    path = tmp_path / "orb.db"
    first = OrbResearchRepository(path)
    second = OrbResearchRepository(path)      # separate connection pool
    now = at(9, 40)

    first.acquire_orchestrator_lease(**_lease_args(first, "a" * 32, now=now))
    with pytest.raises(LeaseUnavailable):
        second.acquire_orchestrator_lease(
            **_lease_args(second, "b" * 32, now=now + timedelta(seconds=1))
        )
    # The loser sees the winner's details, not a partial write.
    holder = second.load_orchestrator_lease(lease_scope_for(DAY, "sid"))
    assert holder.instance_id == "a" * 32
    assert holder.is_active(now)


def test_a_clock_rollback_cannot_create_two_owners(tmp_path):
    """A system-clock jump backwards must not resurrect a displaced holder.

    Ownership is decided by the instance id recorded in the row, not by whose
    clock looks newer, so rolling the clock back cannot let the old holder
    renew or the new holder be displaced silently.
    """

    repository = OrbResearchRepository(tmp_path / "orb.db")
    scope = lease_scope_for(DAY, "sid")
    now = at(9, 40)
    repository.acquire_orchestrator_lease(
        **_lease_args(repository, "a" * 32, now=now, seconds=100)
    )
    later = now + timedelta(seconds=200)
    repository.acquire_orchestrator_lease(
        **_lease_args(repository, "b" * 32, now=later, seconds=300)
    )
    # Clock jumps back to before the takeover.
    rolled_back = now + timedelta(seconds=10)
    assert not repository.heartbeat_orchestrator_lease(
        scope, "a" * 32, now=rolled_back, lease_seconds=300
    ), "a displaced holder must not renew after a clock rollback"
    holder = repository.load_orchestrator_lease(scope)
    assert holder.instance_id == "b" * 32


def test_the_process_id_alone_does_not_prove_ownership(tmp_path):
    """PID is informational; the instance id is the credential."""

    repository = OrbResearchRepository(tmp_path / "orb.db")
    scope = lease_scope_for(DAY, "sid")
    now = at(9, 40)
    repository.acquire_orchestrator_lease(
        **_lease_args(repository, "a" * 32, now=now, pid=4321)
    )
    # Same PID, different instance id: still refused.
    with pytest.raises(LeaseUnavailable):
        repository.acquire_orchestrator_lease(
            **_lease_args(repository, "b" * 32, now=now, pid=4321)
        )
    # Renewal keys on the instance id, not the PID.
    assert repository.heartbeat_orchestrator_lease(
        scope, "a" * 32, now=now, lease_seconds=300
    )


def test_the_machine_identity_is_hashed_not_a_hostname(tmp_path):
    identity = machine_identity()
    import socket

    assert socket.gethostname() not in identity
    assert len(identity) == 16
    assert all(character in "0123456789abcdef" for character in identity)


def test_lease_release_is_owner_qualified(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    scope = lease_scope_for(DAY, "sid")
    now = at(9, 40)
    repository.acquire_orchestrator_lease(
        **_lease_args(repository, "a" * 32, now=now, seconds=600)
    )
    # A different instance's release must not free someone else's lease.
    repository.release_orchestrator_lease(scope, "b" * 32, now=now)
    assert repository.load_orchestrator_lease(scope).is_active(now)
    with pytest.raises(LeaseUnavailable):
        repository.acquire_orchestrator_lease(
            **_lease_args(repository, "c" * 32, now=now + timedelta(seconds=1))
        )


def test_a_released_lease_is_immediately_available(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    now = at(9, 40)
    scope = lease_scope_for(DAY, "sid")
    repository.acquire_orchestrator_lease(
        **_lease_args(repository, "a" * 32, now=now, seconds=600)
    )
    repository.release_orchestrator_lease(scope, "a" * 32, now=now + timedelta(seconds=10))
    holder = repository.acquire_orchestrator_lease(
        **_lease_args(repository, "b" * 32, now=now + timedelta(seconds=20))
    )
    assert holder.instance_id == "b" * 32


def test_leases_are_scoped_per_session_and_source(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    now = at(9, 40)
    base = _lease_args(repository, "a" * 32, now=now)
    repository.acquire_orchestrator_lease(**base)
    other = dict(base)
    other["lease_scope"] = lease_scope_for(DAY, "different-source")
    other["instance_id"] = "b" * 32
    assert repository.acquire_orchestrator_lease(**other).instance_id == "b" * 32


def test_a_duplicate_orchestrator_instance_skips(tmp_path, source):
    first = orchestrator(tmp_path, source)
    assert first.pre_session_checks() is True
    second = orchestrator(tmp_path, source)
    result = second.run()
    assert result["state"] == OrchestratorState.SKIPPED_DUPLICATE_INSTANCE.value
    assert any(f["code"] == "DUPLICATE_INSTANCE" for f in result["failures"])


# =========================================================================== #
# PRE-SESSION CHECKS
# =========================================================================== #


def test_pre_session_checks_pass_on_a_healthy_source(tmp_path, source):
    instance = orchestrator(tmp_path, source)
    assert instance.pre_session_checks() is True
    assert instance.state is OrchestratorState.PRE_SESSION_CHECK
    assert instance.repository is not None
    assert instance.lease is not None


def test_a_missing_source_skips_safely(tmp_path):
    instance = orchestrator(tmp_path, tmp_path / "absent.db")
    result = instance.run()
    assert result["state"] == OrchestratorState.SKIPPED_SOURCE_UNAVAILABLE.value
    assert any(f["code"] == "SOURCE_UNAVAILABLE" for f in result["failures"])


def test_an_empty_source_skips_safely(tmp_path):
    empty = tmp_path / "empty.db"
    connection = sqlite3.connect(empty)
    connection.executescript(SOURCE_SCHEMA)
    connection.commit()
    connection.close()
    result = orchestrator(tmp_path, empty).run()
    assert result["state"] == OrchestratorState.SKIPPED_SOURCE_UNAVAILABLE.value
    assert any(f["code"] == "COLLECTOR_NO_ROWS" for f in result["failures"])


def test_the_research_destination_may_not_be_the_source(tmp_path, source):
    args = make_args(tmp_path, source)
    instance = ShadowOrchestrator(args)
    object.__setattr__(instance, "calendar", OPEN_CALENDAR)
    instance.calendar_decision = OPEN_CALENDAR.classify(DAY)
    instance.research_db_path = Path(source)
    result = instance.run()
    assert result["state"] == OrchestratorState.SESSION_FAILED.value
    assert any(f["code"] == "UNSAFE_DESTINATION" for f in result["failures"])


def test_the_generated_paths_derive_from_the_session_date(tmp_path, source):
    instance = orchestrator(tmp_path, source)
    assert instance.research_db_path.name == f"orb_full_shadow_{DAY.isoformat()}.db"
    assert instance.log_dir.name == DAY.isoformat()
    assert instance.report_dir.name == DAY.isoformat()
    assert "logs" in instance.log_dir.parts and "orb_shadow" in instance.log_dir.parts
    assert "full_shadow" in instance.report_dir.parts


# =========================================================================== #
# END-TO-END WORKFLOW
# =========================================================================== #


def _seed_live_run(instance, *, lane_a=1):
    """A committed live run matching what the orchestrator will look for."""

    from scalping_orb.shadow_service import (
        ShadowCycleMetrics, ShadowLiveStatus, ShadowStateRecord,
    )

    repository = instance.repository
    run_id = "seededLiveRun"
    repository.start_shadow_run(
        run_id, DAY, mode="FOLLOW", started_at_utc=at(9, 45),
        runner_started_before_open=True,
        source_path_identity=instance.source_identity,
        config_identity=instance.data_config.fingerprint,
        strategy_fingerprint=instance.strategy_config.strategy_fingerprint,
        engine_version="ev", active_universe_only=True,
    )
    if lane_a:
        cycle = ShadowCycleMetrics(
            cycle_id="seed-c0", cycle_index=0, started_at_utc=at(11, 0),
            finished_at_utc=at(11, 1), cursor_low_source_id=0, cursor_high_source_id=1,
            source_rows_read=1, normalized_events=1, session_loads=1,
            symbols_in_snapshot=1, symbols_evaluated=1, snapshot_identity="s",
            live_status=ShadowLiveStatus.LIVE_SHADOW_HEALTHY, duration_seconds=0.1,
        )
        record = ShadowStateRecord(
            session_date=DAY, canonical_ticker="AAA", opening_range_revision=0,
            opening_range_version_identity="or-v1",
            final_state="BREAKOUT_REJECTED_STALE", terminal=True,
            rejection_reasons=("LIVE_DECISION_DISABLED_FRESHNESS",),
            evidence_fingerprint="e", candidate_identity="c",
            live_status=ShadowLiveStatus.LIVE_SHADOW_HEALTHY,
            observed_at_utc=at(11, 0), exchange_watermark_utc=at(11, 0),
            observed_receive_lag_seconds=1.0,
        )
        repository.persist_shadow_cycle(run_id, cycle, (record,))
    repository.finish_shadow_run(
        run_id, finished_at_utc=at(14, 20), stop_reason="CONTINUOUS_END_REACHED",
        session_classification="PARTIAL_SHADOW_SESSION",
    )
    return run_id


def test_post_session_selects_the_live_run_without_a_manual_id(tmp_path, source):
    """The manual copy-paste step this whole layer exists to remove."""

    instance = orchestrator(tmp_path, source)
    assert instance.pre_session_checks()
    seeded = _seed_live_run(instance)
    instance.live_run_id = None
    instance.state = OrchestratorState.LIVE_SHADOW_PARTIAL

    assert instance.run_post_session() is True
    assert instance.live_run_id == seeded, "selected automatically"
    assert instance.reconstruction_run_id is not None
    assert instance.reconstruction_run_id != seeded
    assert instance.state is OrchestratorState.COMPARISON_COMPLETE


def test_post_session_persists_a_cross_run_comparison(tmp_path, source):
    instance = orchestrator(tmp_path, source)
    assert instance.pre_session_checks()
    seeded = _seed_live_run(instance)
    instance.state = OrchestratorState.LIVE_SHADOW_PARTIAL
    assert instance.run_post_session()

    rows = instance.repository.load_cross_run_comparison(
        seeded, instance.reconstruction_run_id
    )
    assert rows
    assert instance.repository.table_count("orb_shadow_cross_run_comparison") == len(rows)


def test_reconstruction_writes_no_lane_a_rows(tmp_path, source):
    instance = orchestrator(tmp_path, source)
    assert instance.pre_session_checks()
    seeded = _seed_live_run(instance)
    before = instance.repository.table_count("orb_shadow_live_states")
    instance.state = OrchestratorState.LIVE_SHADOW_PARTIAL
    assert instance.run_post_session()
    assert instance.repository.table_count("orb_shadow_live_states") == before
    assert instance.repository.load_shadow_live_states(
        instance.reconstruction_run_id
    ) == ()


def test_the_report_is_generated_automatically(tmp_path, source):
    instance = orchestrator(tmp_path, source)
    assert instance.pre_session_checks()
    _seed_live_run(instance)
    instance.state = OrchestratorState.LIVE_SHADOW_PARTIAL
    assert instance.run_post_session()
    assert instance.write_report() is True

    report = instance.report_dir / "FULL_SHADOW_SESSION_REPORT.md"
    assert report.is_file()
    text = report.read_text(encoding="utf-8")
    assert "Research Only" in text
    assert "not necessarily actionable live" in text
    # The report names these only to say it does NOT report them. Assert the
    # disclaimer is present and that no metric section exists, rather than
    # banning the words — banning them would delete the disclaimer itself.
    assert "No profitability, win rate, expectancy, position size or order" in text
    for heading in ("## Profitability", "## Win rate", "## P&L", "## Returns"):
        assert heading not in text
    # The report must SAY "NOT A BUY SIGNAL"; it must not ISSUE one.
    assert "NOT A BUY SIGNAL" in text
    assert "NOT LIVE EXECUTION" in text
    assert "RESEARCH ONLY" in text
    for instruction in ("place order", "target quantity", "## Order", "Recommendation:"):
        assert instruction not in text


def test_the_report_states_the_unmet_full_criteria(tmp_path, source):
    instance = orchestrator(tmp_path, source)
    assert instance.pre_session_checks()
    _seed_live_run(instance)
    instance.state = OrchestratorState.LIVE_SHADOW_PARTIAL
    instance.run_post_session()
    instance.write_report()
    text = (instance.report_dir / "FULL_SHADOW_SESSION_REPORT.md").read_text(
        encoding="utf-8"
    )
    assert "Unmet FULL criteria" in text
    assert "PARTIAL_SHADOW_SESSION" in text


def test_no_eligible_live_run_fails_visibly(tmp_path, source):
    instance = orchestrator(tmp_path, source)
    assert instance.pre_session_checks()
    instance.state = OrchestratorState.LIVE_SHADOW_PARTIAL
    assert instance.run_post_session() is False
    assert instance.state is OrchestratorState.SESSION_FAILED
    assert any(f.failure_code == "NO_ELIGIBLE_LIVE_RUN" for f in instance.failures)
    # And no fabricated comparison was written.
    assert instance.repository.table_count("orb_shadow_cross_run_comparison") == 0


def test_a_live_run_without_lane_a_rows_is_not_selected(tmp_path, source):
    instance = orchestrator(tmp_path, source)
    assert instance.pre_session_checks()
    _seed_live_run(instance, lane_a=0)
    instance.state = OrchestratorState.LIVE_SHADOW_PARTIAL
    assert instance.run_post_session() is False
    assert any(f.failure_code == "NO_ELIGIBLE_LIVE_RUN" for f in instance.failures)


# =========================================================================== #
# RECOVERY AND IDEMPOTENCY
# =========================================================================== #


def test_retry_post_session_never_creates_lane_a(tmp_path, source):
    instance = orchestrator(tmp_path, source, retry_post_session=True)
    assert instance.pre_session_checks()
    _seed_live_run(instance)
    before = instance.repository.table_count("orb_shadow_live_states")
    instance.state = OrchestratorState.LIVE_SHADOW_PARTIAL
    instance.run_post_session()
    instance.write_report()
    assert instance.repository.table_count("orb_shadow_live_states") == before


def test_repeating_post_session_is_idempotent(tmp_path, source):
    instance = orchestrator(tmp_path, source)
    assert instance.pre_session_checks()
    seeded = _seed_live_run(instance)
    instance.state = OrchestratorState.LIVE_SHADOW_PARTIAL
    instance.run_post_session()
    first_rows = len(
        instance.repository.load_cross_run_comparison(
            seeded, instance.reconstruction_run_id
        )
    )
    written = instance.repository.persist_cross_run_comparison(
        seeded, instance.reconstruction_run_id, ()
    )
    assert written == 0
    assert len(
        instance.repository.load_cross_run_comparison(
            seeded, instance.reconstruction_run_id
        )
    ) == first_rows


def test_transitions_are_recorded_with_full_context(tmp_path, source):
    instance = orchestrator(tmp_path, source)
    instance.pre_session_checks()
    rows = instance.repository.load_orchestrator_transitions(
        instance.orchestrator_run_id
    )
    assert rows
    assert [row["sequence_index"] for row in rows] == list(range(len(rows)))
    assert all(row["reason"] for row in rows)


def test_duplicate_transitions_are_not_written_twice(tmp_path, source):
    instance = orchestrator(tmp_path, source)
    instance.pre_session_checks()
    transition = OrchestratorTransition(
        sequence_index=99, session_date=DAY,
        prior_state=OrchestratorState.SCHEDULED,
        new_state=OrchestratorState.PRE_SESSION_CHECK,
        occurred_at_utc=at(9, 40), reason="dup",
        config_identity=instance.data_config.fingerprint,
    )
    for _ in range(3):
        instance.repository.record_orchestrator_transition(
            instance.orchestrator_run_id, transition
        )
    rows = instance.repository.load_orchestrator_transitions(
        instance.orchestrator_run_id
    )
    assert sum(1 for row in rows if row["sequence_index"] == 99) == 1


def test_failures_remain_visible_in_the_final_status(tmp_path):
    result = orchestrator(Path(os.environ.get("TMP", ".")), Path("no-such.db")).run()
    assert result["failures"]
    assert result["final_verdict"] in {
        OrchestratorVerdict.PARTIAL_SHADOW_SESSION.value,
        OrchestratorVerdict.FAILED_SHADOW_SESSION.value,
    }
    assert result["final_verdict"] != (
        OrchestratorVerdict.FULL_SHADOW_SESSION_OBSERVED.value
    )


def test_status_starts_nothing_and_takes_no_lease(tmp_path, source, capsys):
    args = make_args(tmp_path, source)
    assert show_status(args) == 0
    out = capsys.readouterr().out
    assert "RESEARCH ONLY" in out
    assert "NO_RUN_RECORDED" in out
    research = tmp_path / "research" / f"orb_full_shadow_{DAY.isoformat()}.db"
    assert not research.exists(), "--status must not create the research database"


def test_status_reports_a_recorded_run(tmp_path, source, capsys):
    instance = orchestrator(tmp_path, source)
    instance.pre_session_checks()
    capsys.readouterr()
    assert show_status(make_args(tmp_path, source)) == 0
    out = capsys.readouterr().out
    assert instance.orchestrator_run_id[:16] in out
    assert "lease" in out.lower()


# =========================================================================== #
# SAFETY
# =========================================================================== #


def test_the_source_is_never_written(tmp_path, source):
    import hashlib

    before = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    instance = orchestrator(tmp_path, source)
    instance.pre_session_checks()
    _seed_live_run(instance)
    instance.state = OrchestratorState.LIVE_SHADOW_PARTIAL
    instance.run_post_session()
    assert hashlib.sha256(Path(source).read_bytes()).hexdigest() == before


def test_no_collector_websocket_or_trading_code_exists():
    import io
    import tokenize

    def code_only(text):
        kept = []
        previous = tokenize.INDENT
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                continue
            if token.type == tokenize.STRING and previous in (
                tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE, tokenize.NL
            ):
                continue
            kept.append(token.string)
            if token.type not in (tokenize.NL, tokenize.COMMENT):
                previous = token.type
        return " ".join(kept)

    forbidden = (
        "websocket", "authenticate", "api_key", "password", "subprocess", "Popen",
        "taskkill", "start_rubix", "launch_rubix", "place_order", "submit_order",
        "broker", "position_size", "portfolio_heat", "send_alert", "streamlit",
        "paper_trade", "realized_pnl", "yahoo",
    )
    for name in (
        "scripts/run_orb_shadow_orchestrator.py",
        "scalping_orb/shadow_orchestrator.py",
        "scalping_orb/shadow_calendar.py",
    ):
        code = code_only(Path(name).read_text(encoding="utf-8"))
        for word in forbidden:
            assert word not in code, f"{name} contains {word!r}"


def test_the_orchestrator_spawns_no_process(tmp_path, source):
    import multiprocessing
    import threading

    threads = threading.active_count()
    children = len(multiprocessing.active_children())
    instance = orchestrator(tmp_path, source)
    instance.pre_session_checks()
    _seed_live_run(instance)
    instance.state = OrchestratorState.LIVE_SHADOW_PARTIAL
    instance.run_post_session()
    assert threading.active_count() == threads
    assert len(multiprocessing.active_children()) == children


def test_research_only_banners_are_unavoidable():
    from scripts.run_orb_shadow_orchestrator import BANNER

    assert "RESEARCH ONLY" in BANNER
    assert "PRODUCTION EXECUTION DISABLED" in BANNER
    assert "no BUY, SELL, order" in BANNER


def test_runtime_modules_hard_code_no_machine_path():
    for name in (
        "scripts/run_orb_shadow_orchestrator.py",
        "scalping_orb/shadow_orchestrator.py",
        "scalping_orb/shadow_calendar.py",
    ):
        text = Path(name).read_text(encoding="utf-8")
        assert "EGX_AI_Trader" not in text
        assert "F:\\" not in text


def test_a_production_database_cannot_be_the_research_target(tmp_path):
    protected = tmp_path / "rubix_live_market.db"
    protected.write_bytes(b"")
    with pytest.raises(ValueError, match="protected/production"):
        OrbResearchRepository(protected)


# =========================================================================== #
# WINDOWS SCHEDULED TASK
# =========================================================================== #


INSTALL_PS1 = Path("scripts/windows/install_orb_shadow_scheduled_task.ps1")
REMOVE_PS1 = Path("scripts/windows/remove_orb_shadow_scheduled_task.ps1")


def test_the_scheduled_task_scripts_exist():
    assert INSTALL_PS1.is_file()
    assert REMOVE_PS1.is_file()


def test_the_task_invokes_the_orchestrator_and_never_rubix():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "run_orb_shadow_orchestrator.py" in text
    for forbidden in ("launch_rubix", "start_rubix", "rubix_collector_supervisor",
                      "start_rubix_production"):
        assert forbidden not in text


def test_the_task_command_quotes_its_paths():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert '"""$RubixDbPath"""' in text
    assert '"""$ResearchRoot"""' in text
    assert "-WorkingDirectory $WorktreePath" in text


def test_the_task_stores_no_credential():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    for forbidden in ("-Password", "ConvertTo-SecureString", "PSCredential",
                      "api_key", "-AsPlainText"):
        assert forbidden not in text


def test_the_task_enforces_one_instance_and_a_bounded_runtime():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "-MultipleInstances IgnoreNew" in text
    assert "-ExecutionTimeLimit" in text


def test_the_task_requires_no_administrator_rights():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "-RunLevel Limited" in text
    assert "-RunLevel Highest" not in text


def test_the_task_validates_paths_before_registering():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "Assert-PathExists" in text
    assert "Test-Path" in text


def test_the_installer_does_not_assume_the_machine_timezone():
    """Task Scheduler fires on LOCAL time; the exchange runs on Cairo."""

    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "Egypt Standard Time" in text
    assert "Africa/Cairo" in text
    assert "[System.TimeZoneInfo]::Local" in text
    assert "ConvertTimeToUtc" in text and "ConvertTimeFromUtc" in text
    assert "TIMEZONE MISMATCH" in text
    # A Cairo wall-clock parameter, plus an explicit local override.
    assert "$CairoStartTime" in text
    assert "$LocalStartTimeOverride" in text


def test_the_installer_warns_that_egypt_observes_dst():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "DST" in text
    assert "Re-run this" in text or "re-run" in text.lower()


def test_the_installer_refuses_when_cairo_cannot_be_resolved():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "Cannot resolve the Cairo timezone" in text
    assert "throw" in text


def test_the_installer_prints_the_command_before_registering():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    printed = text.index("Task command:")
    registered = text.index("Register-ScheduledTask")
    assert printed < registered, "the command must be shown before registration"


def test_whatifonly_returns_before_registration():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    whatif = text.index("if ($WhatIfOnly)")
    registered = text.index("Register-ScheduledTask")
    assert whatif < registered


def test_the_task_command_carries_no_session_specific_date():
    """The schedule is daily; the session date is resolved at run time."""

    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "--session-date" not in text


def test_the_installer_dry_run_registers_nothing(tmp_path):
    """Executes the installer with -WhatIfOnly. Nothing is registered."""

    import subprocess

    result = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-File", str(INSTALL_PS1),
            "-WorktreePath", str(Path.cwd()),
            "-PythonExe", sys_executable(),
            "-RubixDbPath", str(_touch(tmp_path / "rubix source.db")),
            "-WhatIfOnly",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "nothing was registered" in result.stdout.lower()
    # Paths with spaces survive quoting.
    assert "rubix source.db" in result.stdout
    check = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         "if (Get-ScheduledTask -TaskName 'ORB_Shadow_Orchestrator' "
         "-ErrorAction SilentlyContinue) {'YES'} else {'NO'}"],
        capture_output=True, text=True,
    )
    assert check.stdout.strip() == "NO", "the dry run must not register the task"


def sys_executable() -> str:
    import sys

    return sys.executable


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_the_removal_script_does_not_delete_evidence():
    text = REMOVE_PS1.read_text(encoding="utf-8")
    assert "Unregister-ScheduledTask" in text
    for forbidden in ("Remove-Item", "rm -", "del "):
        assert forbidden not in text


def test_both_scripts_parse_as_powershell():
    """Parse only. Nothing is registered or executed."""

    import subprocess

    command = (
        "$e=$null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        "(Resolve-Path $args[0]).Path,[ref]$null,[ref]$e) | Out-Null; "
        "if($e.Count -gt 0){exit 1}else{exit 0}"
    )
    for script in (INSTALL_PS1, REMOVE_PS1):
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command, str(script)],
            capture_output=True,
        )
        assert result.returncode == 0, f"{script} failed to parse"


# =========================================================================== #
# MIGRATION 7
# =========================================================================== #


V6_TABLES = (
    "orb_sessions", "orb_normalized_events", "orb_bars", "orb_candidates",
    "orb_shadow_runs", "orb_shadow_live_states", "orb_shadow_cross_run_comparison",
)
ORCHESTRATOR_TABLES = (
    "orb_shadow_orchestrator_runs", "orb_shadow_orchestrator_transitions",
    "orb_shadow_orchestrator_leases", "orb_shadow_orchestrator_health",
    "orb_shadow_orchestrator_failures",
)


def _seed_v6(path):
    from datetime import timedelta as _td

    from scalping_orb.events import RubixQuoteInput
    from scalping_orb.shadow import OrbShadowIngestionService

    repository = OrbResearchRepository(path, target_schema_version=6)
    config = OrbDataConfig(research_database_path=str(path))
    service = OrbShadowIngestionService(
        repository, config, holidays=(), mapping_validator=lambda a, b: True
    )
    quotes = []
    seq = 0
    for ticker in ("AAA", "BBB"):
        for minute in range(20):
            seq += 1
            moment = at(10, 0) + _td(minutes=minute)
            quotes.append(
                RubixQuoteInput(
                    canonical_ticker=ticker, verified_rubix_symbol=f"CASE~{ticker}",
                    market_timestamp=moment, receive_timestamp=moment + _td(seconds=1),
                    sequence=seq, last_price=10.0 + minute * 0.01,
                    cumulative_volume=100.0 * (minute + 1), bid=9.99, ask=10.01,
                    has_feed_timestamp=True, source_row_id=seq,
                )
            )
    service.ingest(quotes, evaluated_at=at(14, 15))
    repository.start_shadow_run(
        "legacyRun", DAY, mode="FOLLOW", started_at_utc=at(9, 45),
        runner_started_before_open=True, source_path_identity="sid",
        config_identity="cid", strategy_fingerprint="sf", engine_version="ev",
    )
    return repository


def _snapshot(repository, tables):
    with repository.connect() as connection:
        return {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in tables
        }


def test_migration_seven_creates_every_orchestrator_table(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    assert repository.database_status()["user_version"] == 7
    for table in ORCHESTRATOR_TABLES:
        assert repository.table_count(table) == 0


def test_migration_seven_upgrades_a_populated_v6_database(tmp_path):
    path = tmp_path / "orb.db"
    old = _seed_v6(path)
    assert old.database_status()["user_version"] == 6
    before = _snapshot(old, V6_TABLES)
    assert before["orb_normalized_events"] > 0
    assert before["orb_shadow_runs"] == 1

    upgraded = OrbResearchRepository(path)
    assert upgraded.database_status()["user_version"] == 7
    assert _snapshot(upgraded, V6_TABLES) == before


def test_migration_seven_keeps_wal_and_foreign_keys(tmp_path):
    path = tmp_path / "orb.db"
    _seed_v6(path)
    upgraded = OrbResearchRepository(path)
    status = upgraded.database_status()
    assert status["journal_mode"] == "WAL"
    assert status["foreign_keys"] is True
    with upgraded.connect() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_repeating_migration_seven_changes_nothing(tmp_path):
    path = tmp_path / "orb.db"
    _seed_v6(path)
    first = OrbResearchRepository(path)
    before = _snapshot(first, V6_TABLES)
    for _ in range(3):
        OrbResearchRepository(path).migrate()
    final = OrbResearchRepository(path)
    assert final.database_status()["user_version"] == SCHEMA_VERSION
    assert _snapshot(final, V6_TABLES) == before


def test_a_failing_migration_seven_rolls_back_atomically(tmp_path, monkeypatch):
    import scalping_orb.repository as repository_module

    path = tmp_path / "orb.db"
    old = _seed_v6(path)
    before = _snapshot(old, V6_TABLES)

    broken = dict(repository_module.MIGRATIONS)
    broken[7] = ("phase2c_shadow_orchestrator", "CREATE TABLE not valid sql (;")
    monkeypatch.setattr(repository_module, "MIGRATIONS", broken)
    with pytest.raises(sqlite3.Error):
        OrbResearchRepository(path)
    monkeypatch.undo()

    recovered = OrbResearchRepository(path, target_schema_version=6)
    assert recovered.database_status()["user_version"] == 6
    assert _snapshot(recovered, V6_TABLES) == before
    with recovered.connect() as connection:
        names = {r[0] for r in connection.execute("SELECT name FROM sqlite_master")}
    assert "orb_shadow_orchestrator_runs" not in names


def test_a_downgrade_below_seven_is_refused(tmp_path):
    path = tmp_path / "orb.db"
    OrbResearchRepository(path)
    with pytest.raises(RuntimeError, match="downgrade"):
        OrbResearchRepository(path, target_schema_version=6)


def test_no_forbidden_trading_table_or_column_exists(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    forbidden = (
        "order", "execution", "position", "trade", "pnl", "profit", "broker",
        "fill", "alert", "notification", "commission", "quantity",
    )
    with repository.connect() as connection:
        names = {
            r[0] for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for table in ORCHESTRATOR_TABLES:
            for row in connection.execute(f"PRAGMA table_info({table})"):
                assert not any(word in row[1].lower() for word in forbidden), row[1]
    for word in forbidden:
        assert not any(word in name.lower() for name in names), word


# =========================================================================== #
# REGRESSIONS
# =========================================================================== #


def test_the_manual_session_commands_remain_supported():
    """Automation must not remove the hand-run path."""

    from scripts.run_orb_shadow_session import parse_args as session_parse

    for argv in (
        ["--rubix-db-path", "x.db", "--follow", "--active-universe-only"],
        ["--rubix-db-path", "x.db", "--reconstruct", "--compare-latest-live-run"],
        ["--list-runs", "--research-db-path", "y.db"],
    ):
        assert session_parse(argv) is not None


def test_no_threshold_changed():
    from scalping_orb.strategy_config import OrbStrategyConfig

    config = OrbStrategyConfig()
    assert config.minimum_reward_risk == 1.5
    assert config.target_1_r_multiple == 1.0
    assert config.target_2_r_multiple == 2.0
    assert config.maximum_breakout_extension_percent == 0.020
    assert config.maximum_structural_breach_bars == 0
