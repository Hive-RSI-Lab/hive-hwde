"""#910: the release gate fails when a connector check judged nothing.

The laptop board shipped with its microSD socket (J6, SIM-SMD_8P-1040310811)
facing backwards: no connector_mating.yaml family knew the part, so
check_mating judged no connector at all, check_connector_fit had no
extraction for any of J1-J10, and both read as a pass. Now verify_all adds a
connector_check_unjudged error that names the connectors under --strict (the
release gate); mid-pipeline runs keep it in coverage only. Only a waiver
naming every connector lets it through.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "tests"))
import gate  # noqa: E402
import verify_all  # noqa: E402
from test_mating import _board, _fp, _pad, small, usbc  # noqa: E402

KIND = "connector_check_unjudged"
# The laptop board's J6, a Molex 104031 microSD whose card mouth is at the
# solder tails: no family row knows the name.
SD_PADS = "".join(_pad(str(i + 1), -3.3 + 1.1 * i, 5.0, 0.7, 1.4)
                  for i in range(8))
SD_CY = (-7.0, -6.0, 7.0, 6.5)


def _check(name):
    return next(c for c in verify_all.CHECKS if c["name"] == name)


def _planted(tmp_path):
    """An unrecognised edge connector at the board edge, nothing else."""
    return _board(tmp_path, _fp("J6", "SIM-SMD_8P-1040310811", 15.0, 13.0,
                                pads=SD_PADS, cy=SD_CY)
                  + small("R1", 5.0, 5.0), w=30.0, h=20.0)


def _run(pcb, tmp_path, strict):
    rd = tmp_path / ("strict" if strict else "lenient")
    rd.mkdir()
    res = [verify_all.run_one(_check(n), {"pcb": str(pcb)}, rd, {}, strict)
           for n in verify_all.CONNECTOR_CHECKS]
    return res, verify_all.merge(pcb, res, {}, strict)


def _holes(m):
    return [v for v in m["violations"] if v.get("kind") == KIND]


def test_planted_unknown_edge_connector_fails_the_release_gate(tmp_path):
    pcb = _planted(tmp_path)
    res, m = _run(pcb, tmp_path, strict=True)
    by = {r["name"]: r for r in res}
    for name in verify_all.CONNECTOR_CHECKS:
        assert by[name]["nothing_to_judge"], name
        assert any(x.startswith("J6") for x in by[name]["not_judged"]), name
    holes = _holes(m)
    assert sorted(h["unjudged_by"] for h in holes) == \
        sorted(verify_all.CONNECTOR_CHECKS)
    assert all(h["severity"] == "error" and h["refs"] == ["J6"]
               for h in holes)
    assert "J6" in holes[0]["msg"] and "verify-waivers.json" in holes[0]["msg"]
    assert m["status"] == "violations"
    assert m["counts"]["by_check"]["verify_all"] == 2
    g = {"strict": False, "fail_severities": ["error"], "max_count": 0}
    assert gate.evaluate("verify", g, m)["status"] == "fail"


def test_outside_release_it_stays_in_coverage(tmp_path):
    _, m = _run(_planted(tmp_path), tmp_path, strict=False)
    assert _holes(m) == []
    cov = m["coverage"]
    assert set(verify_all.CONNECTOR_CHECKS) <= set(cov["nothing_to_judge"])
    g = {"strict": False, "fail_severities": ["error"], "max_count": 0}
    assert gate.evaluate("verify", g, m)["status"] == "pass"


def test_a_waiver_must_name_every_connector(tmp_path):
    _, m = _run(_planted(tmp_path), tmp_path, strict=True)
    g = {"strict": False, "fail_severities": ["error"], "max_count": 0}
    base = {"check": "verify_all", "kind": KIND, "net": None,
            "reason": "J6 microSD mouth checked by hand against the Molex "
                      "104031 drawing", "approved": "test 2026-11-13"}
    assert gate.evaluate("verify", g, m, [{**base, "refs": ["J6"]}])[
        "status"] != "fail"
    # naming some other part does not cover J6
    assert gate.evaluate("verify", g, m, [{**base, "refs": ["J1"]}])[
        "status"] == "fail"


def test_a_judged_connector_raises_nothing(tmp_path):
    """check_mating judges a known USB-C at the edge: no hole from it."""
    pcb = _board(tmp_path, usbc("J1", 15.0, 16.0) + small("R1", 5.0, 5.0))
    res, m = _run(pcb, tmp_path, strict=True)
    assert not [h for h in _holes(m) if h["unjudged_by"] == "check_mating"]


def test_a_board_without_connectors_is_not_flagged(tmp_path):
    pcb = _board(tmp_path, small("R1", 5.0, 5.0) + small("U1", 9.0, 5.0))
    _, m = _run(pcb, tmp_path, strict=True)
    assert _holes(m) == [] and m["status"] == "pass"


def test_merge_rule_on_results():
    def r(name, **kw):
        return {"name": name, "status": "pass", "counts": {"total": 0},
                "report": None, "violations": [], **kw}
    res = [r("check_mating", nothing_to_judge="no family",
             not_judged=["J2 (X)", "P3 (Y)"]),
           r("check_mate_pins", nothing_to_judge="no pairs",
             not_judged=["J9 (Z)"]),
           r("check_connector_fit", nothing_to_judge="none",
             not_judged=[]),
           r("check_straps", nothing_to_judge="no MCU")]
    m = verify_all.merge("b.kicad_pcb", res, strict=True)
    holes = _holes(m)
    assert len(holes) == 1 and holes[0]["refs"] == ["J2", "P3"]
    assert holes[0]["severity"] == "error"
    assert _holes(verify_all.merge("b.kicad_pcb", res)) == []
