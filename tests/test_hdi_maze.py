"""#477: the route_finish maze on escaped BGAs, one small board per package.

Each fixture (tests/fixtures/hdi_maze, see its README) is a BGA already through
route_escape, with a connector to reach. The answers are what the maze does on
it with the escape left in place (--no-rip-board): completion, DRC errors and
warnings, and what is still unrouted. They are measured, not goals.

Warning counts move with KiCad (conftest's RECORDED_KICAD is older than the
10.0.6 these were measured on), so they are compared only on RECORDED; the
unconnected and error counts hold on any version. holes_co_located is left out
everywhere: on the same copper KiCad counts it by the order of the vias in the
file, and route_finish does not keep that order from run to run. Shuffling one
board's vias gave 8 to 16; its unchanged order 4, another run's 6.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import env  # noqa: E402
import route_finish as rf  # noqa: E402

FIXTURES = REPO / "tests" / "fixtures" / "hdi_maze"
RECORDED = "10.0.6"


def _need_tools():
    if env.find_kicad_cli() is None or env.find_krt() is None:
        pytest.skip("kicad-cli or KRT not installed")


def _maze(tmp_path, case, *flags):
    for f in (FIXTURES / case).iterdir():
        shutil.copy(f, tmp_path / f.name)
    payload, _ = rf.run(["--pcb", str(tmp_path / "b.kicad_pcb"), *flags,
                         "--work-dir", str(tmp_path / "work"),
                         "--out-report", str(tmp_path / "r.json")])
    return payload


def _warnings(payload):
    found = {}
    for v in payload["violations"]:
        if v.get("severity") == "warning" and v["check"] != "holes_co_located":
            found[v["check"]] = found.get(v["check"], 0) + 1
    return found


def _answers(payload, *, before, after, warnings):
    f = payload["facts"]
    assert f["unconnected_before"] == before
    assert f["unconnected_after"] == after
    assert f["errors_after"] == {}
    ver = subprocess.run([str(env.find_kicad_cli()), "version"],
                         capture_output=True, text=True).stdout
    if re.match(r"\d+\.\d+\.\d+", ver).group(0) == RECORDED:
        assert _warnings(payload) == warnings


@pytest.mark.parametrize("case,nets", [
    ("wlcsp49_0.4", 49), ("fbga153_0.5", 153), ("tfbga54_0.8", 54)])
def test_live_fixture_starts_escaped_with_every_net_open(tmp_path, case, nets):
    _need_tools()
    payload = _maze(tmp_path, case, "--measure-only")
    assert payload["facts"]["unconnected_left"] == nets
    assert payload["counts"]["by_severity"].get("error", 0) == nets


def test_live_tfbga54_maze_finishes(tmp_path):
    _need_tools()
    payload = _maze(tmp_path, "tfbga54_0.8", "--no-rip-board")
    assert [p["by_status"] for p in payload["facts"]["passes"]] == [{"routed": 54}]
    _answers(payload, before=54, after=0, warnings={
        "track_dangling": 32, "lib_footprint_issues": 2, "silk_over_copper": 2})


@pytest.mark.smoke
def test_live_wlcsp49_maze_stops_at_18_of_49(tmp_path):
    """Smoke, not test-slow: 29 to 39 min on the loaded tower. 63.3 % routed; pass 3
    gains nothing, so the maze stops there. The 18 left are real work for the
    escape or the router, not a goal."""
    _need_tools()
    payload = _maze(tmp_path, "wlcsp49_0.4", "--no-rip-board")
    assert [p["by_status"] for p in payload["facts"]["passes"]] == [
        {"routed": 30, "failed": 18, "rolled_back": 1},
        {"failed": 18, "routed": 1},
        {"failed": 18}]
    _answers(payload, before=49, after=18, warnings={
        "track_dangling": 37, "silk_over_copper": 15, "lib_footprint_issues": 2})


@pytest.mark.smoke
def test_live_fbga153_maze_stops_at_83_of_153(tmp_path):
    """Smoke, not test-slow: 4 h on the loaded tower, measured once. 45.8 % routed;
    pass 3 gains nothing, so the maze stops there. The 83 left are real work for
    the escape or the router, not a goal."""
    _need_tools()
    payload = _maze(tmp_path, "fbga153_0.5", "--no-rip-board")
    assert [p["by_status"] for p in payload["facts"]["passes"]] == [
        {"routed": 65, "failed": 88},
        {"failed": 83, "routed": 5},
        {"failed": 83}]
    _answers(payload, before=153, after=83, warnings={
        "track_dangling": 126, "via_dangling": 1,
        "lib_footprint_issues": 2, "silk_over_copper": 2})
