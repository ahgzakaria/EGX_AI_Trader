r"""Open the EGX AI Trader dashboard. That is the whole job.

    START.cmd                       double-click
    venv\Scripts\pythonw.exe scripts\launch_dashboard.py
    venv\Scripts\python.exe  scripts\launch_dashboard.py --check
    venv\Scripts\python.exe  scripts\launch_dashboard.py --no-gui

This replaces a 2,501-line launcher that did two things: it started the
dashboard, and it drove the Rubix price collector -- browsing for an
authentication frame, running a preflight, waiting for the feed to go healthy,
polling its status, and offering deep diagnostics. The feed is gone. What was
left was a window whose Start button could not succeed, next to a second button
labelled "Research Only" that did the only thing still possible.

So Research Only is the whole launcher now, and it is not called that any more
because there is nothing to distinguish it from.

Every mechanism that was load-bearing is kept, because each was written after
something went wrong:

* **one instance.** Two launchers meant two Streamlit processes fighting for
  port 8501, and the second one's failure looked like the app being broken.
* **an existing dashboard is reused, not replaced.** A double-click while the
  app is already open must bring up that app, not kill it and start again.
* **a port held by something else is refused rather than reported as ready.**
* **readiness is polled against the health endpoint**, so "started" means the
  page will actually load rather than that a process exists.
* **failures reach a dialog and a log**, because a launcher that fails silently
  is indistinguishable from one that was never double-clicked.

Nothing here reads market data, and nothing places an order.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import webbrowser

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.launcher_process_utils import (  # noqa: E402
    InstanceAlreadyRunning,
    SingleInstanceLock,
    enable_windows_dpi_awareness,
    pid_record_is_current,
    port_is_open,
    process_creation_time,
    read_pid_record,
    safe_window_geometry,
    streamlit_is_ready,
    wait_for_streamlit,
)

APP = PROJECT_ROOT / "app.py"
LAUNCHER_LOG = PROJECT_ROOT / "logs" / "dashboard_launcher.log"
STREAMLIT_LOG = PROJECT_ROOT / "logs" / "streamlit.log"
STREAMLIT_PID_FILE = PROJECT_ROOT / "data" / "egx_streamlit.pid.json"
LAUNCHER_PID_FILE = PROJECT_ROOT / "data" / "egx_dashboard_launcher.pid.json"
GEOMETRY_FILE = PROJECT_ROOT / "data" / "dashboard_launcher_window.json"

DEFAULT_PORT = 8501
READY_TIMEOUT = 45.0

#: Four buttons, a status line and a log. The window it replaced was 1080x820
#: because it carried an auth tab and a monitor tab.
WINDOW_SIZE = (760, 420)
MIN_SIZE = (620, 340)

logger = logging.getLogger("egx.dashboard_launcher")


def configure_logging(debug=False):
    LAUNCHER_LOG.parent.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.handlers.clear()
    handler = logging.FileHandler(LAUNCHER_LOG, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    if debug:
        logger.addHandler(logging.StreamHandler(sys.stdout))


def log_event(stage, message, *, level=logging.INFO, exc_info=False):
    logger.log(level, "%s | %s", stage, message, exc_info=exc_info)


def python_executable():
    """The project venv's python, or whatever is running this as a fallback."""
    candidate = PROJECT_ROOT / "venv" / "Scripts" / "python.exe"
    return candidate if candidate.is_file() else Path(sys.executable)


class DashboardProcess:
    """Start, reuse and stop the Streamlit process behind the dashboard."""

    def __init__(self, port=DEFAULT_PORT):
        self.port = int(port)
        self.process = None
        self._owned = False

    def start(self):
        """Return "started", "existing", or raise.

        An already-running dashboard is reused. Refusing a port held by
        something that is not our dashboard matters more than it looks: the
        alternative is reporting success and handing the user a URL that opens
        somebody else's server.
        """

        if self.process is not None and self.process.poll() is None:
            return "already_owned"

        marker = read_pid_record(STREAMLIT_PID_FILE)
        if port_is_open(self.port):
            if pid_record_is_current(marker) and streamlit_is_ready(self.port):
                self._owned = False
                log_event("reused",
                          f"dashboard PID {marker.get('pid')} already on {self.port}")
                return "existing"
            raise RuntimeError(
                f"Port {self.port} is held by another process. Close it, or start "
                f"the dashboard on a different port.")
        if marker:
            STREAMLIT_PID_FILE.unlink(missing_ok=True)

        command = [str(python_executable()), "-m", "streamlit", "run", "app.py",
                   "--server.port", str(self.port), "--server.headless", "true"]
        flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        log_event("starting", f"port={self.port} cwd={PROJECT_ROOT}")
        self.process = subprocess.Popen(
            command, cwd=str(PROJECT_ROOT), env=os.environ.copy(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=flags, bufsize=1)
        self._owned = True

        STREAMLIT_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        STREAMLIT_PID_FILE.write_text(json.dumps({
            "pid": self.process.pid,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "process_created_at": process_creation_time(self.process.pid),
            "port": self.port,
            "app": str(APP),
        }, indent=2), encoding="utf-8")
        log_event("started", f"PID {self.process.pid} on port {self.port}")
        threading.Thread(target=self._drain, daemon=True).start()
        return "started"

    def wait_until_ready(self, timeout=READY_TIMEOUT, *, progress=None):
        """True only when the health endpoint answers, not when a PID exists."""
        if self.process is None:
            if streamlit_is_ready(self.port):
                return True, f"The dashboard is already serving port {self.port}."
            return False, "The dashboard process was never started."
        ready, detail = wait_for_streamlit(
            self.port, self.process, timeout=float(timeout), progress=progress)
        log_event("ready" if ready else "not_ready", detail,
                  level=logging.INFO if ready else logging.ERROR)
        return ready, detail

    @property
    def url(self):
        return f"http://localhost:{self.port}"

    def stop(self):
        """Stop only a dashboard this launcher started.

        One it merely reused belongs to whoever opened it, and closing this
        window must not take their app down with it.
        """

        if self.process is None or not self._owned:
            return "not_owned"
        if self.process.poll() is not None:
            return "already_stopped"
        try:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        except Exception as error:                      # noqa: BLE001
            log_event("stop_failed", f"{type(error).__name__}: {error}",
                      level=logging.ERROR)
            return "failed"
        finally:
            STREAMLIT_PID_FILE.unlink(missing_ok=True)
            self.process = None
            self._owned = False
        log_event("stopped", "dashboard stopped")
        return "stopped"

    def _drain(self):
        """Streamlit's own output, to a file rather than a pipe nobody reads.

        A full pipe buffer blocks the child; this is what keeps it running.
        """
        STREAMLIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        try:
            with STREAMLIT_LOG.open("a", encoding="utf-8") as sink:
                for line in self.process.stdout:
                    sink.write(line)
                    sink.flush()
        except Exception:                               # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
# The window
# --------------------------------------------------------------------------- #

class LauncherWindow:
    """Four buttons and a log. Deliberately."""

    def __init__(self, port=DEFAULT_PORT):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.dashboard = DashboardProcess(port)
        self.root = tk.Tk()
        self.root.title("EGX AI Trader")
        # Through safe_window_geometry, because a remembered geometry can name
        # a monitor that is no longer attached -- the window then opens off
        # screen and reads as a launcher that did nothing.
        self.root.geometry(safe_window_geometry(
            self._remembered_geometry(),
            self.root.winfo_screenwidth(), self.root.winfo_screenheight(),
            default_size=WINDOW_SIZE, minimum_size=MIN_SIZE))
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.status = tk.StringVar(value="Ready. Press Start to open the dashboard.")
        self.port_var = tk.StringVar(value=str(port))
        self._busy = False

        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill="both", expand=True)

        row = ttk.Frame(frame)
        row.pack(fill="x")
        ttk.Label(row, text="Port").pack(side="left")
        ttk.Entry(row, textvariable=self.port_var, width=8).pack(side="left", padx=(6, 16))
        self.start_button = ttk.Button(row, text="Start dashboard", command=self.start)
        self.start_button.pack(side="left")
        self.open_button = ttk.Button(row, text="Open in browser", command=self.open_browser)
        self.open_button.pack(side="left", padx=6)
        self.stop_button = ttk.Button(row, text="Stop", command=self.stop)
        self.stop_button.pack(side="left")

        ttk.Label(frame, textvariable=self.status, wraplength=700,
                  justify="left").pack(fill="x", pady=(12, 6))
        self.log = tk.Text(frame, height=14, wrap="word")
        self.log.pack(fill="both", expand=True)
        self._say("The daily data update is a separate step: RUN_DAILY.bat.")

    def _remembered_geometry(self):
        try:
            return json.loads(GEOMETRY_FILE.read_text(encoding="utf-8")).get("geometry")
        except (OSError, ValueError):
            return None

    def _say(self, message):
        self.status.set(message)
        self.log.insert("end", message + "\n")
        self.log.see("end")

    def _set_busy(self, busy):
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.start_button.configure(state=state)
        self.stop_button.configure(state=state)

    def _port(self):
        try:
            value = int(str(self.port_var.get()).strip())
        except (TypeError, ValueError):
            return None
        return value if 1 <= value <= 65535 else None

    def start(self):
        if self._busy:
            return
        port = self._port()
        if port is None:
            self._say("That is not a usable port number.")
            return
        self.dashboard.port = port
        self._set_busy(True)
        self._say(f"Starting the dashboard on port {port}…")
        threading.Thread(target=self._start_worker, daemon=True).start()

    def _start_worker(self):
        """Off the UI thread, so the window keeps repainting while it waits."""
        try:
            outcome = self.dashboard.start()
            if outcome == "existing":
                self.root.after(0, self._started, True,
                                "The dashboard was already running; reusing it.")
                return
            ready, detail = self.dashboard.wait_until_ready(
                progress=lambda elapsed: self.root.after(
                    0, self.status.set, f"Waiting for the dashboard ({elapsed:.0f}s)…"))
            if not ready:
                self.dashboard.stop()
            self.root.after(0, self._started, ready, detail)
        except Exception as error:                      # noqa: BLE001
            reason = f"{type(error).__name__}: {error}"
            log_event("start_failed", reason, level=logging.ERROR, exc_info=True)
            self.root.after(0, self._started, False, reason)

    def _started(self, ready, detail):
        from tkinter import messagebox

        self._set_busy(False)
        if not ready:
            self._say(f"Could not start: {detail}")
            messagebox.showerror("The dashboard did not start",
                                 f"{detail}\n\nFull log: {LAUNCHER_LOG}")
            return
        self._say(f"{detail}  →  {self.dashboard.url}")
        self.open_browser()

    def open_browser(self):
        if not streamlit_is_ready(self.dashboard.port):
            self._say("The dashboard is not answering yet. Press Start first.")
            return
        webbrowser.open(self.dashboard.url)

    def stop(self):
        outcome = self.dashboard.stop()
        self._say({
            "stopped": "Dashboard stopped.",
            "already_stopped": "It had already stopped.",
            "not_owned": "That dashboard was started elsewhere, so it is left running.",
            "failed": "Could not stop it -- see the log.",
        }.get(outcome, outcome))

    def close(self):
        try:
            GEOMETRY_FILE.parent.mkdir(parents=True, exist_ok=True)
            GEOMETRY_FILE.write_text(
                json.dumps({"geometry": self.root.winfo_geometry()}), encoding="utf-8")
        except OSError:
            pass
        # The dashboard outlives this window on purpose: closing the launcher
        # is not a request to close the app you are reading.
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# --------------------------------------------------------------------------- #

def check_installation():
    required = (python_executable(), APP, PROJECT_ROOT / "scripts" / "launch_dashboard.py")
    missing = [str(p) for p in required if not Path(p).is_file()]
    if missing:
        print("CHECK=FAIL")
        for path in missing:
            print(f"MISSING={path}")
        return 1
    print(f"PROJECT_ROOT={PROJECT_ROOT}")
    print("VENV=OK")
    print("LAUNCHER=OK")
    print("APP=OK")
    return 0


def run_headless(port):
    """Start the dashboard and report, for a terminal or a smoke test."""
    dashboard = DashboardProcess(port)
    try:
        outcome = dashboard.start()
    except RuntimeError as error:
        print(str(error))
        return 1
    if outcome == "existing":
        print(f"Already running: {dashboard.url}")
        return 0
    ready, detail = dashboard.wait_until_ready()
    print(detail)
    if not ready:
        dashboard.stop()
        return 1
    print(f"Dashboard: {dashboard.url}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Open the EGX AI Trader dashboard")
    parser.add_argument("--check", action="store_true",
                        help="validate the installation and exit")
    parser.add_argument("--no-gui", action="store_true",
                        help="start the dashboard without the launcher window")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--debug", action="store_true",
                        help="mirror launcher events to the console")
    args = parser.parse_args(argv)

    configure_logging(args.debug)
    if args.check:
        return check_installation()
    if args.no_gui:
        return run_headless(args.port)

    # One window. Two launchers meant two Streamlit processes contending for
    # the same port, and the loser's failure looked like a broken app.
    try:
        with SingleInstanceLock(LAUNCHER_PID_FILE, label="dashboard_launcher"):
            enable_windows_dpi_awareness()
            LauncherWindow(args.port).run()
    except InstanceAlreadyRunning:
        log_event("already_running", "another launcher window owns the lock")
        try:
            from tkinter import Tk, messagebox

            root = Tk()
            root.withdraw()
            messagebox.showinfo(
                "EGX AI Trader",
                "The launcher is already open. Look for its window.")
            root.destroy()
        except Exception:                               # noqa: BLE001
            print("The launcher is already open.")
        return 0
    except Exception as error:                          # noqa: BLE001
        log_event("fatal", f"{type(error).__name__}: {error}",
                  level=logging.ERROR, exc_info=True)
        try:
            from tkinter import Tk, messagebox

            root = Tk()
            root.withdraw()
            messagebox.showerror("EGX AI Trader",
                                 f"{error}\n\nFull log: {LAUNCHER_LOG}")
            root.destroy()
        except Exception:                               # noqa: BLE001
            print(f"{type(error).__name__}: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
