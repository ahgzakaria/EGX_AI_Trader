"""The launcher's Stop must ask before it kills.

``stop_process`` sends ``taskkill /T`` without ``/F``, which delivers WM_CLOSE
to the target's windows. The supervisor runs under pythonw with no window at
all, so nothing receives it, and five seconds later the forced kill lands.

That is why 3516 supervisor log lines contained no shutdown line: every stop
through this launcher, for as long as it has existed, was a hard kill that
skipped the final WAL checkpoint on a multi-gigabyte database. The stop flag
is the same one STOP.cmd writes, so all three ways of stopping now leave
through the supervisor's own ``finally``.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from scripts.launch_rubix_production import COLLECTOR_STOP_FLAG, ProductionSupervisor


def test_the_flag_path_matches_the_supervisor_s_own_default():
    """Three writers, one path. A drift here turns a clean stop back into a kill."""
    from scripts.rubix_collector_supervisor import build_parser

    supervisor_default = Path(build_parser().parse_args([
        "--adapter", "x", "--auth-frame-file", "y", "--database", "z",
    ]).stop_file)
    assert supervisor_default == COLLECTOR_STOP_FLAG

    stop_script = Path("scripts/stop_everything.ps1").read_text(encoding="utf-8")
    assert r"data\runtime\stop_requested.flag" in stop_script


def test_stop_asks_before_it_kills():
    """The order matters: a kill first makes the request meaningless."""
    import inspect

    source = inspect.getsource(ProductionSupervisor.stop)
    assert "request_collector_stop()" in source
    assert source.index("request_collector_stop") < source.index("stop_process(self.collector)")


class _Supervisor(ProductionSupervisor):
    def __init__(self, child):  # the real __init__ validates an adapter path
        self.collector = child
        self.streamlit = None
        self._owns_streamlit = False


@pytest.fixture
def watcher(tmp_path):
    """A process shaped like the supervisor: it polls the flag and exits 0."""
    script = tmp_path / "watcher.py"
    script.write_text(textwrap.dedent(f"""
        import time
        from pathlib import Path
        flag = Path(r"{COLLECTOR_STOP_FLAG}")
        started = time.time()
        while time.time() - started < 60:
            if flag.exists() and flag.stat().st_mtime >= started:
                raise SystemExit(0)
            time.sleep(0.1)
        raise SystemExit(3)
    """), encoding="utf-8")
    COLLECTOR_STOP_FLAG.unlink(missing_ok=True)
    child = subprocess.Popen([sys.executable, str(script)])
    time.sleep(0.6)
    yield child
    if child.poll() is None:
        child.kill()
        child.wait(timeout=5)
    COLLECTOR_STOP_FLAG.unlink(missing_ok=True)


def test_a_supervisor_that_honours_the_flag_is_never_killed(watcher):
    supervisor = _Supervisor(watcher)

    assert supervisor.request_collector_stop(timeout=20) is True
    assert watcher.poll() == 0, "it must exit on its own, with its own exit code"


def test_the_flag_is_removed_afterwards(watcher):
    """Left behind it would stop the next morning's collector a second in."""
    _Supervisor(watcher).request_collector_stop(timeout=20)
    assert not COLLECTOR_STOP_FLAG.exists()


def test_a_supervisor_that_ignores_the_flag_falls_through_to_the_kill(tmp_path):
    """The old behaviour must remain available, just no longer first."""
    script = tmp_path / "stubborn.py"
    script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    child = subprocess.Popen([sys.executable, str(script)])
    time.sleep(0.5)
    try:
        supervisor = _Supervisor(child)
        assert supervisor.request_collector_stop(timeout=3) is False
        assert child.poll() is None, "the request alone must not kill it"
        supervisor.stop()   # now the kill
        child.wait(timeout=15)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        COLLECTOR_STOP_FLAG.unlink(missing_ok=True)


def test_asking_a_collector_that_is_already_gone_is_not_an_error():
    class _Dead:
        def poll(self):
            return 0

    assert _Supervisor(_Dead()).request_collector_stop(timeout=1) is False
    assert _Supervisor(None).request_collector_stop(timeout=1) is False
