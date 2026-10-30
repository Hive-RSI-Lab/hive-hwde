"""keepout_gen: a placement keepout with "copper" becomes a KiCad rule area
(no tracks, vias or pours) - the ESP32 antenna keep-out lite-main
(hifi-receiver-2) drew with a board-local script."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import constraints_lint  # noqa: E402
import env  # noqa: E402
import geom  # noqa: E402
import keepout_gen  # noqa: E402
from checklib import CheckError  # noqa: E402

CU = ["F.Cu", "B.Cu"]


def test_plan_areas_from_copper_keepouts():
    cons = {"placement": {"keepouts": [
        {"rect": [7, 55, 0, 80], "reason": "ESP32 antenna", "copper": True},
        {"rect": [20, 20, 30, 30], "reason": "placement only"},
        {"poly": [[0, 0], [5, 0], [5, 5]], "side": "back", "name": "coil",
         "copper": {"tracks": True, "pours": False}},
    ]}}
    areas = keepout_gen.plan_areas(cons, CU)
    assert [a["name"] for a in areas] == ["aiee_keepout_ESP32_antenna",
                                          "aiee_keepout_coil"]
    ant, coil = areas
    assert ant["layers"] == CU and ant["rect"] == [0, 55, 7, 80]
    assert ant["tracks"] and ant["vias"] and ant["pours"]
    assert "pads" not in ant                      # pads stay allowed
    assert coil["layers"] == ["B.Cu"] and coil["poly"][2] == [5.0, 5.0]
    assert coil["tracks"] and not coil["vias"] and not coil["pours"]
    assert keepout_gen.plan_areas({}, CU) == []
    with pytest.raises(CheckError):
        keepout_gen.plan_areas({"placement": {"keepouts": [
            {"rect": [0, 0, 1, 1], "side": "top", "copper": True}]}}, CU)


def test_duplicate_names_are_made_unique():
    k = {"rect": [0, 0, 1, 1], "reason": "x", "copper": True}
    names = [a["name"] for a in keepout_gen.plan_areas(
        {"placement": {"keepouts": [k, dict(k)]}}, CU)]
    assert len(set(names)) == 2


def test_constraints_lint_knows_copper_keepouts(tmp_path):
    p = tmp_path / "constraints.json"
    p.write_text(json.dumps({"placement": {"keepouts": [
        {"rect": [0, 0, 1, 1], "copper": True, "name": "ant"},
        {"rect": [0, 0, 1, 1], "copper": {"tracks": True}}]},
        "power": [{"net": "VBUS", "current_a": 5, "plane_fed": True,
                   "tap_current_a": 0.5}]}))
    violations, _ = constraints_lint.lint_file(p)
    assert not violations, violations


@pytest.mark.smoke
def test_keepout_gen_draws_and_replaces(tmp_path):
    cli = env.find_kicad_cli()
    if cli is None or env.find_kicad_python(cli) is None:
        pytest.skip("KiCad not installed")
    src = REPO / "tests" / "golden" / "blinky2"
    pcbs = sorted(src.rglob("*.kicad_pcb"))
    if not pcbs:
        pytest.skip("no golden board")
    pcb = tmp_path / pcbs[0].name
    shutil.copy2(pcbs[0], pcb)
    (tmp_path / "constraints.json").write_text(json.dumps({"placement": {
        "keepouts": [{"rect": [1, 1, 4, 4], "reason": "ant",
                      "copper": True}]}}))
    for _ in range(2):                      # idempotent: replaced, not added
        payload, _ = keepout_gen.run(["--pcb", str(pcb)])
        assert payload["status"] == "pass"
    areas = [ra for ra in geom.BoardGeom.from_file(pcb).rule_areas
             if ra["name"].startswith("aiee_keepout")]
    assert [a["name"] for a in areas] == ["aiee_keepout_ant"]
    assert areas[0]["flags"].get("tracks") == "not_allowed"
    assert areas[0]["flags"].get("footprints") == "allowed"
    assert payload["facts"]["removed"] == 1
