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


# --- a stop has to say who asked --------------------------------------------
#
# On 2026-09-07 the collector stopped eight minutes into the session. The log
# said "Asked the collector to stop cleanly." and nothing else. Everything
# around it was recoverable -- clean stop, through the flag, Streamlit survived
# so it was not STOP.cmd, and the launcher's own health gate had reported
# RUBIX_FRESH that morning -- but who asked was not on disk anywhere, so the
# lost session has a cause that can be neither confirmed nor ruled out.

def _launcher():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "lrp_stop", "scripts/launch_rubix_production.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_stop_names_the_call_chain_that_asked_for_it():
    module = _launcher()

    def a_button_handler():
        return an_inner_step()

    def an_inner_step():
        return module._stop_requester()

    described = a_button_handler()
    assert "a_button_handler" in described
    assert "an_inner_step" in described


def test_the_stop_plumbing_names_its_caller_not_itself():
    """`stop` and `request_collector_stop` are never the answer to 'who asked'."""

    module = _launcher()

    def stop():                      # the launcher's own method name
        return module._stop_requester()

    assert "stop:" not in stop()


def test_the_attribution_is_not_limited_to_this_file():
    """An atexit handler in another module is the case that was unanswerable."""

    from pathlib import Path

    source = Path("scripts/launch_rubix_production.py").read_text(encoding="utf-8")
    body = source.partition("def _stop_requester():")[2].partition("\ndef ")[0]
    assert "launch_rubix_production" not in body.split('"""')[-1], (
        "filtering by filename hides a stop that came from somewhere else")


def test_every_stop_log_line_carries_the_requester():
    """Both initiators. The forced kill is the harsher path and the same question.

    Read from the log_event call sites, not from the message text: the same
    sentences appear in the docstring explaining why they carry a requester, and
    matching those instead is how this test kept failing on its own prose.
    """

    import ast
    from pathlib import Path

    tree = ast.parse(
        Path("scripts/launch_rubix_production.py").read_text(encoding="utf-8"))

    initiators = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "id", "") == "log_event"
                and node.args and getattr(node.args[0], "value", "") == "stop"):
            continue
        rendered = ast.unparse(node.args[1])
        for phrase in ("Asked the collector to stop cleanly",
                       "Stopping process tree PID"):
            if phrase in rendered:
                initiators[phrase] = rendered

    assert len(initiators) == 2, f"expected both initiators, found {list(initiators)}"
    for phrase, rendered in initiators.items():
        assert "_stop_requester()" in rendered, (
            f"{phrase!r} does not name its caller, which is the thing being fixed")
