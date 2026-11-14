"""KiCad starts under xvfb-run must not race the X server's reset (#898).

An X server resets when its last client disconnects, and wx probes the
display and reconnects: a connect that lands in the reset fails with
"wxEntryStart failed". With many KiCad starts at once on a busy machine that
was about 1 in 8 (24 of 180), and a job that waited an hour in the queue lost
its run. -noreset keeps the server as it is between clients.

The reset itself is the root cause, and it can be seen every time, not one
start in eight: a reset clears the root window's properties. So the probe sets
one, disconnects, and reads it back. Each check runs the repo's own launcher
(bin/pcb-flow's x(), benchcorpus.start_xvfb) with the real xvfb-run first on
PATH, since a machine may wrap xvfb-run with -noreset of its own.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
import benchcorpus  # noqa: E402
import env  # noqa: E402

PROBE = "../tests/fixtures/xvfb/x_probe"     # bin/pcb-flow runs scripts/<name>.py
KEPT = 'HWDE_X_PROBE(STRING) = "kept"'


def _real_xvfb_run() -> str:
    for cand in ("/usr/bin/xvfb-run", shutil.which("xvfb-run")):
        if cand and Path(cand).is_file():
            return cand
    pytest.skip("xvfb-run not installed")


@pytest.fixture
def no_display(tmp_path):
    """The env of a run with no X display and the real xvfb-run on PATH."""
    if not shutil.which("xprop"):
        pytest.skip("xprop not installed")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "xvfb-run").symlink_to(_real_xvfb_run())
    e = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
    e["PATH"] = f"{bindir}{os.pathsep}{e.get('PATH', '')}"
    return e


def _pcb_flow(env_, mode: str) -> str:
    out = subprocess.run([str(ROOT / "bin" / "pcb-flow"), PROBE, mode], env=env_,
                         capture_output=True, text=True, timeout=240)
    return out.stdout + out.stderr


def test_pcb_flow_display_survives_its_last_client_leaving(no_display):
    assert KEPT in _pcb_flow(no_display, "reset")


def test_planted_a_display_that_resets_is_caught(no_display):
    # the launcher as it was: the probe must see the reset
    out = subprocess.run([_real_xvfb_run(), "-a", "-s", "-extension GLX",
                          sys.executable, str(ROOT / "scripts" / f"{PROBE}.py"),
                          "reset"], env=no_display, capture_output=True,
                         text=True, timeout=120)
    assert "no such atom" in out.stdout + out.stderr


def test_benchcorpus_display_survives_its_last_client_leaving(no_display):
    if not shutil.which("Xvfb"):
        pytest.skip("Xvfb not installed")
    proc, display = benchcorpus.start_xvfb()
    try:
        e = dict(no_display, DISPLAY=display)
        subprocess.run(["xprop", "-root", "-f", "HWDE_X_PROBE", "8s", "-set",
                        "HWDE_X_PROBE", "kept"], env=e, check=True, timeout=30)
        out = subprocess.run(["xprop", "-root", "HWDE_X_PROBE"], env=e,
                             capture_output=True, text=True, timeout=30)
        assert KEPT in out.stdout
    finally:
        proc.kill()
        proc.wait()


def test_live_kicad_starts_at_once_all_come_up(no_display):
    """The failure as it was seen: KiCad's python starting wx in several
    pcb-flow runs at once, each on its own xvfb-run display."""
    cli = env.find_kicad_cli()
    if cli is None or env.find_kicad_python(cli) is None:
        pytest.skip("needs KiCad's python")
    n = 8
    with ThreadPoolExecutor(n) as pool:
        outs = list(pool.map(lambda _: _pcb_flow(no_display, "wx"), range(n)))
    bad = [o for o in outs if "wx ok" not in o]
    assert not bad, bad[0]
