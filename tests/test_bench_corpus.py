"""bench.py --corpus (lib/benchcorpus.py): the placement benchmark driver.

Offline: a fake runner stands in for pcbnew, the placer and the router, so
these tests check the driver's bookkeeping, not the pipeline.
"""
from __future__ import annotations

import fcntl
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import benchcorpus  # noqa: E402
from checklib import CheckError  # noqa: E402


def _corpus(tmp_path: Path) -> Path:
    root = tmp_path / "boards"
    for name in ("PCB-0001-A_a", "PCB-0002-A_b"):
        (root / name / "kicad" / "route").mkdir(parents=True)
        (root / name / "kicad" / f"{name}.kicad_pcb").write_text(
            "(kicad_pcb\n\t(segment\n\t\t(start 0 0)\n\t)\n)")
    (root / "PCB-0003-A_nopcb" / "kicad").mkdir(parents=True)
    return root


class FakeRunner:
    """Answers each stage the way the real script reports."""

    def __init__(self, left=False, wx_fails=0):
        self.left, self.wx_fails, self.calls = left, wx_fails, []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        out = ""
        joined = " ".join(argv)
        if "-c" in argv:
            out = "stripped=7\n"
            if not self.left:
                Path(argv[-1]).write_text("(kicad_pcb\n\t(gr_line)\n)")
        elif "place_metrics.py" in joined:
            dest = Path(argv[argv.index("--out") + 1])
            dest.write_text(json.dumps({"status": "pass", "counts": {"total": 0},
                                        "metrics": {"hpwl": {"total_mm": 50.0},
                                                    "crossings": {"count": 1}}}))
        elif "place_seed.py" in joined:
            out = json.dumps({"status": "pass", "counts": {"total": 0}}) + "\n"
        elif "place_anneal.py" in joined:
            out = json.dumps({"status": "error",
                              "error": "CheckError: no legal candidate"}) + "\n"
        elif "route_auto.py" in joined:
            if self.wx_fails:
                self.wx_fails -= 1
                return subprocess.CompletedProcess(argv, 1, "wxEntryStart failed\n", "")
            dest = Path(argv[argv.index("--out-report") + 1])
            dest.write_text(json.dumps({
                "status": "violations",
                "facts": {"completion": 0.5, "fr_completion": 1.0,
                          "routable_nets": 2, "unrouted_nets": ["/RF"]},
                "violations": [{"check": "clearance", "severity": "error"},
                               {"check": "clearance", "severity": "error"},
                               {"check": "silk_overlap", "severity": "warning"}]}))
        return subprocess.CompletedProcess(argv, 0, out, "")


class FixedLoad:
    def __init__(self, load):
        self.load = load

    def start(self):
        return self

    def stop(self):
        return {"max": self.load, "mean": self.load, "samples": 1,
                "limit": benchcorpus.LOAD_LIMIT}


def _run_board(tmp_path, runner, load=3.0):
    root = _corpus(tmp_path)
    return benchcorpus.run_board(
        root / "PCB-0001-A_a", tmp_path / "work", scripts=SCRIPTS,
        venv_py="py", bundled_py="kpy", env={}, runner=runner,
        sampler=FixedLoad(load))


def test_corpus_boards_needs_a_pcb(tmp_path):
    names = [b.name for b in benchcorpus.corpus_boards(_corpus(tmp_path))]
    assert names == ["PCB-0001-A_a", "PCB-0002-A_b"]


def test_run_board_records_result(tmp_path):
    r = _run_board(tmp_path, FakeRunner())
    saved = json.loads((tmp_path / "work" / "PCB-0001-A_a" / "result.json").read_text())
    assert saved == r
    assert r["stripped"] == 7
    assert r["route"]["completion"] == 0.5
    assert r["route"]["unrouted_nets"] == ["/RF"]
    assert r["drc"] == {"errors": 2, "warnings": 1,
                        "errors_by_check": {"clearance": 2}}
    # the anneal's error comes from its stdout when it wrote no report
    assert r["anneal"]["error"] == "CheckError: no legal candidate"
    assert r["designer"]["hpwl_mm"] == 50.0
    # stale work dirs from the copy are gone; the boards repo is untouched
    assert not (tmp_path / "work" / "PCB-0001-A_a" / "kicad" / "route").exists()
    assert (tmp_path / "boards" / "PCB-0001-A_a" / "kicad" / "route").exists()


def test_runtime_valid_only_below_load_limit(tmp_path):
    assert _run_board(tmp_path / "q", FakeRunner(), load=11.9)["runtime_valid"] is True
    assert _run_board(tmp_path / "b", FakeRunner(), load=12.0)["runtime_valid"] is False


def test_strip_leftover_stops_before_placing(tmp_path):
    runner = FakeRunner(left=True)
    r = _run_board(tmp_path, runner)
    assert "copper strip failed" in r["error"]
    assert not any("place_seed.py" in " ".join(c) for c in runner.calls)


def test_route_retried_on_wx_entry_start(tmp_path):
    r = _run_board(tmp_path / "one", FakeRunner(wx_fails=1))
    assert r["stages"]["route"]["tries"] == 2
    assert r["route"]["completion"] == 0.5
    r = _run_board(tmp_path / "none", FakeRunner())
    assert r["stages"]["route"]["tries"] == 1


class RouteTimeout(FakeRunner):
    def __call__(self, argv, **kw):
        if "route_auto.py" in " ".join(argv):
            raise subprocess.TimeoutExpired(argv, benchcorpus.STAGE_TIMEOUT_S)
        return super().__call__(argv, **kw)


def test_route_timeout_keeps_seed_and_anneal(tmp_path):
    r = _run_board(tmp_path, RouteTimeout())
    assert "timed out" in r["error"]
    # the stages that ran keep their results; only route is empty
    assert r["seed"]["status"] == "pass"
    assert r["anneal"]["error"] == "CheckError: no legal candidate"
    assert r["route"]["status"] is None
    # a board that fails before seeding has no seed or anneal result
    r = _run_board(tmp_path / "left", FakeRunner(left=True))
    assert r["seed"]["status"] is None and r["anneal"]["status"] is None


def test_run_board_records_a_board_that_lost_its_pcb(tmp_path):
    root = _corpus(tmp_path)
    runner = FakeRunner()
    r = benchcorpus.run_board(
        root / "PCB-0003-A_nopcb", tmp_path / "work", scripts=SCRIPTS,
        venv_py="py", bundled_py="kpy", env={}, runner=runner,
        sampler=FixedLoad(3.0))
    assert "no kicad/*.kicad_pcb" in r["error"] and runner.calls == []
    saved = benchcorpus.result_path(tmp_path / "work", "PCB-0003-A_nopcb")
    assert json.loads(saved.read_text())["error"] == r["error"]
    # a board whose whole dir was renamed away is recorded the same way
    gone = benchcorpus.run_board(
        root / "PCB-0004-A_renamed", tmp_path / "work", scripts=SCRIPTS,
        venv_py="py", bundled_py="kpy", env={}, runner=runner,
        sampler=FixedLoad(3.0))
    assert "no kicad/*.kicad_pcb" in gone["error"] and runner.calls == []
    saved = benchcorpus.result_path(tmp_path / "work", "PCB-0004-A_renamed")
    assert json.loads(saved.read_text())["error"] == gone["error"]
    # a board that still has its pcb runs its stages
    r = _run_board(tmp_path / "ok", runner)
    assert r.get("error") is None and runner.calls


def _fake_one(board, work, **kw):
    d = Path(work) / board.name
    d.mkdir(parents=True, exist_ok=True)
    res = {"board": board.name}
    benchcorpus.result_path(work, board.name).write_text(json.dumps(res))
    return res


def test_run_corpus_skips_done_boards_unless_rerun(tmp_path):
    root, work = _corpus(tmp_path), tmp_path / "work"
    (work / "PCB-0001-A_a").mkdir(parents=True)
    benchcorpus.result_path(work, "PCB-0001-A_a").write_text("{}")
    s = benchcorpus.run_corpus(root, work, nice=0, xvfb=False, run_one=_fake_one)
    assert s["skipped"] == ["PCB-0001-A_a"]
    assert [r["board"] for r in s["ran"]] == ["PCB-0002-A_b"]
    s = benchcorpus.run_corpus(root, work, rerun=["PCB-0001-A_a"], nice=0,
                               xvfb=False, run_one=_fake_one)
    assert s["skipped"] == ["PCB-0002-A_b"]
    assert [r["board"] for r in s["ran"]] == ["PCB-0001-A_a"]


def test_run_corpus_refuses_unknown_board_and_held_lock(tmp_path):
    root, work = _corpus(tmp_path), tmp_path / "work"
    with pytest.raises(CheckError, match="no board"):
        benchcorpus.run_corpus(root, work, boards=["PCB-0003-A_nopcb"], nice=0,
                               xvfb=False, run_one=_fake_one)
    work.mkdir(exist_ok=True)
    with open(work / ".lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(CheckError, match="holds"):
            benchcorpus.run_corpus(root, work, nice=0, xvfb=False,
                                   run_one=_fake_one)
