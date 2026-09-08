"""Session-hours watchdog decisions for the Rubix collector. Alert only.

Everything the watchdog decides lives here so it can be tested without a
desktop, a live supervisor or a database: the session window, what counts as a
loss worth waking the operator for, and the anti-repeat rule that keeps one
failure from becoming a stream of alerts.

**This module never restarts anything.** The collector's one human step is the
authentication-frame export, and `scripts/rubix_collector_supervisor.py`
refuses a frame older than 15 minutes at startup. A watchdog that relaunched
the supervisor mid-session would therefore either fail that gate or require
loosening it; the gate stays as it is, and the watchdog's whole job is to say
plainly that the feed died so a human can export a fresh frame and start it.
See `docs/audits/providers/rubix/RUBIX_AUTOSTART_ARCHITECTURE_AUDIT.md` §5.

What this module never does: authenticate, open a websocket, read or write the
Rubix database, take the supervisor lock, start or stop any process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum


#: EGX regular session in Cairo local time. The watchdog is deliberately silent
#: outside it: the collector exiting on its own after the close is documented
#: normal behaviour, not a fault worth an alert.
SESSION_OPEN = time(10, 0)
SESSION_CLOSE = time(14, 30)

#: How long the newest stored quote may lag before a live supervisor is called
#: stalled. Generous on purpose. A thin symbol can legitimately go quiet, but
#: the *whole market* going quiet for five minutes inside the session has only
#: ever meant a dead websocket. Under it, the supervisor's own liveness
#: watchdog is still the first responder and deserves room to reconnect.
FEED_STALL_SECONDS = 300.0


class WatchVerdict(str, Enum):
    """What the watchdog concluded on one poll."""

    #: Outside 10:00-14:30 Cairo. Nothing is expected to be running.
    OUTSIDE_SESSION = "OUTSIDE_SESSION"
    #: A live supervisor holds the lock and the feed is progressing.
    HEALTHY = "HEALTHY"
    #: The session is open and no supervisor has ever been seen this run. The
    #: morning start did not happen, or happened and died before the first poll.
    NEVER_STARTED = "NEVER_STARTED"
    #: A supervisor was observed alive and is now gone. This is the failure the
    #: watchdog exists for.
    SUPERVISOR_LOST = "SUPERVISOR_LOST"
    #: The process is alive but the database has stopped growing.
    FEED_STALLED = "FEED_STALLED"


#: Verdicts that warrant waking the operator. HEALTHY and OUTSIDE_SESSION are
#: the quiet ones, and quiet is the normal state.
ALERTING_VERDICTS = frozenset(
    {WatchVerdict.NEVER_STARTED, WatchVerdict.SUPERVISOR_LOST, WatchVerdict.FEED_STALLED}
)


def in_session(moment: datetime) -> bool:
    """Is `moment` inside the EGX regular session, by its own local clock?

    Weekday and holiday are not asked here. The watchdog only ever reports on a
    supervisor it can see, and on a non-trading day there is none to lose --
    whereas duplicating the calendar would put a second, drifting answer next to
    `core.calendar.egx_calendar_service`.
    """

    return SESSION_OPEN <= moment.time() <= SESSION_CLOSE


def assess(
    *,
    moment: datetime,
    supervisor_running: bool,
    feed_age_seconds: float | None,
    ever_seen_alive: bool,
    stall_seconds: float = FEED_STALL_SECONDS,
) -> WatchVerdict:
    """Reduce one poll to a verdict. Pure: no clock, no I/O, no process calls.

    `feed_age_seconds` is None when the database could not be read at all. That
    is deliberately *not* an alert on its own: a reader failing says something
    about the reader, and calling the feed dead on that evidence would cry wolf
    on a locked file or a transient permission error. A genuinely dead feed also
    shows up as a lost supervisor or, once the process is alive but mute, as a
    stall the next time the database does open.
    """

    if not in_session(moment):
        return WatchVerdict.OUTSIDE_SESSION
    if not supervisor_running:
        return WatchVerdict.SUPERVISOR_LOST if ever_seen_alive else WatchVerdict.NEVER_STARTED
    if feed_age_seconds is not None and feed_age_seconds > stall_seconds:
        return WatchVerdict.FEED_STALLED
    return WatchVerdict.HEALTHY


@dataclass
class WatchState:
    """Carries what one poll cannot know: history.

    Two things need remembering. Whether a supervisor was ever alive, which is
    the only thing separating "it died" from "it never started". And which
    alerts have already fired, so a five-hour outage is one alert and not nine
    hundred.
    """

    ever_seen_alive: bool = False
    #: Verdicts already announced and not yet cleared by a return to health.
    announced: set[WatchVerdict] = field(default_factory=set)

    def observe(self, verdict: WatchVerdict) -> bool:
        """Record `verdict` and answer: should this one be announced now?

        An alert fires on the transition into a condition, never on every poll
        while it persists. Recovery clears the record, so a feed that stalls,
        recovers and stalls again alerts twice -- which is the truth, and is
        what an operator needs to see.
        """

        if verdict is WatchVerdict.HEALTHY:
            self.ever_seen_alive = True
            self.announced.clear()
            return False
        if verdict is WatchVerdict.OUTSIDE_SESSION:
            return False
        if verdict is WatchVerdict.FEED_STALLED:
            # The process is alive, so this poll is also evidence for the
            # "it died" branch of any later loss.
            self.ever_seen_alive = True
        if verdict in self.announced:
            return False
        self.announced.add(verdict)
        return True


def alert_message(verdict: WatchVerdict, *, feed_age_seconds: float | None = None) -> str:
    """One line an operator can act on without opening a log."""

    if verdict is WatchVerdict.SUPERVISOR_LOST:
        return (
            "Rubix collector died mid-session. Nothing is being recorded. "
            "Export a fresh auth frame and start it from the launcher."
        )
    if verdict is WatchVerdict.NEVER_STARTED:
        return (
            "EGX session is open and the Rubix collector is not running. "
            "Export an auth frame and start it from the launcher."
        )
    if verdict is WatchVerdict.FEED_STALLED:
        age = "unknown" if feed_age_seconds is None else f"{feed_age_seconds:.0f}s"
        return (
            f"Rubix collector is running but the feed has been silent for {age}. "
            "The websocket is probably dead; check the launcher."
        )
    return f"Rubix collector watchdog: {verdict.value}"


__all__ = [
    "ALERTING_VERDICTS",
    "FEED_STALL_SECONDS",
    "SESSION_CLOSE",
    "SESSION_OPEN",
    "WatchState",
    "WatchVerdict",
    "alert_message",
    "assess",
    "in_session",
]
