"""V-U12-1: the four KiCad SWIG workers (board_swig, place_swig, route_swig,
update_swig) hold safelib.board_locks(job) from LoadBoard to Save.

The lock tests are real: each worker runs under KiCad's bundled python on a
copy of the blinky2 golden board, once with the board free (it proceeds) and
once while this process holds the board's writer lock (it waits, and its
output lands only after the lock is released). Skipped without KiCad.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
GOLDEN = REPO / "tests" / "golden"

sys.path.insert(0, str(SCRIPTS / "lib"))
import env  # noqa: E402
import safelib  # noqa: E402


# ------------------------------------------------------------ pure python

def test_board_locks_one_lock_when_board_is_out(tmp_path):
    pcb = tmp_path / "b.kicad_pcb"
    with safelib.board_locks({"board": str(pcb), "out": str(pcb)}) as held:
        assert held == [pcb.resolve()]
    assert (tmp_path / "b.kicad_pcb.lock").exists()


def test_board_locks_both_paths_sorted_and_skips_absent(tmp_path):
    a, b = tmp_path / "a.kicad_pcb", tmp_path / "b.kicad_pcb"
    with safelib.board_locks({"board": str(b), "out": str(a)}) as held:
        assert held == [a.resolve(), b.resolve()]
    with safelib.board_locks({"board": str(b)}) as held:  # export_dsn shape
        assert held == [b.resolve()]


# ------------------------------------------------------------ real KiCad

@pytest.fixture(scope="module")
def bundled_python() -> Path:
    cli = env.find_kicad_cli()
    bp = env.find_kicad_python(cli) if cli else None
    if bp is None:
        pytest.skip("KiCad (kicad-cli + bundled python) not installed")
    return bp


def _board(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / "blinky2.kicad_pcb"
    shutil.copy2(GOLDEN / "blinky2" / "blinky2.kicad_pcb", p)
    (d / "blinky2.kicad_pro").write_text('{"meta": {"filename": "x"}}',
                                         encoding="utf-8")
    return p


def _job(worker: str, pcb: Path) -> tuple[dict, Path]:
    """(job, the file the worker writes last). place/board print their
    result; route/update write job["result"] after the board save."""
    res = pcb.parent / "result.json"
    base = {"board": str(pcb), "out": str(pcb)}
    if worker == "board_swig":
        return {**base, "verb": "set_outline", "rect": [0, 0, 50, 40]}, pcb
    if worker == "place_swig":
        return {**base, "ops": [{"op": "move", "ref": "J1",
                                 "x": 10.0, "y": 10.0}]}, pcb
    if worker == "route_swig":  # no duplicates -> no save; result still lands
        return {**base, "verb": "dedup_copper", "result": str(res)}, res
    return {**base, "verb": "apply_update", "result": str(res)}, res


def _start(bp: Path, worker: str, job: dict, d: Path) -> subprocess.Popen:
    jp = d / "job.json"
    jp.write_text(json.dumps(job), encoding="utf-8")
    return subprocess.Popen([str(bp), str(SCRIPTS / "lib" / f"{worker}.py"),
                             str(jp)], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)


def _ok(proc: subprocess.Popen, job: dict) -> None:
    out, err = proc.communicate(timeout=180)
    assert proc.returncode == 0, (out[-2000:], err[-2000:])
    if "result" in job:
        assert json.loads(Path(job["result"]).read_text())["ok"] is True


@pytest.mark.smoke
@pytest.mark.parametrize("worker", ["board_swig", "place_swig",
                                    "route_swig", "update_swig"])
def test_worker_waits_for_the_board_writer_lock(bundled_python, worker,
                                                tmp_path):
    # free board: the worker runs straight through (the case that is kept)
    free = _board(tmp_path / "free")
    job, last = _job(worker, free)
    t0 = time.monotonic()
    _ok(_start(bundled_python, worker, job, free.parent), job)
    baseline = time.monotonic() - t0
    assert last.exists()

    # held board: the worker blocks until the holder lets go (suppressed)
    held = _board(tmp_path / "held")
    job, last = _job(worker, held)
    before = held.read_bytes()
    # well past the time a free run took, but under the worker's lock
    # timeout: past it the worker rightly refuses (LockBusy) instead of waiting
    hold = min(2 * baseline + 2.0, safelib.DEFAULT_LOCK_TIMEOUT / 2)
    with safelib.writer_lock(held, what="test holder"):
        proc = _start(bundled_python, worker, job, held.parent)
        time.sleep(hold)
        assert proc.poll() is None, "worker finished under a held lock"
        assert held.read_bytes() == before
        assert not (held.parent / "result.json").exists()
        released = time.time()
    _ok(proc, job)
    assert last.stat().st_mtime >= released - 0.01
