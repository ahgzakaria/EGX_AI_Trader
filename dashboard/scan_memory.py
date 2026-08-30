"""Keep a completed-session scan alive across page navigation.

Streamlit reruns the entire script on every interaction, and `st.button` is
True only on the run that immediately follows the click. A page written as

    if not st.button("Scan"):
        return
    result = scan()

therefore loses its results the moment anything else happens -- including
navigating to another page and back. The scan here reads a year of daily
history for the whole universe and takes about two minutes, so that was the
price of a click on the sidebar.

**Rerunning it cannot change the answer.** These scans read *finished* daily
bars for a session that has already closed. This is not a live quote panel;
the same inputs are on disk and the same rule reads them, so the same rows come
back. Discarding the result was pure cost.

What can change the answer is real, and both cases are handled rather than
assumed away:

* **The thresholds changed.** Then the stored rows were produced by a different
  rule and must not be shown as if they were current. The configuration is
  fingerprinted and a mismatch discards the memory outright.
* **A newer session has closed.** Then the stored rows are still exactly what
  the rule said -- about an older session. They are kept and labelled with the
  session they describe, never silently presented as today's.

The memory lives in ``st.session_state``: it belongs to one browser session and
does not outlive it. Nothing here is written to disk, because a scan is a view
of data that is already on disk, not a record of anything.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
import hashlib
import json
from typing import Any, NamedTuple, Optional

import streamlit as st


class RememberedScan(NamedTuple):
    """A scan result, plus what a reader needs to judge whether it is current."""

    result: Any
    scanned_at: datetime
    session_date: Optional[str]
    #: The newest session that had closed when this was recalled, or None when
    #: that could not be determined. Compare with ``session_date``.
    latest_session: Optional[str]

    @property
    def is_stale(self) -> bool:
        """True when a session has closed since this scan ran.

        Unknown is not stale. When the completed session cannot be resolved,
        this answers False and the caller shows the result unqualified -- the
        alternative is warning about staleness on no evidence, every rerun.
        """
        if not self.session_date or not self.latest_session:
            return False
        return str(self.latest_session) > str(self.session_date)


def config_digest(config) -> str:
    """A short hash of a strategy configuration.

    Accepts a dataclass or a mapping. Anything that does not serialise falls
    back to ``repr``, which is stable enough to detect a change even when it is
    not pretty -- the value is only ever compared with another of its own kind.
    """
    if config is None:
        payload = "none"
    elif is_dataclass(config) and not isinstance(config, type):
        payload = json.dumps(asdict(config), sort_keys=True, default=repr,
                             separators=(",", ":"))
    elif isinstance(config, dict):
        payload = json.dumps(config, sort_keys=True, default=repr,
                             separators=(",", ":"))
    else:
        payload = repr(config)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _latest_completed_session() -> Optional[str]:
    """The newest EGX session that has definitively closed, or None.

    Never raises. This decides whether to show a label, and a page must not
    fail to render because a calendar lookup did.
    """
    try:
        from core.egx_calendar import effective_holidays
        from core.egx_session import authoritative_completed_session

        value = authoritative_completed_session(holidays=effective_holidays())
        return None if value is None else str(value)[:10]
    except Exception:                                            # noqa: BLE001
        return None


def remember(key: str, result, config, session_date=None) -> None:
    """Store ``result`` for this browser session under ``key``."""

    st.session_state[f"scan_memory:{key}"] = {
        "result": result,
        "digest": config_digest(config),
        "scanned_at": datetime.now(),
        "session_date": (str(session_date)[:10] if session_date
                         else str(getattr(result, "session_date", "") or "")[:10] or None),
    }


def recall(key: str, config) -> Optional[RememberedScan]:
    """Return the stored scan for ``key``, or None.

    Returns None when nothing is stored, and also when the configuration no
    longer matches the one that produced it -- in that case the stale entry is
    dropped rather than left to be recalled again.
    """
    stored = st.session_state.get(f"scan_memory:{key}")
    if not stored:
        return None
    if stored.get("digest") != config_digest(config):
        forget(key)
        return None
    return RememberedScan(
        result=stored["result"],
        scanned_at=stored["scanned_at"],
        session_date=stored.get("session_date"),
        latest_session=_latest_completed_session(),
    )


def forget(key: str) -> None:
    """Drop the stored scan for ``key``, if any."""

    st.session_state.pop(f"scan_memory:{key}", None)


def scan_caption(remembered: RememberedScan) -> str:
    """One line saying when this ran and whether the market has moved past it."""

    when = remembered.scanned_at.strftime("%H:%M")
    session = remembered.session_date or "—"
    if remembered.is_stale:
        return (f"Scanned at {when} for session {session}. "
                f"**{remembered.latest_session} has closed since** — rescan to "
                f"see it.")
    return (f"Scanned at {when} for session {session}. Kept while you move "
            f"between pages; the bars it read are final, so rescanning returns "
            f"the same rows.")
