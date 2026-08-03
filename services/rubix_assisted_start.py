"""Assisted-start logic for the existing Rubix collector. No GUI, no I/O side effects.

Everything the assisted mode decides lives here so it can be tested without a
desktop session: inbox scanning, frame selection, the collector-only command,
and the post-start health gate. The tkinter layer on top only renders this.

What this module never does: authenticate, open a websocket, drive a browser,
store a credential, launch Streamlit, or reimplement frame validation. Frame
validity comes from the existing official validator
(`services.rubix_auth_assistant.inspect_auth_frame`); duplicate prevention
comes from the existing `SingleInstanceLock` and `supervisor_status`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
import os

from services.rubix_auth_assistant import (
    AUTH_EXPIRED,
    AUTH_INVALID,
    DEFAULT_MAX_AGE_MINUTES,
    AuthFrameInspection,
    inspect_auth_frame,
)


#: Suffixes a browser writes while a download is still in flight. Reading one
#: would look like a malformed frame and produce a misleading rejection.
PARTIAL_SUFFIXES = frozenset({".tmp", ".crdownload", ".part", ".partial", ".download"})

#: Extensions the official workflow actually produces.
#:
#: The real production frame is a **.txt** delimited envelope, not JSON: Rubix
#: price authentication uses tag/value 0x02 and record separator 0x1c, which
#: `_classify_delimited_structure` recognises as PRICE_AUTH_DELIMITED. An
#: inbox that accepted only `.json` would have rejected every genuine frame.
#:
#: The extension is a cheap pre-filter, never the decision: `inspect_auth_frame`
#: remains the sole authority on validity, so a `.txt` full of nonsense is still
#: refused. Acceptance is not broadened beyond what the validator admits.
FRAME_SUFFIXES = frozenset({".json", ".txt"})

#: Refused as an inbox: pointing the watcher at a database directory would
#: invite a frame being written next to production data.
PROTECTED_INBOX_NAMES = frozenset(
    {"data", "backups", "logs", "reports", "venv", ".git"}
)

#: The user's real, long-established export target.
#:
#: The Rubix authentication export has always written here, so the assisted
#: mode watches this file **in place**. An earlier version watched a
#: repository inbox instead, which added a manual copy step to a workflow that
#: never had one. Watching the real file removes that step entirely: nothing is
#: copied into the repository, moved to a consumed folder, or deleted.
DEFAULT_AUTH_FRAME_PATH = Path(r"C:\secure-temp\rubix-price-auth-frame.txt")


class FrameSelection(str, Enum):
    """Why the watcher did or did not choose a frame."""

    WAITING_FOR_FRESH_AUTH_FRAME = "WAITING_FOR_FRESH_AUTH_FRAME"
    FRAME_SELECTED = "FRAME_SELECTED"
    #: Several equally-fresh valid frames; the user must disambiguate.
    AMBIGUOUS_REQUIRES_USER_SELECTION = "AMBIGUOUS_REQUIRES_USER_SELECTION"
    INBOX_UNAVAILABLE = "INBOX_UNAVAILABLE"


class FrameStatus(str, Enum):
    """What the window shows about the auth frame, in the user's terms.

    Kept separate from `FrameSelection`, which is about *which* file to use:
    with a single watched file there is nothing to select between, but the user
    still needs to know whether it is usable.
    """

    WAITING_FOR_FRESH_AUTH_FRAME = "WAITING_FOR_FRESH_AUTH_FRAME"
    AUTH_FRAME_VALID = "AUTH_FRAME_VALID"
    AUTH_FRAME_EXPIRED = "AUTH_FRAME_EXPIRED"
    AUTH_FRAME_REJECTED = "AUTH_FRAME_REJECTED"


class AssistedState(str, Enum):
    """Lifecycle of one assisted start."""

    STARTING = "STARTING"
    RUNNING_HEALTHY = "RUNNING_HEALTHY"
    RUNNING_SOURCE_STALE = "RUNNING_SOURCE_STALE"
    AUTH_FRAME_REJECTED = "AUTH_FRAME_REJECTED"
    INSTANCE_ALREADY_RUNNING = "INSTANCE_ALREADY_RUNNING"
    START_FAILED = "START_FAILED"
    HEALTH_TIMEOUT = "HEALTH_TIMEOUT"


@dataclass(frozen=True)
class FrameCandidate:
    """One inbox file plus the official validator's verdict. Never its contents."""

    path: Path
    inspection: AuthFrameInspection

    @property
    def valid(self) -> bool:
        return self.inspection.valid

    @property
    def authoritative_timestamp(self) -> datetime | None:
        """The frame's own timestamp when present; mtime is only fallback.

        The brief is explicit that the validated internal timestamp governs
        freshness. Filesystem time is trivially changed by a copy or a sync
        client, so it is supporting evidence, never the decision.
        """

        # Delimited PRICE_AUTH_DELIMITED frames carry no embedded timestamp,
        # so filesystem mtime is the only freshness signal available for them.
        # JSON frames that do carry one always take precedence.
        embedded = self.inspection.embedded_timestamp_utc
        if embedded:
            try:
                return datetime.fromisoformat(embedded)
            except ValueError:  # pragma: no cover - validator normalises this
                return None
        mtime = self.inspection.file_mtime_utc
        if mtime:
            try:
                return datetime.fromisoformat(mtime)
            except ValueError:  # pragma: no cover
                return None
        return None

    @property
    def uses_embedded_timestamp(self) -> bool:
        return bool(self.inspection.embedded_timestamp_found)

    def age_seconds(self, now: datetime) -> float | None:
        stamp = self.authoritative_timestamp
        return None if stamp is None else (now - stamp).total_seconds()

    def remaining_seconds(self, now: datetime, max_age_minutes: float) -> float | None:
        age = self.age_seconds(now)
        return None if age is None else max_age_minutes * 60.0 - age

    def summary(self, now: datetime, max_age_minutes: float) -> dict:
        """Display-safe. Carries no field that could contain a secret."""

        age = self.age_seconds(now)
        remaining = self.remaining_seconds(now, max_age_minutes)
        return {
            "filename": self.path.name,
            "validated_timestamp_utc": (
                self.authoritative_timestamp.isoformat()
                if self.authoritative_timestamp
                else None
            ),
            "timestamp_source": (
                "frame_internal" if self.uses_embedded_timestamp else "file_mtime"
            ),
            "age_seconds": None if age is None else round(age, 1),
            "remaining_seconds": None if remaining is None else round(remaining, 1),
            "validation_result": self.inspection.status,
            "rejection_reason": None if self.valid else self.inspection.message,
        }


@dataclass(frozen=True)
class InboxScan:
    selection: FrameSelection
    selected: FrameCandidate | None
    candidates: tuple[FrameCandidate, ...]
    rejected: tuple[FrameCandidate, ...]
    detail: str

    @property
    def ready(self) -> bool:
        return self.selection is FrameSelection.FRAME_SELECTED

    @property
    def status(self) -> FrameStatus:
        """The four states the window displays.

        Expired and rejected are kept apart because they mean different things
        to the user: re-export, versus something is wrong with the export.
        """

        if self.selection is FrameSelection.FRAME_SELECTED:
            return FrameStatus.AUTH_FRAME_VALID
        # Distinguish "the export is stale" from "the export is broken" using
        # the official validator's own verdict, never a local re-derivation.
        # The validator's own constants, imported rather than spelled out: it
        # reports "EXPIRED"/"INVALID", and a hardcoded guess silently degrades
        # every expired frame to WAITING, hiding the one state the user can act on.
        statuses = {item.inspection.status for item in self.candidates}
        if statuses and statuses <= {AUTH_EXPIRED}:
            return FrameStatus.AUTH_FRAME_EXPIRED
        if AUTH_INVALID in statuses:
            return FrameStatus.AUTH_FRAME_REJECTED
        if self.selection is FrameSelection.AMBIGUOUS_REQUIRES_USER_SELECTION:
            return FrameStatus.AUTH_FRAME_REJECTED
        return FrameStatus.WAITING_FOR_FRESH_AUTH_FRAME


def resolve_inbox(root: Path, configured: str | os.PathLike) -> Path:
    """Resolve the inbox safely, or refuse.

    Refuses traversal outside the project, and refuses directories that hold
    production data — an auth frame must never be watched for next to a
    database.
    """

    candidate = Path(configured)
    if not candidate.is_absolute():
        candidate = Path(root) / candidate
    resolved = candidate.resolve()
    root_resolved = Path(root).resolve()
    try:
        relative = resolved.relative_to(root_resolved)
    except ValueError as error:
        raise ValueError(
            f"auth-frame inbox must stay inside the project root: {resolved}"
        ) from error
    parts = relative.parts
    if not parts:
        raise ValueError("auth-frame inbox cannot be the project root itself")
    if len(parts) == 1 and parts[0] in PROTECTED_INBOX_NAMES:
        raise ValueError(
            f"refusing a top-level protected directory as the inbox: {resolved}"
        )
    if resolved.is_file():
        raise ValueError(f"auth-frame inbox must be a directory: {resolved}")
    return resolved


def _is_candidate_file(path: Path, inbox: Path) -> bool:
    """Regular JSON files that genuinely live inside the inbox."""

    if path.suffix.lower() in PARTIAL_SUFFIXES:
        return False
    if path.suffix.lower() not in FRAME_SUFFIXES:
        return False
    if path.is_symlink():
        # A symlink or junction could point anywhere; resolve and require it to
        # still sit inside the inbox.
        try:
            target = path.resolve()
            target.relative_to(inbox.resolve())
        except (OSError, ValueError):
            return False
    if not path.is_file():
        return False
    return True


def scan_frame_file(
    frame_path: Path,
    *,
    now: datetime | None = None,
    max_age_minutes: float = DEFAULT_MAX_AGE_MINUTES,
) -> InboxScan:
    """Validate one known auth frame **in place**. The production path.

    The user's export has always written to one well-known file, so there is
    nothing to select between and no reason to relocate anything. This reads
    the file where it already is and asks the existing official validator for
    a verdict — it never copies, moves, renames or deletes it, and it never
    returns or records its contents.

    A missing file is not an error: at 09:10 the morning export simply has not
    happened yet, which is `WAITING_FOR_FRESH_AUTH_FRAME`.

    Freshness follows the official contract. `inspect_auth_frame` is re-run on
    every poll, so overwriting the file with a newer export is picked up on the
    next pass without any bookkeeping here.
    """

    moment = now or datetime.now(timezone.utc)
    path = Path(frame_path)
    if not path.is_file():
        return InboxScan(
            FrameSelection.WAITING_FOR_FRESH_AUTH_FRAME, None, (), (),
            f"no auth frame yet at {path}",
        )

    inspection = inspect_auth_frame(path, now=moment, max_age_minutes=max_age_minutes)
    candidate = FrameCandidate(path=path, inspection=inspection)
    if candidate.valid:
        return InboxScan(
            FrameSelection.FRAME_SELECTED, candidate, (candidate,), (),
            "auth frame is valid and fresh",
        )
    return InboxScan(
        FrameSelection.WAITING_FOR_FRESH_AUTH_FRAME, None, (candidate,), (candidate,),
        f"auth frame present but not usable: {inspection.status}",
    )


def scan_inbox(
    inbox: Path,
    *,
    now: datetime | None = None,
    max_age_minutes: float = DEFAULT_MAX_AGE_MINUTES,
    ambiguity_window_seconds: float = 2.0,
) -> InboxScan:
    """Find the one frame to use, or explain why there isn't one.

    Selection rules, in order:

    * no valid frame  -> ``WAITING_FOR_FRESH_AUTH_FRAME``
    * exactly one     -> selected
    * several, one clearly newest by validated timestamp -> selected
    * several indistinguishably recent -> ``AMBIGUOUS_REQUIRES_USER_SELECTION``

    "Indistinguishable" is deliberate: two frames whose validated timestamps sit
    within ``ambiguity_window_seconds`` cannot be ordered with confidence, and
    silently picking one could start a session against the wrong credential.
    """

    moment = now or datetime.now(timezone.utc)
    if not inbox.is_dir():
        return InboxScan(
            FrameSelection.INBOX_UNAVAILABLE, None, (), (),
            f"inbox directory does not exist: {inbox}",
        )

    candidates: list[FrameCandidate] = []
    for path in sorted(inbox.iterdir(), key=lambda item: item.name):
        if not _is_candidate_file(path, inbox):
            continue
        inspection = inspect_auth_frame(
            path, now=moment, max_age_minutes=max_age_minutes
        )
        candidates.append(FrameCandidate(path=path, inspection=inspection))

    valid = [item for item in candidates if item.valid]
    rejected = tuple(item for item in candidates if not item.valid)

    if not valid:
        detail = (
            "no auth-frame files found in the inbox"
            if not candidates
            else f"{len(rejected)} file(s) present, none currently valid"
        )
        return InboxScan(
            FrameSelection.WAITING_FOR_FRESH_AUTH_FRAME, None,
            tuple(candidates), rejected, detail,
        )

    if len(valid) == 1:
        return InboxScan(
            FrameSelection.FRAME_SELECTED, valid[0], tuple(candidates), rejected,
            "exactly one valid frame",
        )

    # Order by the authoritative timestamp. A frame with no usable timestamp
    # cannot participate in ordering, so its presence makes the set ambiguous.
    stamped = [(item.authoritative_timestamp, item) for item in valid]
    if any(stamp is None for stamp, _ in stamped):
        return InboxScan(
            FrameSelection.AMBIGUOUS_REQUIRES_USER_SELECTION, None,
            tuple(candidates), rejected,
            "a valid frame has no usable timestamp; ordering is not deterministic",
        )
    stamped.sort(key=lambda pair: pair[0], reverse=True)
    newest_stamp, newest = stamped[0]
    runner_up_stamp = stamped[1][0]
    if (newest_stamp - runner_up_stamp).total_seconds() < ambiguity_window_seconds:
        return InboxScan(
            FrameSelection.AMBIGUOUS_REQUIRES_USER_SELECTION, None,
            tuple(candidates), rejected,
            f"{len(valid)} valid frames within {ambiguity_window_seconds:g}s; "
            "select one explicitly",
        )
    return InboxScan(
        FrameSelection.FRAME_SELECTED, newest, tuple(candidates), rejected,
        f"newest of {len(valid)} valid frames by validated timestamp",
    )


@dataclass(frozen=True)
class CollectorCommand:
    """The collector-only command. Mirrors the launcher's own, minus the app."""

    executable: str
    arguments: tuple[str, ...]
    working_directory: str

    def as_list(self) -> list[str]:
        return [self.executable, *self.arguments]


def build_collector_command(
    *,
    python_executable: str | os.PathLike,
    project_root: Path,
    adapter_path: Path,
    auth_frame: Path,
    database: Path,
    symbols_path: Path,
    pid_file: Path,
    lock_file: Path,
    log_file: Path,
    batch_size: int = 100,
) -> CollectorCommand:
    """Exactly the launcher's supervisor invocation — and nothing else.

    No Streamlit, no dashboard, no second launcher UI, no ORB, no broker path.
    """

    supervisor = Path(project_root) / "scripts" / "rubix_collector_supervisor.py"
    return CollectorCommand(
        executable=str(python_executable),
        arguments=(
            str(supervisor),
            "--adapter", str(adapter_path),
            "--auth-frame-file", str(auth_frame),
            "--database", str(database),
            "--symbols", str(symbols_path),
            "--batch-size", str(int(batch_size)),
            "--pid-file", str(pid_file),
            "--lock-file", str(lock_file),
            "--log-file", str(log_file),
        ),
        working_directory=str(project_root),
    )


@dataclass
class CountdownState:
    """Opt-in auto-start countdown. Disabled unless explicitly enabled."""

    enabled: bool = False
    seconds: float = 5.0
    remaining: float = 5.0
    cancelled: bool = False
    started: bool = False

    def begin(self) -> None:
        self.remaining = self.seconds
        self.cancelled = False
        self.started = False

    def cancel(self) -> None:
        self.cancelled = True

    def tick(self, delta: float = 1.0) -> bool:
        """Advance. Returns True only on the tick that should fire the start."""

        if not self.enabled or self.cancelled or self.started:
            return False
        self.remaining = max(0.0, self.remaining - float(delta))
        if self.remaining <= 0.0:
            self.started = True
            return True
        return False


def countdown_should_abort(scan: InboxScan) -> str | None:
    """A countdown must abort the moment its premise stops holding."""

    if scan.selection is not FrameSelection.FRAME_SELECTED:
        return f"selection changed to {scan.selection.value}"
    if scan.selected is not None and not scan.selected.valid:
        return "selected frame is no longer valid"
    return None


@dataclass(frozen=True)
class HealthOutcome:
    state: AssistedState
    detail: str
    checks: tuple[tuple[str, bool, str], ...] = ()


def evaluate_health(
    readiness: dict,
    *,
    supervisor_running: bool,
    elapsed_seconds: float,
    timeout_seconds: float,
) -> HealthOutcome:
    """A live process alone is never `RUNNING_HEALTHY`.

    Health requires the *source* to be advancing, which is the only evidence
    that the collector is doing its job rather than merely existing.
    """

    checks = tuple(
        (item["name"], bool(item["ok"]), str(item["detail"]))
        for item in readiness.get("checks", [])
    )
    named = {name: (ok, detail) for name, ok, detail in checks}

    if not supervisor_running:
        if elapsed_seconds >= timeout_seconds:
            return HealthOutcome(
                AssistedState.HEALTH_TIMEOUT,
                f"supervisor did not become healthy within {timeout_seconds:g}s",
                checks,
            )
        return HealthOutcome(AssistedState.STARTING, "supervisor is starting", checks)

    progressing_ok, progressing_detail = named.get(
        "source_progressing", (False, "not evaluated")
    )
    database_ok, database_detail = named.get(
        "source_database", (False, "not evaluated")
    )
    if not database_ok:
        if elapsed_seconds >= timeout_seconds:
            return HealthOutcome(
                AssistedState.HEALTH_TIMEOUT, f"source unusable: {database_detail}",
                checks,
            )
        return HealthOutcome(AssistedState.STARTING, database_detail, checks)
    if progressing_ok:
        return HealthOutcome(
            AssistedState.RUNNING_HEALTHY, progressing_detail, checks
        )
    if elapsed_seconds >= timeout_seconds:
        return HealthOutcome(
            AssistedState.RUNNING_SOURCE_STALE,
            f"supervisor is running but the source is not advancing: {progressing_detail}",
            checks,
        )
    return HealthOutcome(AssistedState.STARTING, progressing_detail, checks)


class FrameDisposal(str, Enum):
    """What happens to the frame after a successful start.

    The default is ``LEAVE_UNTOUCHED``: the existing security contract treats
    the frame as a short-lived file the user owns, and moving or deleting it by
    default would be this code taking custody of credential-adjacent material
    it has no business handling. Both other options are explicit opt-ins.
    """

    LEAVE_UNTOUCHED = "LEAVE_UNTOUCHED"
    MOVE_TO_CONSUMED = "MOVE_TO_CONSUMED"
    DELETE = "DELETE"


def dispose_frame(
    frame: Path, disposal: FrameDisposal, *, consumed_dir: Path | None = None
) -> str:
    """Never copies the frame anywhere it could be reused as a credential."""

    if disposal is FrameDisposal.LEAVE_UNTOUCHED:
        return "frame left untouched (default)"
    if disposal is FrameDisposal.DELETE:
        frame.unlink(missing_ok=True)
        return "frame deleted at explicit user request"
    if consumed_dir is None:
        raise ValueError("MOVE_TO_CONSUMED requires a consumed directory")
    consumed_dir.mkdir(parents=True, exist_ok=True)
    destination = consumed_dir / frame.name
    frame.replace(destination)
    return f"frame moved to {consumed_dir.name}/ (ignored, not reusable)"


__all__ = [
    "DEFAULT_AUTH_FRAME_PATH",
    "FRAME_SUFFIXES",
    "PARTIAL_SUFFIXES",
    "PROTECTED_INBOX_NAMES",
    "AssistedState",
    "CollectorCommand",
    "CountdownState",
    "FrameCandidate",
    "FrameDisposal",
    "FrameSelection",
    "FrameStatus",
    "HealthOutcome",
    "InboxScan",
    "build_collector_command",
    "countdown_should_abort",
    "dispose_frame",
    "evaluate_health",
    "resolve_inbox",
    "scan_frame_file",
    "scan_inbox",
]
