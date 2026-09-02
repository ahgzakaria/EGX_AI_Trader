"""Production supervisor for the independent Rubix SQLite collector.

The supervisor never authenticates to Rubix itself.  It passes a fresh,
user-supplied temporary frame file to the external adapter, redacts child
output, restarts failures, and publishes non-secret local health evidence.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import traceback


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.symbols import load_symbols
from providers.rubix_subscription import build_rubix_subscription_plan
from providers.rubix_sqlite_provider import RubixSQLiteProvider
from scripts.launcher_process_utils import (
    InstanceAlreadyRunning,
    SingleInstanceLock,
)
from core.universe import UNIVERSE_SOURCE


FEED_URL = "wss://eg-feed3.mubashertrade.com/websocket/price"
STOP_REQUESTED = False
SECRET_PATTERN = re.compile(
    r"(?i)(token|cookie|authorization|auth[_ -]?frame)\s*[:=]\s*\S+"
)


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    os.replace(temporary, path)


def redact(value: str) -> str:
    value = SECRET_PATTERN.sub(r"\1=[REDACTED]", str(value or ""))
    return re.sub(r"(?i)(--auth-frame-file)\s+\S+", r"\1 [REDACTED]", value)


class StructuredLog:
    def __init__(self, path: Path, max_bytes=5_000_000, backups=5):
        self.path = path
        self.max_bytes = int(max_bytes)
        self.backups = int(backups)
        path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: str, **fields) -> None:
        self._rotate()
        payload = {
            "timestamp": datetime.now(timezone.utc).astimezone().isoformat(),
            "event": event,
            **{key: redact(value) if isinstance(value, str) else value for key, value in fields.items()},
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    def _rotate(self):
        if not self.path.exists() or self.path.stat().st_size < self.max_bytes:
            return
        oldest = self.path.with_suffix(self.path.suffix + f".{self.backups}")
        oldest.unlink(missing_ok=True)
        for index in range(self.backups - 1, 0, -1):
            source = self.path.with_suffix(self.path.suffix + f".{index}")
            if source.exists():
                os.replace(source, self.path.with_suffix(self.path.suffix + f".{index + 1}"))
        os.replace(self.path, self.path.with_suffix(self.path.suffix + ".1"))


def validate_auth_file(path: Path, max_age_minutes=15) -> Path:
    path = path.expanduser().resolve()
    if not path.is_file() or not 0 < path.stat().st_size <= 1_000_000:
        raise ValueError("A non-empty temporary authentication-frame file is required")
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    age = (datetime.now(timezone.utc) - modified).total_seconds() / 60
    if age < -1 or age > float(max_age_minutes):
        raise ValueError(f"Authentication-frame file is {max(age, 0):.1f} minutes old")
    return path


def validate_adapter(path: Path) -> Path:
    path = path.expanduser().resolve()
    missing = [name for name in ("adapter.py", "cli.py", "storage.py", "protocol.py") if not (path / name).is_file()]
    if missing:
        raise ValueError("Rubix adapter is incomplete: " + ", ".join(missing))
    return path


#: A WAL past this size is not a busy session, it is one that never reset. A
#: full EGX day writes on the order of a gigabyte; 45 GB is what accumulated
#: while a health() poll held a read snapshot almost continuously, because
#: SQLite cannot reuse the file while any reader still needs the old frames.
WAL_RECLAIM_THRESHOLD_BYTES = 4 * 1024 ** 3


def database_maintenance(path: Path, *, integrity: bool = True,
                         truncate: bool = False, timeout: float = 15.0) -> dict:
    """Checkpoint the WAL, and optionally verify every page.

    The two halves cost wildly different amounts. ``wal_checkpoint(PASSIVE)``
    writes back only what the WAL holds, so it is proportional to recent
    activity. ``integrity_check`` reads and verifies *every page of the
    database*, so it is proportional to the whole file -- 5.84 GB of it.

    Measured from this supervisor's own log on 2026-08-24, from the gaps
    between maintenance events against the 300 s schedule: after the close the
    check took 33-63 s, but **during the session, with the collector writing
    concurrently, it took 233-635 s**. One took ten and a half minutes. The
    supervisor was spending roughly seventy percent of the trading session
    reading a six-gigabyte file at over 100 MB/s, which is what made the
    machine heavy at exactly the hours it needed to be responsive.

    Nothing consumed the periodic verdict. It went to a log line and the health
    file; System Health and the scalping page run their own checks on demand.
    So it now runs where it is worth its cost -- at startup, before the day's
    data is relied on, and at shutdown -- and the periodic pass checkpoints
    only.
    """
    if not path.is_file():
        return {"integrity": "MISSING", "wal_checkpoint": None, "bytes": 0}
    try:
        with sqlite3.connect(path, timeout=timeout) as connection:
            verdict = (
                connection.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity else "NOT_CHECKED"
            )
            # PASSIVE checkpoints the frames but leaves the file at its
            # high-water mark. Measured directly: with journal_size_limit set,
            # PASSIVE and RESTART both left a 4,301,312-byte WAL untouched and
            # only TRUNCATE returned it to zero. journal_size_limit is not the
            # lever here, whatever it reads like -- TRUNCATE is.
            #
            # It is also why the live WAL reached 30.73 GB against a 5.85 GB
            # database: every stop before 2026-08-24 was a kill, so the shutdown
            # checkpoint never ran at all, and nothing ever truncated. TRUNCATE
            # waits for readers, so it belongs at shutdown once the child is
            # stopped -- never mid-session, where waiting is a stall in the one
            # process that must not stall.
            mode = "TRUNCATE" if truncate else "PASSIVE"
            checkpoint = connection.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
        return {
            "integrity": verdict,
            "wal_checkpoint": list(checkpoint) if checkpoint else None,
            "bytes": path.stat().st_size,
        }
    except sqlite3.Error as error:
        return {"integrity": "ERROR", "error": str(error), "bytes": path.stat().st_size}


class CollectorSupervisor:
    def __init__(self, args):
        self.args = args
        self.adapter = validate_adapter(Path(args.adapter))
        self.auth_file = validate_auth_file(Path(args.auth_frame_file), args.auth_max_age_minutes)
        self.database = Path(args.database).expanduser().resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.log = StructuredLog(Path(args.log_file), args.log_max_bytes, args.log_backups)
        self.pid_file = Path(args.pid_file)
        self.lock_file = Path(getattr(args, "lock_file", None)
                              or self.pid_file.with_suffix(".lock"))
        self.lock = None
        self.health_file = Path(args.health_file)
        self.child = None
        self.restarts = 0
        self.started_at = time.monotonic()
        # Wall clock, kept separately: started_at is monotonic and measures
        # uptime, so comparing it against a file's mtime compares two different
        # clocks and the staleness test silently always -- or never -- fires.
        self.started_wall = time.time()
        self.stop_file = Path(getattr(args, "stop_file", None)
                              or PROJECT_ROOT / "data" / "runtime" / "stop_requested.flag")
        self.last_maintenance = 0.0
        # Zero, so the first pass through the loop always publishes: a
        # supervisor that has just started is exactly when someone is looking.
        self.last_health_write = 0.0
        self.health_seconds = float(getattr(args, "health_seconds", None) or 30)
        plan = build_rubix_subscription_plan(args.symbols, args.batch_size)
        if plan.invalid:
            raise ValueError(f"Invalid Rubix symbol mappings: {len(plan.invalid)}")
        self.symbols = list(plan.subscriptions)
        self.requested_symbols = list(plan.requested)

    def child_command(self):
        command = [
            sys.executable, "-m", "rubix_feed.cli", "--url", FEED_URL,
            "--auth-frame-file", str(self.auth_file), "--database", str(self.database),
            "--stale-seconds", str(self.args.stale_seconds),
            "--subscription-batch-size", str(self.args.batch_size),
            "--authenticated-session",
        ]
        for symbol in self.symbols:
            command.extend(("--symbol", symbol))
        return command

    def start_child(self):
        # Never run two collectors under one supervisor: a live child means the
        # existing session already owns the credential.
        if self.child is not None and self.child.poll() is None:
            self.log.emit("collector_start_skipped", reason="child already running",
                          pid=self.child.pid)
            return
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(self.adapter.parent) + os.pathsep + environment.get("PYTHONPATH", "")
        self.child = subprocess.Popen(
            self.child_command(), cwd=str(self.adapter.parent), env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=self._drain_child_output, args=(self.child,), daemon=True).start()
        self.log.emit("collector_started", pid=self.child.pid, symbols=len(self.symbols), restart=self.restarts)

    def _drain_child_output(self, child):
        if child.stdout is None:
            return
        for line in child.stdout:
            self.log.emit("collector_output", message=redact(line.rstrip()))

    def health(self):
        provider = RubixSQLiteProvider(
            self.database,
            stale_after_minutes=float(self.args.stale_seconds) / 60,
            bar_stale_after_minutes=float(self.args.bar_stale_seconds) / 60,
            expected_symbols=self.requested_symbols,
        )
        health = provider.health()
        health.update({
            "supervisor_pid": os.getpid(),
            "collector_pid": self.child.pid if self.child and self.child.poll() is None else None,
            "collector_alive": bool(self.child and self.child.poll() is None),
            "restart_count": self.restarts,
            "supervisor_uptime_seconds": round(time.monotonic() - self.started_at, 1),
            "auth_file_persisted": True,
            "auth_contents_logged": False,
        })
        return health

    def publish_health(self, **extra):
        """Write the health file and reset its timer. See the run loop."""

        record = self.health()
        if extra:
            record.update(extra)
        atomic_json(self.health_file, record)
        self.last_health_write = time.monotonic()
        return record

    def stop_child(self):
        if self.child is None or self.child.poll() is not None:
            return
        self.child.terminate()
        try:
            self.child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.child.kill()
            self.child.wait(timeout=5)
        self.log.emit("collector_stopped", return_code=self.child.returncode)

    def reclaim_wal(self, *, budget_seconds: float = 120.0,
                    attempt_timeout: float = 15.0) -> None:
        """Truncate the write-ahead log before the collector starts writing.

        Only TRUNCATE returns the WAL file to zero; PASSIVE and RESTART write
        the frames back and leave the file at its high-water mark. The periodic
        maintenance runs PASSIVE, so the WAL's *contents* are flushed every five
        minutes all session -- but the file only ever grows.

        Truncating was therefore put in the shutdown path, and the shutdown path
        does not run: this supervisor is killed, its health file has never
        carried `shutdown: true`. The result is on record twice. The comment in
        database_maintenance describes the WAL reaching 30.73 GB against a
        5.85 GB database for exactly this reason, and on 2026-09-01 it was
        **43,074 MB against 7,655 MB** -- six times the database.

        That is not a housekeeping detail. Every reader must traverse the WAL to
        build a consistent view, so the whole application slows with it:
        `SELECT ticker FROM quotes GROUP BY ticker` went from 2.9 seconds to
        over 280, and a Daily Dashboard scan could no longer get past loading
        its quote overlays.

        Startup is the one moment this is both safe and cheap. TRUNCATE waits
        for readers, which is why it must never run mid-session -- but here the
        collector has not been started yet, so there is nothing to wait for.
        And because the periodic PASSIVE checkpoints already wrote the frames
        back, there is little left to do beyond resetting the file.

        It is reported, never enforced: a busy result is logged and the
        collector starts regardless. A supervisor that refused to start because
        it could not tidy up would be a worse failure than the untidiness.

        One attempt is not enough, which the first live run proved. "Nothing to
        wait for before start_child" was wrong: the *launcher* reads this
        database too, on a health() timer, and one of its calls spanned the
        whole attempt. The truncate found the file locked, gave up after its
        15-second timeout, and logged 45,165,903,792 bytes before and after. So
        it now retries within a budget rather than taking one refusal as the
        answer.
        """

        if not self.database.is_file():
            return
        before = self.wal_bytes()
        if before == 0:
            # Nothing to reclaim, and nothing to wait for. Without this the
            # retry below compared `wal_bytes() < before` -- 0 < 0 is false --
            # and spent its whole budget confirming an empty file, delaying
            # every collector start by two minutes. At 09:45 that is the
            # opening auction, which is the exact cost this was written to
            # avoid.
            return
        started = time.monotonic()
        deadline = started + max(0.0, float(budget_seconds))
        attempts, result = 0, {}
        while True:
            attempts += 1
            try:
                result = database_maintenance(
                    self.database, integrity=False, truncate=True,
                    timeout=max(1.0, float(attempt_timeout)))
            except Exception as error:                   # noqa: BLE001 - never block the start
                self.log.emit("wal_reclaim_failed", error=str(error),
                              attempts=attempts)
                return
            # A checkpoint that reports busy=0 completed; that is the success
            # signal, not the file size. Retrying is only for the busy case,
            # which is a reader still holding a snapshot.
            checkpoint = result.get("wal_checkpoint")
            succeeded = bool(checkpoint) and checkpoint[0] == 0
            if succeeded or self.wal_bytes() < before or time.monotonic() >= deadline:
                break
            # A reader still holds a snapshot. On 2026-09-02 that reader was the
            # launcher, which polls health() on a timer and had one call open
            # across the whole first attempt -- so a single 15-second try found
            # the database locked and gave up with the WAL untouched at 45 GB.
            time.sleep(1.0)
        self.log.emit(
            "wal_reclaimed",
            wal_bytes_before=before,
            wal_bytes_after=self.wal_bytes(),
            seconds=round(time.monotonic() - started, 1),
            attempts=attempts,
            checkpoint=result.get("wal_checkpoint"),
        )

    def wal_bytes(self) -> int:
        """Size of the -wal sidecar, or 0 when it does not exist."""

        try:
            return self.database.with_name(self.database.name + "-wal").stat().st_size
        except OSError:
            return 0

    def prune_telemetry(self) -> None:
        """Age out per-tick telemetry, within a budget, on the way out.

        ``feed_metrics`` had reached 44,246,339 rows against 18,249,746 in
        ``quotes`` -- the telemetry two and a half times the data it describes,
        with no index on it. The health snapshot counts those rows on every
        read.

        Bounded on purpose: one call deletes what it can inside its budget and
        the next resumes. The first pass will not catch up on forty million
        rows, and a shutdown that took minutes to do so would be a worse
        problem than the one being fixed.
        """
        if not self.args.prune_telemetry:
            return
        try:
            from services.feed_metrics_retention import prune

            result = prune(
                self.database,
                retention_days=self.args.telemetry_retention_days,
                budget_seconds=self.args.telemetry_prune_seconds,
            )
            self.log.emit("telemetry_pruned", **result.as_log_fields())
        except Exception as error:  # noqa: BLE001 - never cost the checkpoint
            self.log.emit("telemetry_prune_failed", error=str(error))

    def previous_run_shut_down_cleanly(self) -> bool:
        """Did the last supervisor leave through its own shutdown path?

        Only a clean exit writes ``shutdown: true``, and only after the
        database has been checkpointed. A kill, a power cut or a crash leaves
        the flag absent -- which is exactly when the file is worth verifying.

        Unreadable or missing counts as *not* clean: an unknown history is a
        reason to check, never a reason to skip.
        """
        try:
            record = json.loads(self.health_file.read_text(encoding="utf-8"))
            return record.get("shutdown") is True
        except (OSError, ValueError):
            return False

    def verify_database_in_background(self, *, skip: bool) -> None:
        """Verify every page, without holding the collector up.

        ``integrity_check`` reads the whole database -- 148 s for the cheaper
        pragma on 5.84 GB with nothing competing, and 233-635 s during a
        session. On the startup path that is the collector not collecting, so
        it runs on a daemon thread behind a collector that is already up.

        Skipped entirely after a clean shutdown, which verified the same file
        on its way out. Nothing consumes the verdict beyond the log, so the
        cost has to be earned.
        """
        if skip:
            self.log.emit("database_startup_check_skipped",
                          reason="previous run shut down cleanly")
            return

        def verify():
            try:
                self.log.emit("database_startup_check",
                              **database_maintenance(self.database, integrity=True))
            except Exception as error:  # noqa: BLE001 - a check must not kill the run
                self.log.emit("database_startup_check_failed", error=str(error))

        threading.Thread(target=verify, name="startup-integrity", daemon=True).start()

    def stop_requested_on_disk(self):
        """True when something asked for a clean stop by creating the flag.

        Checked once per heartbeat alongside the signal flag, so a stop takes
        effect within a couple of seconds and leaves through the same ``finally``
        every other exit uses -- child stopped, database checkpointed, health
        file marked, lock released.

        A flag written *before* this run started is ignored and deleted. The
        alternative is a stale flag from yesterday's shutdown stopping tomorrow
        morning's collector a second after it starts, which would be a far
        worse failure than the one this exists to fix.
        """
        try:
            if not self.stop_file.exists():
                return False
            if self.stop_file.stat().st_mtime < self.started_wall:
                self.log.emit("stop_flag_stale_ignored", path=str(self.stop_file))
                self.stop_file.unlink(missing_ok=True)
                return False
            self.log.emit("stop_requested_by_flag", path=str(self.stop_file))
            return True
        except OSError as error:
            # An unreadable flag must never take the collector down mid-session.
            self.log.emit("stop_flag_unreadable", error=str(error))
            return False

    def run(self):
        # Atomic OS-level lock held for the whole lifetime: two supervisors can
        # never both hold it (no start-up race), and the OS drops it on exit or
        # crash so a stale lock never blocks a restart. The PID file is metadata.
        self.lock = SingleInstanceLock(
            self.lock_file, label="Rubix collector supervisor", meta_path=self.pid_file,
            extra_meta={"executable": sys.executable,
                        "project_root": str(PROJECT_ROOT),
                        "database": str(self.database)})
        try:
            self.lock.acquire()
        except InstanceAlreadyRunning as error:
            self.log.emit("supervisor_duplicate_blocked", reason=str(error))
            raise
        backoff = float(self.args.restart_backoff_seconds)
        # Read before the loop starts overwriting the health file.
        previous_was_clean = self.previous_run_shut_down_cleanly()
        try:
            self.reclaim_wal()
            self.start_child()
            # Only after the collector is up, and only when corruption is
            # actually plausible. Verifying before start_child held the
            # collector for 156 seconds and counting on a 5.84 GB database --
            # at 09:10 that is the opening auction missed, which is far worse
            # than learning about corruption two minutes later.
            self.verify_database_in_background(skip=previous_was_clean)
            while not STOP_REQUESTED and not self.stop_requested_on_disk():
                # The heartbeat is two seconds because a dead collector should
                # be restarted quickly and a stop should take effect quickly.
                # Neither of those needs health(): the restart branch below
                # asks the process, not the database, and nothing in this loop
                # reads the dictionary it publishes.
                #
                # health() costs about 220 ms warm during a session -- mostly
                # the newest-candle read -- and about 520 ms outside one, and
                # this supervisor runs all day. Paying it every two seconds
                # meant holding a read snapshot roughly a tenth of the time for
                # a file that is display evidence: System Health embeds it,
                # nothing branches on it, and nothing anywhere measures its age.
                #
                # So it is published on its own timer. State changes do not wait
                # for that timer -- a collector exit publishes immediately below
                # -- so the file is never late about anything that happened,
                # only about how things have stayed.
                if time.monotonic() - self.last_health_write >= self.health_seconds:
                    self.publish_health()
                if self.child.poll() is not None:
                    self.log.emit("collector_exit", return_code=self.child.returncode)
                    if self.restarts >= self.args.max_restarts:
                        self.log.emit("supervisor_failed", reason="restart limit reached")
                        self.publish_health()
                        return 2
                    self.restarts += 1
                    time.sleep(min(backoff * self.restarts, 60))
                    self.start_child()
                    # The restart is the event worth recording, and the next
                    # scheduled write could be half a minute away.
                    self.publish_health()
                now = time.monotonic()
                if now - self.last_maintenance >= self.args.maintenance_seconds:
                    # Checkpoint only. The full verification runs at startup and
                    # at shutdown; running it here consumed most of the session.
                    self.log.emit("database_maintenance",
                                  **database_maintenance(self.database, integrity=False))
                    # Pruning belongs here as well as in the shutdown path,
                    # because the shutdown path does not run: it is an exit
                    # handler, and a killed process never reaches one. This
                    # supervisor is killed rather than asked to stop -- the
                    # health file has never carried `shutdown: true`, and no log
                    # in this project has ever contained a `telemetry_pruned`
                    # event.
                    #
                    # So the pruner written for this table has never executed
                    # once. feed_metrics went from 44,246,339 rows on
                    # 2026-08-25 to 55,771,859 on 2026-09-01 -- 1.6 million a
                    # day, and now larger than the quotes table it describes.
                    #
                    # It is safe to call on a schedule: it deletes in bounded
                    # batches inside a time budget and reports whether it
                    # finished, and it was designed to resume on the next call
                    # rather than to complete in one.
                    self.prune_telemetry()
                    # A WAL this far past a normal session's size is not going
                    # to come back on its own, and waiting for the next startup
                    # means carrying it for the rest of the day -- which is what
                    # made every read on 2026-09-02 slow. One short attempt: it
                    # either succeeds because nothing is reading right now, or
                    # it reports busy and the loop moves on. The timeout is what
                    # keeps the promise never to stall the supervisor here.
                    if self.wal_bytes() > WAL_RECLAIM_THRESHOLD_BYTES:
                        self.reclaim_wal(budget_seconds=0.0, attempt_timeout=5.0)
                    # Taken after the work, not before: on a slow checkpoint the
                    # next one should be a full interval away, not immediate.
                    self.last_maintenance = time.monotonic()
                time.sleep(self.args.heartbeat_seconds)
            return 0
        finally:
            self.stop_child()
            # The child is stopped by now, so nothing else holds the file and
            # the WAL can actually be returned to zero.
            #
            # Pruning runs first and the checkpoint second, deliberately: the
            # deletes are themselves journalled, so truncating before them
            # would leave the freed space in the WAL instead of reclaiming it.
            self.prune_telemetry()
            maintenance = database_maintenance(self.database, truncate=True)
            self.log.emit("supervisor_shutdown", **maintenance)
            self.publish_health(shutdown=True, database=maintenance)
            if self.lock is not None:
                self.lock.release()                      # OS lock + metadata file


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--auth-frame-file", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--symbols", default=str(PROJECT_ROOT / UNIVERSE_SOURCE))
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--stale-seconds", type=int, default=60)
    parser.add_argument("--bar-stale-seconds", type=int, default=120)
    parser.add_argument("--auth-max-age-minutes", type=int, default=15)
    parser.add_argument("--heartbeat-seconds", type=float, default=2)
    # Deliberately not the heartbeat: see the run loop.
    parser.add_argument("--health-seconds", type=float, default=30)
    parser.add_argument("--maintenance-seconds", type=float, default=300)
    parser.add_argument("--max-restarts", type=int, default=20)
    parser.add_argument("--restart-backoff-seconds", type=float, default=2)
    parser.add_argument("--pid-file", default=str(PROJECT_ROOT / "data" / "rubix_supervisor.pid.json"))
    parser.add_argument("--lock-file", default=str(PROJECT_ROOT / "data" / "rubix_supervisor.lock"))
    parser.add_argument("--health-file", default=str(PROJECT_ROOT / "data" / "rubix_supervisor_health.json"))
    parser.add_argument("--log-file", default=str(PROJECT_ROOT / "logs" / "rubix_supervisor.log"))
    parser.add_argument("--log-max-bytes", type=int, default=5_000_000)
    parser.add_argument("--log-backups", type=int, default=5)
    # A file another process can create to ask for a clean stop. Windows does
    # not deliver SIGTERM, and closing the console window kills the process
    # outright: in 3516 log lines this supervisor had never once reached its
    # own shutdown path, so every stop skipped the final checkpoint and left
    # the health file claiming the collector was still up.
    parser.add_argument("--stop-file",
                        default=str(PROJECT_ROOT / "data" / "runtime" / "stop_requested.flag"))
    # Telemetry retention. On by default because leaving it off is what let
    # feed_metrics reach 44 million rows, but every part of it is adjustable
    # and the whole thing is one flag away from off.
    parser.add_argument("--no-prune-telemetry", dest="prune_telemetry",
                        action="store_false",
                        help="keep every per-tick metric row forever")
    parser.add_argument("--telemetry-retention-days", type=int, default=7,
                        help="days of latency/interval/duplicate rows to keep")
    parser.add_argument("--telemetry-prune-seconds", type=float, default=60.0,
                        help="ceiling on how long one shutdown spends pruning")
    return parser


def _request_stop(*_):
    global STOP_REQUESTED
    STOP_REQUESTED = True


def main():
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)
    args = build_parser().parse_args()
    try:
        return CollectorSupervisor(args).run()
    except Exception as error:
        # pythonw has no console. Persist every fatal startup failure with its
        # stage and traceback so the launcher can show a useful diagnosis.
        StructuredLog(Path(args.log_file)).emit(
            "supervisor_fatal",
            exception_type=type(error).__name__,
            message=str(error),
            traceback=traceback.format_exc(),
            supervisor_pid=os.getpid(),
            working_directory=str(Path.cwd()),
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
