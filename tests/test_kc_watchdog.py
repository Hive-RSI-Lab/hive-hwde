"""kicad-cli runs end on no progress, not at a fixed 300 s (issue #414).

kc.run_watched kills a run that has given no output and gained no CPU time
for its stall window, or that passes its hard cap, and kills its whole process
group (the AppImage's AppRun forks the real kicad-cli). A run that keeps
working is let be. No KiCad needed: the commands are sh and python.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import kc  # noqa: E402

linux = pytest.mark.skipif(not Path("/proc/self/stat").exists(),
                           reason="CPU progress is read from /proc")


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(kc, "_POLL_S", 0.1)
    monkeypatch.delenv("HWDE_KC_TIMEOUT", raising=False)
    monkeypatch.delenv("HWDE_KC_STALL_S", raising=False)


def test_a_silent_idle_run_is_killed_as_a_group():
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as ei:
        kc.run_watched(["sh", "-c", "sleep 30 & sleep 30"],
                       stall_s=1.0, hard_s=None)
    assert ei.value.stalled is True
    assert time.monotonic() - t0 < 10


@linux
def test_a_silent_run_burning_cpu_is_not_killed():
    code = "import time\nt=time.time()\nwhile time.time()-t<2.5: pass\nprint('done')"
    cp = kc.run_watched([sys.executable, "-c", code], stall_s=1.0, hard_s=None)
    assert cp.returncode == 0 and cp.stdout.strip() == "done"


def test_a_run_that_keeps_printing_is_not_killed():
    cp = kc.run_watched(["sh", "-c", "for i in 1 2 3 4 5 6; do echo $i; sleep 0.4; done"],
                        stall_s=1.0, hard_s=None)
    assert cp.returncode == 0 and cp.stdout.split() == list("123456")


def test_the_hard_cap_ends_a_working_run():
    code = "import time\nwhile True: print(1, flush=True); time.sleep(0.1)"
    with pytest.raises(subprocess.TimeoutExpired) as ei:
        kc.run_watched([sys.executable, "-c", code], stall_s=10.0, hard_s=1.0)
    assert ei.value.stalled is False and ei.value.timeout == 1.0


def test_output_and_returncode_pass_through():
    cp = kc.run_watched(["sh", "-c", "echo out; echo err >&2; exit 3"],
                        stall_s=5.0, hard_s=5.0)
    assert (cp.stdout, cp.stderr, cp.returncode) == ("out\n", "err\n", 3)


def test_run_cli_limits_from_env(monkeypatch):
    seen = {}

    def fake(cmd, *, stall_s, hard_s, env=None, cwd=None):
        seen.update(stall_s=stall_s, hard_s=hard_s)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(kc, "run_watched", fake)
    kc.run_cli(Path("kicad-cli"), ["version"])
    assert seen == {"stall_s": kc.STALL_S, "hard_s": kc.HARD_S}
    kc.run_cli(Path("kicad-cli"), ["version"], timeout=50)
    assert seen["hard_s"] == 50
    monkeypatch.setenv("HWDE_KC_TIMEOUT", "900")
    monkeypatch.setenv("HWDE_KC_STALL_S", "30")
    kc.run_cli(Path("kicad-cli"), ["version"], timeout=50)
    assert seen == {"stall_s": 30.0, "hard_s": 900.0}
    monkeypatch.setenv("HWDE_KC_TIMEOUT", "0")
    kc.run_cli(Path("kicad-cli"), ["version"])
    assert seen["hard_s"] is None
