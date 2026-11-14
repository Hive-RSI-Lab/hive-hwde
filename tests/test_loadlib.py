"""Time limits that stretch for a busy machine but still catch a hang (#970).

Each stretch case has its planted fault beside it: the same work on an idle
box (no CPU pressure) or a stuck one (no CPU) still runs out at the base.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))
import loadlib  # noqa: E402
from conftest import time_limit  # noqa: E402


def _drive(lim, rate: float) -> float:
    """Run `lim` on a fake clock with work that uses `rate` CPUs; return the
    time it ran out."""
    t = cpu = 0.0
    lim.clock, lim.read_cpu = (lambda: t), (lambda pid: cpu)
    lim.t0, lim.own = 0.0, False
    while True:
        w = lim.wait()
        if w is None:
            return t
        assert w > 0
        t, cpu = t + w, cpu + rate * w
        assert t < 1e5


def _lim(base, p, **kw):
    return loadlib.Limit(base, read_pressure=lambda: p, **kw)


def test_busy_work_on_a_loaded_box_gets_the_stretch():
    # the false red of #970: 300 s of work stalled half the time needs 600 s
    assert _drive(_lim(300, 0.5), 0.5) == pytest.approx(600)
    assert _drive(_lim(300, 0.99), 0.1) == pytest.approx(300 * loadlib.STRETCH_MAX)


def test_planted_a_stuck_call_runs_out_at_its_base_on_any_load():
    lim = _lim(300, 0.99)
    assert _drive(lim, 0.0) == pytest.approx(300)
    assert "stuck" in lim.why


def test_planted_slow_work_on_an_idle_box_runs_out_at_its_base():
    lim = _lim(300, 0.05)
    assert _drive(lim, 1.0) == pytest.approx(300)
    assert "not stretched" in lim.why


def test_without_proc_it_is_the_plain_limit():
    lim = _lim(300, 0.9)
    t = 0.0
    lim.clock, lim.read_cpu, lim.t0 = (lambda: t), (lambda pid: None), 0.0
    while (w := lim.wait()) is not None:
        t += w
    assert t == pytest.approx(300) and "no process CPU" in lim.why


def test_stretch_and_pressure_reading(tmp_path):
    assert loadlib.stretch(0) == 1 and loadlib.stretch(0.5) == 2
    assert loadlib.stretch(0.95) == loadlib.STRETCH_MAX == loadlib.stretch(1)
    f = tmp_path / "cpu.pressure"
    f.write_text("some avg10=12.00 avg60=40.50 avg300=30.00 total=1\n"
                 "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
    assert loadlib._psi_some(f) == pytest.approx(0.405)
    assert loadlib._psi_some(tmp_path / "none") is None
    assert 0 <= loadlib.pressure() <= 1


def test_tree_cpu_counts_a_busy_child():
    p = subprocess.Popen([sys.executable, "-c", "import os,time\nt=time.time()\n"
                          "while time.time()-t<0.6: pass\nos._exit(0)"])
    try:
        time.sleep(0.5)
        assert loadlib.tree_cpu(p.pid) > 0
        assert loadlib.tree_cpu(1 << 30) is None
    finally:
        p.wait()


BUSY = "import time\nt=time.time()\nwhile time.time()-t<{}: pass\n"


@pytest.mark.parametrize("p,ok", [(0.9, True), (0.0, False)], ids=["loaded", "planted_idle"])
def test_live_run_lets_a_loaded_child_finish(monkeypatch, p, ok):
    monkeypatch.setattr(loadlib, "pressure", lambda: p)
    argv = [sys.executable, "-c", BUSY.format(1.6)]
    if ok:
        assert loadlib.run(argv, timeout=1.0, capture_output=True).returncode == 0
    else:
        with pytest.raises(subprocess.TimeoutExpired):
            loadlib.run(argv, timeout=1.0, capture_output=True)


def test_live_planted_a_hung_child_is_killed_at_its_base_under_load(monkeypatch):
    monkeypatch.setattr(loadlib, "pressure", lambda: 0.95)
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        loadlib.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1.0)
    assert time.monotonic() - t0 < 2.5


def test_live_run_passes_input_and_output():
    cp = loadlib.run([sys.executable, "-c", "import sys; print(sys.stdin.read()[::-1])"],
                     timeout=30, input="abc", text=True, capture_output=True)
    assert (cp.returncode, cp.stdout.strip()) == (0, "cba")


def test_the_test_limit_stretches_for_a_loaded_busy_test():
    with time_limit(0.5, "the_test", read_pressure=lambda: 0.9):
        _work(1.0, sleep=False)


def _work(seconds: float, sleep: bool) -> None:
    # In its own frame: CPython 3.14.4 leaves a loop's backward jump after an
    # `if` out of the exception table, and that jump is where a signal handler
    # runs, so its exception skips a try or with in the same frame.
    t = time.monotonic()
    while time.monotonic() - t < seconds:
        if sleep:
            time.sleep(0.05)


@pytest.mark.parametrize("p,work", [(0.9, "sleep"), (0.0, "busy")])
def test_planted_the_test_limit_still_fails_stuck_or_idle(p, work):
    t0 = time.monotonic()
    with pytest.raises(pytest.fail.Exception, match="the_test ran past its limit of 0.5 s"):
        with time_limit(0.5, "the_test", read_pressure=lambda: p):
            _work(3, work == "sleep")
    assert time.monotonic() - t0 < 1.5   # at its base, not stretched to 2 s
