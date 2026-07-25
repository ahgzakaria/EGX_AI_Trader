"""One-window supervisor and manual authentication assistant for Rubix.

The user still authenticates through the official Rubix website.  This
launcher validates a manually exported JSON frame locally, passes only its file
path to the independent collector, and never logs or persists its contents.
"""

from __future__ import annotations

import atexit
import argparse
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
import webbrowser


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
PRODUCTION_DB = PROJECT_ROOT / "data" / "rubix_live_market.db"
DEFAULT_ADAPTER = Path.home() / "OneDrive" / "Documents" / "Scrapping" / "rubix_feed"
FEED_URL = "wss://eg-feed3.mubashertrade.com/websocket/price"
AUTH_MAX_AGE_MINUTES = 15
LAUNCHER_CONFIG = PROJECT_ROOT / "config" / "rubix_launcher_settings.json"
LAUNCHER_LOG = PROJECT_ROOT / "logs" / "rubix_launcher.log"
SUPERVISOR_LOG = PROJECT_ROOT / "logs" / "rubix_supervisor.log"
STREAMLIT_LOG = PROJECT_ROOT / "logs" / "streamlit.log"
LAUNCHER_PID_FILE = PROJECT_ROOT / "data" / "rubix_launcher.pid.json"
SUPERVISOR_PID_FILE = PROJECT_ROOT / "data" / "rubix_supervisor.pid.json"
SUPERVISOR_LOCK_FILE = PROJECT_ROOT / "data" / "rubix_supervisor.lock"
STREAMLIT_PID_FILE = PROJECT_ROOT / "data" / "egx_streamlit.pid.json"

from services.rubix_auth_assistant import (  # noqa: E402 - project root above
    AUTH_EXPIRED,
    AUTH_INVALID,
    AUTH_MISSING,
    AUTH_VALID,
    inspect_auth_frame,
    load_launcher_preferences,
    run_preflight,
    save_launcher_preferences,
    validate_auth_frame_or_raise,
)
from scripts.launcher_process_utils import (  # noqa: E402 - project root above
    InstanceAlreadyRunning,
    claim_pid_file,
    enable_windows_dpi_awareness,
    pid_record_is_current,
    port_is_open,
    process_creation_time,
    read_pid_record,
    release_pid_file,
    safe_window_geometry,
    should_suppress_retry,
    streamlit_is_ready,
    supervisor_status,
    wait_for_streamlit,
)


LOGGER = logging.getLogger("egx.rubix_launcher")


def configure_logging(debug=False):
    """Initialize durable launcher logging before Tk or child startup."""

    if LOGGER.handlers:
        return LOGGER
    LAUNCHER_LOG.parent.mkdir(parents=True, exist_ok=True)
    LOGGER.setLevel(logging.DEBUG if debug else logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s stage=%(stage)s %(message)s"
    )
    handler = RotatingFileHandler(
        LAUNCHER_LOG, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(formatter)
    LOGGER.addHandler(handler)
    if debug:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        LOGGER.addHandler(console)
    return LOGGER


def log_event(stage, message, *, level=logging.INFO, exc_info=False):
    """Write one redacted stage transition to the durable launcher log."""

    configure_logging().log(
        level, redact_log(message), extra={"stage": str(stage)}, exc_info=exc_info
    )


def _safe_command(command):
    """Return a command description with authentication paths removed."""

    safe = []
    redact_next = False
    for item in command:
        text = str(item)
        if redact_next:
            safe.append("[REDACTED]")
            redact_next = False
            continue
        safe.append(text)
        redact_next = text.lower() == "--auth-frame-file"
    return redact_log(subprocess.list2cmdline(safe))


def save_operational_report(prefix, payload):
    """Persist non-secret collector evidence outside the adapter database."""

    directory = PROJECT_ROOT / "reports" / "rubix"
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = directory / f"{prefix}_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def validate_auth_frame(path, now=None, max_age_minutes=AUTH_MAX_AGE_MINUTES):
    """Backward-compatible validation entry point used by the supervisor."""

    return validate_auth_frame_or_raise(
        path, now=now, max_age_minutes=max_age_minutes
    )


def validate_adapter_path(path):
    adapter = Path(path).expanduser().resolve()
    required = ("adapter.py", "cli.py", "protocol.py", "storage.py", "__init__.py")
    missing = [name for name in required if not (adapter / name).is_file()]
    if missing:
        raise ValueError("Rubix collector path is missing: " + ", ".join(missing))
    return adapter


def redact_log(text):
    """Remove common secret-bearing fragments before UI display or log output."""

    value = str(text or "")
    value = re.sub(r"(?i)(token|cookie|authorization|auth[_ -]?frame)\s*[:=]\s*\S+", r"\1=[REDACTED]", value)
    value = re.sub(
        r'(?i)(--auth-frame-file)\s+(?:"[^"]*"|\S+)',
        r"\1 [REDACTED]",
        value,
    )
    return value


def build_collector_command(python, adapter, auth_frame, database, symbols, batch_size=100):
    """Build a list command without shell interpolation or secret content."""

    command = [
        str(python), "-m", "rubix_feed.cli", "--url", FEED_URL,
        "--auth-frame-file", str(auth_frame), "--database", str(database),
        "--stale-seconds", "60", "--subscription-batch-size", str(int(batch_size)),
        "--authenticated-session",
    ]
    for symbol in symbols:
        command.extend(("--symbol", symbol))
    return command


def stop_process(process, timeout=5):
    """Stop a Windows child tree and guarantee no collector grandchild remains."""

    if process is None or process.poll() is not None:
        return
    pid = getattr(process, "pid", None)
    log_event("stop", f"Stopping process tree PID {pid}.")
    if os.name == "nt" and pid:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt" and pid:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            process.kill()
        process.wait(timeout=timeout)


class ProductionSupervisor:
    def __init__(self, adapter_path=DEFAULT_ADAPTER, database=None):
        self.adapter_path = Path(adapter_path)
        self.database = Path(os.getenv("RUBIX_DB_PATH") or database or PRODUCTION_DB)
        self.collector = None
        self.streamlit = None
        self.streamlit_port = None
        self._owns_streamlit = False
        self.messages = queue.Queue()

    @property
    def python(self):
        candidate = PROJECT_ROOT / "venv" / "Scripts" / "python.exe"
        return candidate if candidate.is_file() else Path(sys.executable)

    def start_collector(self, auth_frame, symbols, batch_size=100):
        adapter = validate_adapter_path(self.adapter_path)
        auth_frame = validate_auth_frame(auth_frame)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        # The launcher owns only the supervisor process.  Reconnect,
        # resubscription, health heartbeats and child restarts remain isolated
        # from Streamlit and the trading engine.
        command = [
            str(self.python), str(PROJECT_ROOT / "scripts" / "rubix_collector_supervisor.py"),
            "--adapter", str(adapter), "--auth-frame-file", str(auth_frame),
            "--database", str(self.database),
            "--symbols", str(PROJECT_ROOT / "data" / "symbols.csv"),
            "--batch-size", str(int(batch_size)),
            "--pid-file", str(SUPERVISOR_PID_FILE),
            "--lock-file", str(SUPERVISOR_LOCK_FILE),
            "--log-file", str(SUPERVISOR_LOG),
        ]
        # Do not spawn a second supervisor when a verified live one already exists
        # (one session per credential). The supervisor's OS lock is the ultimate
        # backstop, but refusing here avoids even a doomed second launch.
        status = supervisor_status(SUPERVISOR_PID_FILE)
        if status["running"]:
            raise InstanceAlreadyRunning(
                f"Rubix supervisor is already running (PID {status['pid']})."
            )
        if status["record"]:
            SUPERVISOR_PID_FILE.unlink(missing_ok=True)     # stale metadata (dead PID)
        environment = os.environ.copy()
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        log_event(
            "supervisor_requested",
            f"command={_safe_command(command)} cwd={PROJECT_ROOT}",
        )
        self.collector = subprocess.Popen(
            command, cwd=str(PROJECT_ROOT), env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=flags, bufsize=1,
        )
        log_event("supervisor_started", f"PID {self.collector.pid}.")
        threading.Thread(
            target=self._capture,
            args=(self.collector, "supervisor", None),
            daemon=True,
        ).start()

    def start_streamlit(self, port=8501):
        port = int(port)
        if self.streamlit is not None and self.streamlit.poll() is None:
            return "already_owned"
        marker = read_pid_record(STREAMLIT_PID_FILE)
        if port_is_open(port):
            if pid_record_is_current(marker) and streamlit_is_ready(port):
                self.streamlit_port = port
                self._owns_streamlit = False
                log_event(
                    "streamlit_reused",
                    f"Existing EGX dashboard PID {marker.get('pid')} is ready on port {port}.",
                )
                return "existing"
            raise RuntimeError(
                f"Port {port} is occupied by another process. Stop it or choose another dashboard port."
            )
        if marker:
            STREAMLIT_PID_FILE.unlink(missing_ok=True)
        environment = os.environ.copy()
        environment["RUBIX_DB_PATH"] = str(self.database)
        command = [
            str(self.python), "-m", "streamlit", "run", "app.py",
            "--server.port", str(int(port)), "--server.headless", "true",
        ]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        log_event(
            "streamlit_requested",
            f"command={_safe_command(command)} cwd={PROJECT_ROOT}",
        )
        self.streamlit = subprocess.Popen(
            command, cwd=str(PROJECT_ROOT), env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=flags, bufsize=1,
        )
        self.streamlit_port = port
        self._owns_streamlit = True
        STREAMLIT_PID_FILE.write_text(
            json.dumps(
                {
                    "pid": self.streamlit.pid,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "process_created_at": process_creation_time(self.streamlit.pid),
                    "port": port,
                    "app": str(PROJECT_ROOT / "app.py"),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        log_event("streamlit_started", f"PID {self.streamlit.pid} on port {port}.")
        threading.Thread(
            target=self._capture,
            args=(self.streamlit, "streamlit", STREAMLIT_LOG),
            daemon=True,
        ).start()
        return "started"

    def wait_until_streamlit_ready(self, port=8501, timeout=45):
        if self.streamlit is None:
            if streamlit_is_ready(port):
                return True, f"Existing dashboard is ready on port {int(port)}."
            return False, "Streamlit process was not started."
        ready, detail = wait_for_streamlit(
            int(port), self.streamlit, timeout=float(timeout)
        )
        log_event(
            "streamlit_ready" if ready else "streamlit_failed",
            detail,
            level=logging.INFO if ready else logging.ERROR,
        )
        return ready, detail

    def health(self):
        from config.settings_manager import settings
        from core.symbols import load_symbols
        from providers.rubix_sqlite_provider import RubixSQLiteProvider

        cfg = settings.get("market_data")
        return RubixSQLiteProvider(
            self.database,
            stale_after_minutes=float(cfg.get("rubix_quote_stale_seconds", 60)) / 60,
            bar_stale_after_minutes=float(cfg.get("rubix_bar_stale_seconds", 120)) / 60,
            expected_symbols=load_symbols(PROJECT_ROOT / "data" / "symbols.csv"),
        ).health()

    def wait_until_healthy(self, timeout=90):
        deadline = time.monotonic() + float(timeout)
        latest = {}
        while time.monotonic() < deadline:
            if self.collector is not None and self.collector.poll() is not None:
                return False, latest or {"reason": "collector exited"}
            try:
                latest = self.health()
            except Exception as error:
                latest = {
                    "reason": f"health check failed: {type(error).__name__}: {error}"
                }
            healthy = (
                latest.get("schema_valid") is True
                and latest.get("collector_status") == "CONNECTED"
                and latest.get("authentication_status") == "ACKNOWLEDGED"
                and int(latest.get("symbols_received") or 0) > 0
                and latest.get("status") == "RUBIX_FRESH"
            )
            if healthy:
                return True, latest
            time.sleep(2)
        return False, latest or {"reason": "collector health timeout"}

    def stop(self):
        if self._owns_streamlit:
            stop_process(self.streamlit)
        stop_process(self.collector)
        self.streamlit = self.collector = None
        if self._owns_streamlit:
            release_pid_file(STREAMLIT_PID_FILE, read_pid_record(STREAMLIT_PID_FILE).get("pid"))
        self._owns_streamlit = False
        self.streamlit_port = None

    def _capture(self, process, name, log_path):
        if process.stdout is None:
            return
        handle = None
        try:
            if log_path is not None:
                Path(log_path).parent.mkdir(parents=True, exist_ok=True)
                handle = Path(log_path).open("a", encoding="utf-8")
            for line in process.stdout:
                safe = redact_log(line.rstrip())
                if handle is not None:
                    stamped = f"{datetime.now().astimezone().isoformat()} [{name}] {safe}"
                    handle.write(stamped + "\n")
                    handle.flush()
                else:
                    log_event(f"{name}_output", safe)
                self.messages.put(f"[{name}] {safe}")
        finally:
            if handle is not None:
                handle.close()


class LauncherUI:
    def __init__(self):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = tk.Tk()
        self.root.title("EGX AI Trader — Rubix Production Launcher")
        self.root.geometry("850x650")
        self.supervisor = ProductionSupervisor()
        self.auth_var = tk.StringVar()
        self.adapter_var = tk.StringVar(value=str(DEFAULT_ADAPTER))
        self.db_var = tk.StringVar(value=str(os.getenv("RUBIX_DB_PATH") or PRODUCTION_DB))
        self.status_var = tk.StringVar(value="Stopped")
        self._last_health_check = 0.0
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(500, self._refresh)
        atexit.register(self.supervisor.stop)

    def _build(self):
        from tkinter import filedialog

        frame = self.ttk.Frame(self.root, padding=16)
        frame.pack(fill="both", expand=True)
        self.ttk.Label(frame, text="EGX AI Trader — Rubix Production", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        self.ttk.Label(frame, text="Authentication data is selected for this launch only and is never saved or shown in logs.").pack(anchor="w", pady=(0, 14))
        self.ttk.Label(
            frame,
            text=(
                "Required for Rubix: export the current authenticated price WebSocket frame "
                "to a local text file, then select it below. The file must be less than 15 minutes old."
            ),
            foreground="#8a4b08",
            wraplength=790,
        ).pack(anchor="w", pady=(0, 10))
        fields = self.ttk.Frame(frame)
        fields.pack(fill="x")
        for row, (label, variable, browse) in enumerate((
            ("Fresh auth-frame file (Browse only)", self.auth_var, self._select_auth_file),
            ("Rubix collector folder", self.adapter_var, lambda: self.adapter_var.set(filedialog.askdirectory() or self.adapter_var.get())),
            ("Production database", self.db_var, lambda: self.db_var.set(filedialog.asksaveasfilename(defaultextension=".db") or self.db_var.get())),
        )):
            self.ttk.Label(fields, text=label).grid(row=row, column=0, sticky="w", pady=4)
            entry = self.ttk.Entry(fields, textvariable=variable, width=78)
            if row == 0:
                # The launcher accepts a path, never pasted session content.
                # Read-only input prevents accidental on-screen secret exposure.
                entry.configure(state="readonly")
            entry.grid(row=row, column=1, sticky="ew", padx=8)
            self.ttk.Button(fields, text="Browse", command=browse).grid(row=row, column=2)
        fields.columnconfigure(1, weight=1)
        buttons = self.ttk.Frame(frame)
        buttons.pack(fill="x", pady=14)
        self.ttk.Button(buttons, text="Start Rubix & App", command=self.start).pack(side="left")
        self.ttk.Button(
            buttons, text="Start Research Only", command=self.start_research_only
        ).pack(side="left", padx=(8, 0))
        self.ttk.Button(buttons, text="Stop", command=self.stop).pack(side="left", padx=8)
        self.ttk.Button(buttons, text="Open Dashboard", command=lambda: webbrowser.open("http://localhost:8501")).pack(side="left")
        self.ttk.Label(frame, textvariable=self.status_var, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.log = self.tk.Text(frame, height=23, wrap="word")
        self.log.pack(fill="both", expand=True, pady=(8, 0))

    def _select_auth_file(self):
        """Select only a user-created temporary file; never read its contents."""
        from tkinter import filedialog

        selected = filedialog.askopenfilename(
            title="Select the fresh Rubix authentication-frame file",
            filetypes=(("Text files", "*.txt"), ("All files", "*.*")),
        )
        if selected:
            self.auth_var.set(selected)

    def _retry_with_auth_file(self):
        self._select_auth_file()
        if self.auth_var.get():
            self.root.after(100, self.start)
        else:
            self.status_var.set("Rubix not started — select a fresh auth-frame file with Browse")

    def start(self):
        from tkinter import messagebox
        from config.settings_manager import settings
        from providers.rubix_subscription import build_rubix_subscription_plan

        try:
            log_event("collector_validation", "Validating Rubix startup inputs.")
            self.supervisor.stop()
            self.supervisor = ProductionSupervisor(self.adapter_var.get(), self.db_var.get())
            # Validate before producing subscription reports or starting any
            # process, so an invalid path has no operational side effects.
            validate_auth_frame(self.auth_var.get())
            cfg = settings.get("market_data")
            plan = build_rubix_subscription_plan(
                PROJECT_ROOT / "data" / "symbols.csv",
                cfg.get("rubix_subscription_batch_size", 100),
            )
            if plan.invalid:
                self.log.insert("end", f"Invalid mappings: {len(plan.invalid)}\n")
            report = save_operational_report("SUBSCRIPTION_PLAN", {
                **plan.as_report(),
                "database": str(self.supervisor.database),
                "requested": list(plan.requested),
                "subscriptions": list(plan.subscriptions),
            })
            self.log.insert("end", f"Subscription report: {report}\n")
            self.status_var.set(f"Starting collector for {len(plan.subscriptions)} symbols...")
            self.supervisor.start_collector(
                self.auth_var.get(), plan.subscriptions,
                cfg.get("rubix_subscription_batch_size", 100),
            )
        except InstanceAlreadyRunning as error:
            # A verified supervisor already owns the feed session — attach to it
            # rather than starting a competing collector on the same credential.
            reason = redact_log(str(error))
            log_event("collector_already_running", reason)
            self.log.insert("end", f"Rubix already running: {reason}\n")
            self.log.see("end")
            self.status_var.set(f"Rubix already running — {reason}")
            if hasattr(self, "_launch_in_progress"):
                self._launch_in_progress = False
            if hasattr(self, "_enable_start_buttons"):
                self._enable_start_buttons()
            open_it = messagebox.askyesno(
                "Rubix supervisor already running",
                f"{reason}\n\nA healthy Rubix supervisor is already running, so a second one "
                "was NOT started (one live session per credential).\n\n"
                "Open the dashboard for the running instance?\n\n"
                "To replace it, press Stop first, then Start.",
            )
            if open_it and hasattr(self, "_open_dashboard"):
                self._open_dashboard()
            return
        except Exception as error:
            reason = redact_log(str(error))
            log_event(
                "collector_start_failed",
                f"{type(error).__name__}: {reason}",
                level=logging.ERROR,
                exc_info=True,
            )
            self.log.insert("end", f"Rubix was not started: {reason}\n")
            self.log.see("end")
            self.status_var.set(f"Rubix not started — {reason}")
            if hasattr(self, "_launch_in_progress"):
                self._launch_in_progress = False
            if hasattr(self, "_enable_start_buttons"):
                self._enable_start_buttons()
            if "authentication-frame" in reason.lower():
                self.auth_var.set("")
            choice = messagebox.askyesnocancel(
                "Rubix was not started",
                f"{reason}\n\nUse Browse and select the file itself; do not paste its contents.\n\n"
                "Yes: Select another file and retry\nNo: Continue with Research Only (EODHD)\nCancel: Stop launch",
            )
            if choice is True:
                self.root.after(100, self._retry_with_auth_file)
            elif choice is False:
                self.start_research_only()
            return
        threading.Thread(target=self._finish_start, daemon=True).start()

    def _finish_start(self):
        healthy, health = self.supervisor.wait_until_healthy()
        self.root.after(0, lambda: self._apply_start_result(healthy, health))

    def _apply_start_result(self, healthy, health):
        """Apply worker results on Tk's UI thread."""

        from tkinter import messagebox

        report = save_operational_report("COLLECTOR_HEALTH", health)
        self.log.insert("end", f"Health report: {report}\n")

        if not healthy:
            reason = health.get("reason") or health.get("status") or "no fresh quotes"
            choice = messagebox.askyesnocancel(
                "Rubix unavailable",
                f"Collector is not healthy: {reason}\n\n"
                "Yes: Retry Rubix\nNo: Continue with Research Only (EODHD)\nCancel: Stop launch",
            )
            self.supervisor.stop()
            if choice is True:
                self.root.after(100, self.start)
                return
            if choice is None:
                self.status_var.set("Cancelled")
                return
            self.start_research_only()
            return
        else:
            self.status_var.set(
                f"Rubix healthy — {health.get('symbols_received', 0)} symbols receiving data"
            )
        self.supervisor.start_streamlit()
        webbrowser.open("http://localhost:8501")

    def stop(self):
        self.supervisor.stop()
        self.status_var.set("Stopped")

    def start_research_only(self):
        """Start the app on EODHD current research only — no Rubix, live intraday off."""

        try:
            self.supervisor.stop()
            self.supervisor = ProductionSupervisor(self.adapter_var.get(), self.db_var.get())
            self.supervisor.start_streamlit()
            self.status_var.set(
                "Research Only — current research uses EODHD; live data unavailable; "
                "intraday opportunities disabled."
            )
            self.log.insert(
                "end",
                "Started without Rubix. Current research uses EODHD; there are no live "
                "intraday quotes in this mode. Yahoo is not used.\n",
            )
            self.log.see("end")
            webbrowser.open("http://localhost:8501")
        except Exception as error:
            reason = redact_log(str(error))
            self.status_var.set(f"Research Only failed — {reason}")
            self.log.insert("end", f"Research Only failed: {reason}\n")

    def close(self):
        self.stop()
        self.root.destroy()

    def _refresh(self):
        while True:
            try:
                message = self.supervisor.messages.get_nowait()
            except queue.Empty:
                break
            self.log.insert("end", message + "\n")
            self.log.see("end")
        now = time.monotonic()
        if now - self._last_health_check >= 2 and self.supervisor.collector is not None:
            self._last_health_check = now
            if self.supervisor.collector.poll() is not None:
                self.status_var.set("Collector stopped unexpectedly — live intraday unavailable (EODHD research still available)")
            else:
                health = self.supervisor.health()
                self.status_var.set(
                    f"{health.get('status', 'STARTING')} · collector {health.get('collector_status')} · "
                    f"updating {health.get('symbols_updating', 0)}/{health.get('symbols_requested', 0)} · "
                    f"age {health.get('age_seconds') if health.get('age_seconds') is not None else 'N/A'}s"
                )
        if self.supervisor.streamlit is not None and self.supervisor.streamlit.poll() is not None:
            self.status_var.set("Streamlit stopped unexpectedly")
        self.root.after(500, self._refresh)

    def run(self):
        self.root.mainloop()


class RubixAuthenticationAssistantUI(LauncherUI):
    """Security-preserving manual assistant aligned with CURRENT_RESEARCH_V2.

    The operational panel reflects the current architecture: EODHD current research,
    Rubix live intraday, and a frozen Yahoo snapshot used ONLY for legacy backtests.
    There is no operational Yahoo provider and no Yahoo fallback here.
    """

    # Grouped operational status. Yahoo never appears as an operational provider.
    STATUS_SECTIONS = (
        ("Current Research", (
            ("Provider", "research_provider"),
            ("Token", "eodhd_token"),
            ("Authentication", "eodhd_auth"),
            ("Latest Session", "research_latest_session"),
            ("Freshness", "research_freshness"),
            ("Cache", "research_cache"),
        )),
        ("Live Intraday", (
            ("Provider", "live_provider"),
            ("Supervisor", "supervisor"),
            ("Collector", "collector"),
            ("Authentication", "rubix_auth"),
            ("Coverage", "coverage"),
            ("Latest Quote", "latest_quote"),
            ("Quote Freshness", "quote_freshness"),
            ("Value Progression", "value_progression"),
        )),
        ("Application", (
            ("Streamlit", "app_status"),
            ("Dashboard URL", "dashboard_url"),
            ("Process PID", "app_pid"),
            ("Health", "app_health"),
        )),
        ("Market", (
            ("Session", "market_session"),
            ("Phase", "market_phase"),
            ("Next Session", "market_next"),
        )),
        ("Safety", (
            ("Paper Mode", "paper_mode"),
            ("Production", "production"),
            ("Broker Execution", "broker"),
        )),
    )
    # Flattened (label, key) list used to allocate the status variables.
    STATUS_FIELDS = tuple(
        (f"{section} · {label}", key)
        for section, fields in STATUS_SECTIONS for label, key in fields
    )

    def __init__(self):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.preferences = load_launcher_preferences(LAUNCHER_CONFIG)
        enable_windows_dpi_awareness()
        self.root = tk.Tk()
        self.root.title("EGX AI Trader — Rubix Production Launcher")
        self.root.update_idletasks()
        self._last_good_geometry = safe_window_geometry(
            self.preferences.get("window_geometry", "1080x820"),
            self.root.winfo_screenwidth(),
            self.root.winfo_screenheight(),
        )
        self.root.minsize(900, 650)
        self.root.geometry(self._last_good_geometry)
        theme = self.preferences.get("theme", "System")
        if theme != "System" and theme in ttk.Style().theme_names():
            ttk.Style().theme_use(theme)

        self.supervisor = ProductionSupervisor()
        self.auth_var = tk.StringVar()
        self.auth_status_var = tk.StringVar(value=AUTH_MISSING)
        self.auth_message_var = tk.StringVar(value="No authentication file selected.")
        self.adapter_var = tk.StringVar(value=self.preferences.get("adapter_path", str(DEFAULT_ADAPTER)))
        self.db_var = tk.StringVar(value=self.preferences.get("database_path", str(os.getenv("RUBIX_DB_PATH") or PRODUCTION_DB)))
        self.port_var = tk.StringVar(value=str(self.preferences.get("streamlit_port", 8501)))
        self.theme_var = tk.StringVar(value=theme)
        self.delete_auth_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Ready — complete the assistant, then run Self Check.")
        self.status_values = {key: tk.StringVar(value="Not started") for _label, key in self.STATUS_FIELDS}
        self._ui_events = queue.Queue()
        self._launch_in_progress = False
        self._pending_port = 8501
        self._last_health_check = 0.0
        self._build_assistant_window()
        self.root.bind("<Configure>", self._remember_window_geometry, add="+")
        self._apply_auth_inspection(inspect_auth_frame(""))
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(500, self._refresh)
        atexit.register(self.supervisor.stop)

    def _build_assistant_window(self):
        outer = self.ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)
        self.ttk.Label(outer, text="EGX AI Trader — Rubix Production", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        self.ttk.Label(
            outer,
            text="Manual authentication only. Frame contents are never displayed, logged, copied, or saved by this launcher.",
        ).pack(anchor="w", pady=(0, 10))
        notebook = self.ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        assistant = self.ttk.Frame(notebook, padding=12)
        monitor = self.ttk.Frame(notebook, padding=12)
        notebook.add(assistant, text="1  Authentication Assistant")
        notebook.add(monitor, text="2  Start & Monitor")
        self._build_auth_tab(assistant)
        self._build_monitor_tab(monitor)

    def _build_auth_tab(self, frame):
        instructions = (
            "1. Open Rubix normally and sign in.\n"
            "2. Open the Live Market page.\n"
            "3. Open Chrome DevTools, then Network.\n"
            "4. Select the WebSocket ending with /websocket/price.\n"
            "5. Open Messages and copy ONLY the outbound authentication message.\n"
            "6. Save it as rubix-price-auth-frame.txt.\n"
            "7. Select that file below within 15 minutes.\n"
            "8. Run Self Check, then press Start Rubix."
        )
        guide = self.ttk.LabelFrame(frame, text="One-minute manual setup", padding=12)
        guide.pack(fill="x")
        self.ttk.Label(guide, text=instructions, justify="left", wraplength=980).pack(anchor="w")
        self.ttk.Label(
            guide,
            text="No login automation, DevTools control, cookie access, token extraction, or authentication bypass is used.",
            foreground="#8a4b08",
        ).pack(anchor="w", pady=(8, 0))

        auth = self.ttk.LabelFrame(frame, text="Authentication file", padding=12)
        auth.pack(fill="x", pady=10)
        self.ttk.Label(auth, text="Fresh price-auth file").grid(row=0, column=0, sticky="w")
        self.ttk.Entry(auth, textvariable=self.auth_var, state="readonly").grid(row=0, column=1, sticky="ew", padx=8)
        self.ttk.Button(auth, text="Browse", command=self._select_auth_file).grid(row=0, column=2)
        self.auth_badge = self.tk.Label(auth, textvariable=self.auth_status_var, font=("Segoe UI", 11, "bold"), padx=12)
        self.auth_badge.grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.ttk.Label(auth, textvariable=self.auth_message_var, wraplength=760).grid(row=1, column=1, columnspan=2, sticky="w", pady=(10, 0))
        self.ttk.Checkbutton(
            auth,
            text="Delete this temporary file after Stop (optional, default OFF)",
            variable=self.delete_auth_var,
        ).grid(row=2, column=1, sticky="w", pady=(8, 0))
        auth.columnconfigure(1, weight=1)

        config = self.ttk.LabelFrame(frame, text="Local configuration (no secrets)", padding=12)
        config.pack(fill="x")
        self._path_row(config, 0, "Rubix collector folder", self.adapter_var, self._select_adapter)
        self._path_row(config, 1, "Production database", self.db_var, self._select_database)
        self.ttk.Label(config, text="Dashboard port").grid(row=2, column=0, sticky="w", pady=4)
        self.ttk.Entry(config, textvariable=self.port_var, width=12).grid(row=2, column=1, sticky="w", padx=8)
        self.ttk.Label(config, text="Theme").grid(row=2, column=1, sticky="e", padx=(0, 150))
        themes = ("System",) + tuple(self.ttk.Style().theme_names())
        self.ttk.Combobox(config, textvariable=self.theme_var, values=themes, state="readonly", width=14).grid(row=2, column=2, sticky="e")
        config.columnconfigure(1, weight=1)

        actions = self.ttk.Frame(frame)
        actions.pack(fill="x", pady=10)
        self.ttk.Button(actions, text="Run Self Check", command=self.run_self_check).pack(side="left")
        self.ttk.Button(actions, text="Open Full Guide", command=self._open_guide).pack(side="left", padx=8)
        self.preflight = self.ttk.Treeview(frame, columns=("state", "check", "message"), show="headings", height=9)
        for key, heading, width in (("state", "Status", 80), ("check", "Check", 180), ("message", "What it means", 700)):
            self.preflight.heading(key, text=heading)
            self.preflight.column(key, width=width, anchor="center" if key == "state" else "w")
        self.preflight.pack(fill="both", expand=True)

    def _build_monitor_tab(self, frame):
        buttons = self.ttk.Frame(frame)
        buttons.pack(fill="x")
        self.start_button = self.ttk.Button(buttons, text="Start Rubix & App", command=self.start)
        self.start_button.pack(side="left")
        self.research_button = self.ttk.Button(
            buttons, text="Start Research Only", command=self.start_research_only)
        self.research_button.pack(side="left", padx=8)
        self.ttk.Button(buttons, text="Stop", command=self.stop).pack(side="left")
        self.ttk.Button(buttons, text="Refresh Status", command=self.refresh_status).pack(side="left", padx=8)
        self.ttk.Button(buttons, text="Open Dashboard", command=self._open_dashboard).pack(side="left")
        self.ttk.Label(frame, textvariable=self.status_var, font=("Segoe UI", 11, "bold"), wraplength=1000).pack(anchor="w", pady=10)

        panel = self.ttk.Frame(frame)
        panel.pack(fill="x")
        for col, (section, fields) in enumerate(self.STATUS_SECTIONS):
            box = self.ttk.LabelFrame(panel, text=section, padding=8)
            box.grid(row=0, column=col, sticky="nsew", padx=4)
            for label, key in fields:
                row = self.ttk.Frame(box)
                row.pack(fill="x", anchor="w", pady=1)
                self.ttk.Label(row, text=f"{label}:", width=16).pack(side="left", anchor="w")
                self.ttk.Label(row, textvariable=self.status_values[key],
                               font=("Segoe UI", 9, "bold"), wraplength=180).pack(side="left", anchor="w")
        for col in range(len(self.STATUS_SECTIONS)):
            panel.columnconfigure(col, weight=1)
        self.ttk.Label(
            frame,
            text=("Legacy: Frozen Yahoo Snapshot — backtest reproduction only, not "
                  "operational. No operational Yahoo provider or fallback exists."),
            foreground="#6b6b6b", wraplength=1000,
        ).pack(anchor="w", pady=(8, 0))
        self.ttk.Label(frame, text="Safe log preview (authentication contents are always redacted)").pack(anchor="w", pady=(8, 0))
        self.log = self.tk.Text(frame, height=14, wrap="word")
        self.log.pack(fill="both", expand=True, pady=(4, 0))
        self._init_static_status()

    def _init_static_status(self):
        """Seed provider/safety cards that do not depend on a running process."""
        self.status_values["research_provider"].set("EODHD")
        self.status_values["live_provider"].set("Rubix")
        self.status_values["dashboard_url"].set(f"http://localhost:{self._safe_port()}")
        self.refresh_status()

    def _safe_port(self):
        try:
            return self._port()
        except Exception:
            return 8501

    def _path_row(self, parent, row, label, variable, command):
        self.ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        self.ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=8)
        self.ttk.Button(parent, text="Browse", command=command).grid(row=row, column=2)

    def _select_auth_file(self):
        from tkinter import filedialog

        selected = filedialog.askopenfilename(
            title="Select the fresh Rubix authentication-frame file",
            initialdir=self.preferences.get("last_auth_folder") or str(Path.home()),
            filetypes=(("Text or JSON files", "*.txt *.json"), ("All files", "*.*")),
        )
        if selected:
            self.auth_var.set(selected)
            self.preferences["last_auth_folder"] = str(Path(selected).parent)
            self._apply_auth_inspection(inspect_auth_frame(selected))
            self._save_preferences()

    def _select_adapter(self):
        from tkinter import filedialog
        selected = filedialog.askdirectory(initialdir=self.adapter_var.get() or str(Path.home()))
        if selected:
            self.adapter_var.set(selected)

    def _select_database(self):
        from tkinter import filedialog
        current = Path(self.db_var.get() or PRODUCTION_DB).expanduser()
        selected = filedialog.asksaveasfilename(
            initialfile=current.name,
            initialdir=str(current.parent),
            defaultextension=".db",
            filetypes=(("SQLite database", "*.db *.sqlite *.sqlite3"),),
        )
        if selected:
            self.db_var.set(selected)

    def _apply_auth_inspection(self, inspection):
        self.auth_status_var.set(inspection.status)
        self.auth_message_var.set(inspection.message)
        colors = {
            AUTH_VALID: ("#d9f2e3", "#116530"), AUTH_EXPIRED: ("#fff0cc", "#7a4b00"),
            AUTH_INVALID: ("#fde2e2", "#8f1d1d"), AUTH_MISSING: ("#eeeeee", "#444444"),
        }
        background, foreground = colors.get(inspection.status, colors[AUTH_MISSING])
        self.auth_badge.configure(background=background, foreground=foreground)
        self.status_values["rubix_auth"].set(inspection.status)

    def run_self_check(self):
        inspection = inspect_auth_frame(self.auth_var.get())
        self._apply_auth_inspection(inspection)
        results = run_preflight(
            PROJECT_ROOT, self.adapter_var.get(), self.db_var.get(),
            self.auth_var.get(), self.port_var.get(),
        )
        for row in self.preflight.get_children():
            self.preflight.delete(row)
        for item in results:
            self.preflight.insert("", "end", values=("PASS" if item.ok else "FAIL", item.name, item.message))
        failed = [item.name for item in results if not item.ok]
        self.status_var.set(
            "Self Check needs attention: " + ", ".join(failed)
            if failed else "Self Check passed. You can start Rubix safely."
        )
        self._save_preferences()
        return results

    def start(self):
        from tkinter import messagebox

        results = self.run_self_check()
        if any(not item.ok for item in results):
            messagebox.showwarning("Self Check incomplete", "Fix the failed items shown in Self Check before starting Rubix.")
            return
        self._pending_port = self._port()
        # Startup flow: load env + verify EODHD token + central calendar before Rubix.
        from core.environment import load_project_environment
        load_project_environment()
        self.refresh_status()
        if not self._eodhd_configured():
            self.log.insert(
                "end",
                "Note: EODHD_API_TOKEN is not configured — Rubix live intraday can still "
                "start, but current research (EODHD) will be unavailable. Configure .env.\n",
            )
            self.log.see("end")
        self._launch_in_progress = True
        self.start_button.configure(state="disabled")
        self.research_button.configure(state="disabled")
        self.status_var.set("Starting Rubix supervisor — waiting for collector health...")
        log_event("rubix_start", f"Rubix startup requested on dashboard port {self._pending_port}.")
        super().start()
        if self.supervisor.collector is None and self.supervisor.streamlit is None:
            self._launch_in_progress = False
            self._enable_start_buttons()

    def _finish_start(self):
        """Complete collector and Streamlit readiness waits off Tk's UI thread."""

        healthy, health = self.supervisor.wait_until_healthy(timeout=90)
        if healthy:
            try:
                self._ui_events.put(
                    ("status", "Rubix collector is healthy. Starting Streamlit dashboard...")
                )
                self.supervisor.start_streamlit(self._pending_port)
                self._ui_events.put(
                    ("status", f"Waiting for localhost:{self._pending_port} to become ready...")
                )
                ready, detail = self.supervisor.wait_until_streamlit_ready(
                    self._pending_port, timeout=45
                )
                health = dict(health)
                health["dashboard_ready"] = ready
                health["dashboard_detail"] = detail
                if not ready:
                    health["reason"] = detail
                    healthy = False
            except Exception as error:
                healthy = False
                health = dict(health)
                health.update({
                    "dashboard_ready": False,
                    "reason": f"{type(error).__name__}: {error}",
                })
                log_event(
                    "streamlit_start_failed",
                    health["reason"],
                    level=logging.ERROR,
                    exc_info=True,
                )
        self._ui_events.put(("rubix_start_result", (healthy, health)))

    def _apply_start_result(self, healthy, health):
        from tkinter import messagebox

        self._launch_in_progress = False
        self._enable_start_buttons()
        report = save_operational_report("COLLECTOR_HEALTH", health)
        self.log.insert("end", f"Health report saved: {report}\n")
        self._apply_health(health)
        if not healthy:
            # On a non-trading day (EGX weekend or configured holiday) a connected,
            # authenticated collector with no fresh quotes is EXPECTED — there are no
            # live ticks to receive. Do not nag with a pointless Rubix retry.
            if should_suppress_retry(health, self._is_trading_day_today()):
                self.supervisor.stop()
                self._record_launch("rubix", "market_closed")
                self.status_var.set(
                    "Market closed today (EGX weekend/holiday) — no live quotes expected. "
                    "Current research (EODHD) is available in Research Only mode."
                )
                messagebox.showinfo(
                    "Market closed today",
                    "Today is a non-trading day for the EGX (weekend or a configured "
                    "holiday), so no live quotes will arrive — this is expected, not a "
                    "fault. Rubix authenticated and connected successfully.\n\n"
                    "Use 'Start Research Only' for EODHD current research, or start Rubix "
                    "again on the next trading day.",
                )
                return
            reason = self._friendly_health_reason(health)
            choice = messagebox.askyesnocancel(
                "Rubix is not ready",
                f"{reason}\n\nYes: retry Rubix\nNo: continue with Research Only (EODHD)\nCancel: stop launch",
            )
            self.supervisor.stop()
            self._record_launch("rubix", "failed")
            if choice is True:
                self.root.after(100, self.start)
            elif choice is False:
                self.start_research_only()
            else:
                self.status_var.set("Launch cancelled. No process is running.")
            return
        self.status_var.set(
            f"Rubix and dashboard are ready — {health.get('symbols_received', 0)} symbols received."
        )
        self._record_launch("rubix", "ready")
        self._save_preferences()
        log_event(
            "startup_success",
            f"Rubix and Streamlit ready on port {self._pending_port}; browser requested.",
        )
        self._open_dashboard()

    def _is_trading_day_today(self):
        """True when today is a regular EGX trading day, honoring configured holidays.

        Fails OPEN (returns True) so a lookup error never hides a genuine alert.
        """
        try:
            from core.egx_session import cairo_now, is_regular_trading_day
            # holidays default to the shared EGX calendar (built-in ∪ configured).
            return is_regular_trading_day(cairo_now().date())
        except Exception:
            return True

    def _friendly_health_reason(self, health):
        if health.get("dashboard_ready") is False:
            return str(health.get("reason") or "The dashboard did not become ready.")
        if health.get("collector_status") == "DISCONNECTED":
            return "The collector is disconnected."
        if health.get("authentication_status") != "ACKNOWLEDGED":
            return "Rubix did not acknowledge the selected authentication file."
        if not health.get("schema_valid"):
            return "Rubix database has not been created yet."
        if not health.get("symbols_received"):
            return "Waiting for fresh market data. No quotes have been received yet."
        return "Waiting for fresh market data."

    def _apply_health(self, health):
        """Populate the Live Intraday + Application cards from Rubix collector health."""
        status = str(health.get("status") or "UNAVAILABLE")
        received = int(health.get("symbols_received") or 0)
        requested = int(health.get("symbols_requested") or 0)
        coverage = health.get("symbol_coverage_pct")
        latest = health.get("latest_received_timestamp") or health.get("latest_exchange_timestamp")
        fresh = status == "RUBIX_FRESH"
        self.status_values["live_provider"].set("Rubix" if fresh else "Rubix (no fresh quotes)")
        self.status_values["supervisor"].set(
            "Running" if self.supervisor.collector is not None
            and self.supervisor.collector.poll() is None else "Stopped")
        self.status_values["collector"].set(health.get("collector_status") or "Not available")
        self.status_values["rubix_auth"].set(health.get("authentication_status") or self.auth_status_var.get())
        self.status_values["coverage"].set(f"{received}/{requested}" + (f" ({coverage:.1f}%)" if coverage is not None else ""))
        self.status_values["latest_quote"].set(str(latest or "Not available"))
        self.status_values["quote_freshness"].set(health.get("freshness") or "Waiting")
        self.status_values["value_progression"].set(
            health.get("value_progression") or ("Advancing" if fresh else "Not observed"))
        # Application cards
        if self.supervisor.streamlit is not None and self.supervisor.streamlit.poll() is None:
            self.status_values["app_status"].set("Running")
            self.status_values["app_pid"].set(str(self.supervisor.streamlit.pid))
        self.status_values["app_health"].set("Healthy" if fresh else "Live intraday unavailable")

    def _eodhd_configured(self):
        try:
            from core.environment import is_eodhd_configured
            return bool(is_eodhd_configured())
        except Exception:
            return False

    def start_research_only(self):
        """Start the app on EODHD current research only. Never Rubix, never Yahoo."""
        if self._launch_in_progress:
            return
        try:
            self._pending_port = self._port()
        except ValueError as error:
            self.status_var.set(str(error))
            return
        from core.environment import load_project_environment
        load_project_environment()
        self.refresh_status()
        if not self._eodhd_configured():
            from tkinter import messagebox
            self.status_var.set("EODHD token missing — configure EODHD_API_TOKEN in .env.")
            self.log.insert(
                "end",
                "Research Only blocked: EODHD_API_TOKEN is not configured. Add it to the "
                "project .env file. Yahoo is never used as a substitute.\n",
            )
            self.log.see("end")
            messagebox.showerror(
                "EODHD token missing",
                "Current research needs EODHD_API_TOKEN in the project .env file.\n\n"
                "The dashboard can still open in diagnostics mode, but current analysis "
                "will show DATA_UNAVAILABLE. Yahoo is never used as a fallback.",
            )
            # Still allow opening the app in diagnostics mode (analysis blocked).
        self._launch_in_progress = True
        self.start_button.configure(state="disabled")
        self.research_button.configure(state="disabled")
        self.status_var.set("Starting dashboard in Research Only mode (EODHD current research)...")
        log_event("research_start", f"Research-only dashboard requested on port {self._pending_port}.")
        threading.Thread(
            target=self._finish_research_start,
            args=(self._pending_port,),
            daemon=True,
        ).start()

    def _finish_research_start(self, port):
        try:
            self.supervisor.stop()
            self.supervisor = ProductionSupervisor(self.adapter_var.get(), self.db_var.get())
            self.supervisor.start_streamlit(port)
            ready, detail = self.supervisor.wait_until_streamlit_ready(port, timeout=45)
            self._ui_events.put(("research_start_result", (ready, detail)))
        except Exception as error:
            reason = f"{type(error).__name__}: {error}"
            log_event(
                "research_start_failed", reason, level=logging.ERROR, exc_info=True
            )
            self._ui_events.put(("research_start_result", (False, reason)))

    def _apply_research_start_result(self, ready, detail):
        from tkinter import messagebox

        self._launch_in_progress = False
        self._enable_start_buttons()
        if not ready:
            self.supervisor.stop()
            self.status_var.set(f"Research Only could not start — {detail}")
            self.log.insert("end", f"Research Only could not start: {detail}\n")
            self.log.see("end")
            self._record_launch("research", "failed")
            messagebox.showerror(
                "Dashboard startup failed",
                f"{detail}\n\nFull details: {LAUNCHER_LOG}",
            )
            return
        self.status_var.set(
            "Research Only started — current research uses EODHD. Live data unavailable; "
            "intraday opportunities disabled."
        )
        for key, value in {
            "live_provider": "Not started (Research Only)", "supervisor": "Not started",
            "collector": "Not started", "rubix_auth": "Not required",
            "quote_freshness": "N/A", "value_progression": "N/A",
            "app_status": "Running", "app_health": "Research Only (no live intraday)",
        }.items():
            self.status_values[key].set(value)
        if self.supervisor.streamlit is not None:
            self.status_values["app_pid"].set(str(self.supervisor.streamlit.pid))
        self.log.insert(
            "end",
            "Started without Rubix. Current research uses EODHD; live intraday is "
            "disabled in this mode. Yahoo is not used.\n",
        )
        self._record_launch("research", "ready")
        self._save_preferences()
        log_event("startup_success", f"Research-only Streamlit ready on port {self._pending_port}.")
        self._open_dashboard()

    def refresh_status(self):
        """Refresh Current Research / Market / Safety cards. Starts no process."""
        try:
            from services.research_launcher_status import (
                current_research_status, market_status, safety_status)
        except Exception as error:
            self.status_values["research_provider"].set(f"EODHD (status error: {type(error).__name__})")
            return
        try:
            research = current_research_status(online=False)
            self.status_values["research_provider"].set(research["provider"])
            self.status_values["eodhd_token"].set(research["token"])
            self.status_values["eodhd_auth"].set(research["authentication"])
            self.status_values["research_latest_session"].set(
                str(research.get("latest_completed_session") or "Unknown"))
            self.status_values["research_freshness"].set(str(research.get("freshness") or "Unknown"))
            self.status_values["research_cache"].set(str(research.get("cache_status") or "Unknown"))
        except Exception as error:
            log_event("research_status_failed", f"{type(error).__name__}: {error}",
                      level=logging.WARNING)
        try:
            market = market_status()
            self.status_values["market_session"].set(market["day_kind"])
            self.status_values["market_phase"].set(
                str(market.get("phase") or "Unknown").replace("_", " ").title())
            self.status_values["market_next"].set(str(market.get("next_session") or "Unknown"))
        except Exception as error:
            log_event("market_status_failed", f"{type(error).__name__}: {error}",
                      level=logging.WARNING)
        try:
            safety = safety_status()
            self.status_values["paper_mode"].set("On" if safety["paper_mode"] else "Off")
            self.status_values["production"].set(
                "Disabled" if not safety["production_enabled"] else "ENABLED")
            self.status_values["broker"].set(
                "Disabled" if not safety["broker_orders_enabled"] else "ENABLED")
        except Exception as error:
            log_event("safety_status_failed", f"{type(error).__name__}: {error}",
                      level=logging.WARNING)
        self.status_values["dashboard_url"].set(f"http://localhost:{self._safe_port()}")

    def stop(self):
        selected = self.auth_var.get()
        self.supervisor.stop()
        self._launch_in_progress = False
        self._enable_start_buttons()
        self.status_var.set("Stopped. Only launcher-managed processes were closed.")
        self.status_values["collector"].set("Stopped")
        self.status_values["supervisor"].set("Stopped")
        self.status_values["app_status"].set("Stopped")
        self.status_values["app_health"].set("Stopped")
        if self.delete_auth_var.get() and selected:
            try:
                Path(selected).unlink(missing_ok=True)
                self.auth_var.set("")
                self._apply_auth_inspection(inspect_auth_frame(""))
                self.log.insert("end", "Temporary authentication file deleted after collector stop.\n")
            except OSError:
                self.log.insert("end", "Could not delete the temporary authentication file; delete it manually.\n")
        self._save_preferences()

    def _refresh(self):
        while True:
            try:
                event, payload = self._ui_events.get_nowait()
            except queue.Empty:
                break
            if event == "rubix_start_result":
                self._apply_start_result(*payload)
            elif event == "research_start_result":
                self._apply_research_start_result(*payload)
            elif event == "status":
                self.status_var.set(str(payload))
        while True:
            try:
                message = self.supervisor.messages.get_nowait()
            except queue.Empty:
                break
            self.log.insert("end", redact_log(message) + "\n")
            self.log.see("end")
        inspection = inspect_auth_frame(self.auth_var.get())
        if inspection.status != self.auth_status_var.get():
            self._apply_auth_inspection(inspection)
        now = time.monotonic()
        if now - self._last_health_check >= 2 and self.supervisor.collector is not None:
            self._last_health_check = now
            if self.supervisor.collector.poll() is not None:
                self.status_var.set("The collector stopped unexpectedly. Live intraday unavailable; EODHD research still available.")
                self.status_values["collector"].set("Disconnected")
                self.status_values["live_provider"].set("Rubix (stopped)")
                self.status_values["app_health"].set("Live intraday unavailable")
            else:
                try:
                    health = self.supervisor.health()
                    self._apply_health(health)
                    self.status_var.set(
                        f"Rubix {self.status_values['live_provider'].get()} — coverage {self.status_values['coverage'].get()} — "
                        f"latest quote {self.status_values['latest_quote'].get()}"
                    )
                except Exception as error:
                    self.status_var.set("Waiting for the Rubix database health check.")
                    log_event(
                        "health_check_failed",
                        f"{type(error).__name__}: {error}",
                        level=logging.WARNING,
                    )
        if self.supervisor.streamlit is not None and self.supervisor.streamlit.poll() is not None:
            self.status_var.set("The dashboard stopped unexpectedly. Press Start again.")
            self.status_values["app_health"].set("Dashboard stopped")
        self.root.after(500, self._refresh)

    def _enable_start_buttons(self):
        if hasattr(self, "start_button"):
            self.start_button.configure(state="normal")
            self.research_button.configure(state="normal")

    def _port(self):
        try:
            value = int(self.port_var.get())
        except ValueError as error:
            raise ValueError("Dashboard port must be a number from 1 to 65535.") from error
        if not 1 <= value <= 65535:
            raise ValueError("Dashboard port must be from 1 to 65535.")
        return value

    def _open_dashboard(self):
        log_event("browser_launch", f"Opening http://localhost:{self._port()} once.")
        webbrowser.open(f"http://localhost:{self._port()}")

    def _remember_window_geometry(self, _event=None):
        """Remember geometry only while the window is normal and visibly sized."""

        if self.root.state() != "normal":
            return
        current = self.root.geometry()
        normalized = safe_window_geometry(
            current,
            self.root.winfo_screenwidth(),
            self.root.winfo_screenheight(),
        )
        # A valid geometry normalizes to itself; minimized Windows sentinels do not.
        if current == normalized:
            self._last_good_geometry = current

    def _open_guide(self):
        guide = PROJECT_ROOT / "AUTHENTICATION_ASSISTANT_GUIDE.md"
        if guide.is_file():
            os.startfile(guide)

    def _record_launch(self, mode, result):
        recent = list(self.preferences.get("recent_launches", []))
        recent.append({"time": datetime.now(timezone.utc).isoformat(), "mode": mode, "result": result})
        self.preferences["recent_launches"] = recent[-10:]

    def _save_preferences(self):
        try:
            port = self._port()
        except ValueError:
            port = 8501
        self.preferences.update({
            "adapter_path": self.adapter_var.get(), "database_path": self.db_var.get(),
            "window_geometry": self._last_good_geometry, "theme": self.theme_var.get(),
            "streamlit_port": port,
        })
        # The selected auth file and its contents are deliberately omitted.
        save_launcher_preferences(self.preferences, LAUNCHER_CONFIG)

    def close(self):
        self.stop()
        self.root.destroy()


def _show_fatal_error(title, message):
    """Keep pythonw failures visible even when Tk initialization did not finish."""

    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, f"{message}\n\nFull log: {LAUNCHER_LOG}")
        root.destroy()
    except Exception:
        pass


def _check_installation():
    required = (
        PROJECT_ROOT / "venv" / "Scripts" / "python.exe",
        PROJECT_ROOT / "venv" / "Scripts" / "pythonw.exe",
        PROJECT_ROOT / "scripts" / "launch_rubix_production.py",
        PROJECT_ROOT / "scripts" / "rubix_collector_supervisor.py",
        PROJECT_ROOT / "app.py",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        print("CHECK=FAIL")
        for path in missing:
            print(f"MISSING={path}")
        return 1
    print(f"PROJECT_ROOT={PROJECT_ROOT}")
    print("VENV=OK")
    print("LAUNCHER=OK")
    print("SUPERVISOR=OK")
    print("APP=OK")
    return 0


def build_argument_parser():
    parser = argparse.ArgumentParser(description="EGX AI Trader Rubix launcher")
    parser.add_argument("--debug", action="store_true", help="mirror launcher events to the console")
    parser.add_argument("--check", action="store_true", help="validate installation files and exit")
    return parser


def main(argv=None):
    args = build_argument_parser().parse_args(argv)
    configure_logging(args.debug)
    if args.check:
        return _check_installation()

    log_event("launcher_entered", f"Launcher entered; project={PROJECT_ROOT}")
    try:
        claim_pid_file(LAUNCHER_PID_FILE, label="Rubix production launcher")
    except InstanceAlreadyRunning as error:
        log_event("duplicate_launcher", str(error), level=logging.WARNING)
        if streamlit_is_ready(8501):
            webbrowser.open("http://localhost:8501")
        _show_fatal_error("EGX AI Trader is already open", str(error))
        return 2

    try:
        os.chdir(PROJECT_ROOT)
        enable_windows_dpi_awareness()
        log_event("gui_created", "Creating the Rubix launcher GUI.")
        RubixAuthenticationAssistantUI().run()
        log_event("launcher_exit", "Launcher window closed normally.")
        return 0
    except Exception as error:
        reason = f"{type(error).__name__}: {redact_log(str(error))}"
        log_event(
            "fatal_error", reason + "\n" + traceback.format_exc(),
            level=logging.ERROR,
        )
        _show_fatal_error("EGX AI Trader launcher failed", reason)
        return 1
    finally:
        release_pid_file(LAUNCHER_PID_FILE)


if __name__ == "__main__":
    raise SystemExit(main())
