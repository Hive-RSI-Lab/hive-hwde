"""silk_fix and place_edit silk_clear {over_pads, edge_mm, at, uuid}: the
silk cleanup pinned core and lite-main scripted by hand (kicad/gen/
silk_clean.py, silk_clear.py, silk_edge.py).

The live tests run on a copy of pinned core, the board that needed the
scripts, with the faults those scripts fixed put back by hand: C1's
reference moved onto its pads, a footprint silk line across its pads and
one past the board edge. Each fix must bring DRC back to the pinned board's
counts (silk findings gone, nothing else moved).
"""
from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import env  # noqa: E402
import place_edit  # noqa: E402
import silk_fix  # noqa: E402
from checklib import CheckError  # noqa: E402

CORE = REPO / "pinned" / "core" / "kicad"


# ------------------------------------------------------------ plan (no KiCad)

def _v(check, *items):
    return {"check": check, "severity": "warning",
            "items": [dict(zip(("msg", "pos", "uuid"), it)) for it in items]}


def test_plan_maps_each_silk_finding_to_one_op():
    viol = [
        _v("silk_over_copper", ("Reference field of C1", [1, 2], "u-ref"),
           ("Pad 1 [GND] of C1 on F.Cu", [1, 2], "u-pad")),
        _v("silk_overlap", ("Reference field of C1", [1, 2], "u-ref"),
           ("Segment of C1 on F.Silkscreen", [3, 4], "u-a")),
        _v("silk_edge_clearance", ("Segment on Edge.Cuts", [0, 0], "u-e"),
           ("Arc of U2 on B.Silkscreen", [5, 6], "u-b")),
        _v("silk_over_copper", ("Segment of C1 on F.Silkscreen", [7, 8], ""),
           ("Pad 2 of C1 on F.Cu", [7, 8], "u-p2")),
        _v("silk_overlap", ("Value field of R3", [9, 9], "u-v"),
           ("Text on F.Silkscreen", [9, 9], "u-t")),
        _v("clearance", ("Track [GND] on F.Cu", [0, 0], "x"),
           ("Segment of C1 on F.Silkscreen", [0, 0], "never")),
    ]
    ops, left = silk_fix.plan(viol)
    assert ops == [
        {"op": "set_text", "ref": "C1", "field": "reference", "hide": True},
        {"op": "set_text", "ref": "R3", "field": "value", "hide": True},
        {"op": "silk_clear", "ref": "C1", "layer": "F.SilkS",
         "uuid": ["u-a"]},
        {"op": "silk_clear", "ref": "U2", "layer": "B.SilkS",
         "uuid": ["u-b"]},
        {"op": "silk_clear", "ref": "C1", "layer": "F.SilkS", "at": [7, 8]},
    ]
    assert left == ["silk_overlap: Text on F.Silkscreen"]
    assert len(place_edit.validate_ops({"version": 1, "ops": ops})) == 5


def test_report_shapes():
    v = [_v("silk_overlap", ("Reference field of C1", [0, 0], "u"))]
    assert silk_fix._violations({"violations": v}) == v
    assert silk_fix._violations({"failing": v}) == v
    with pytest.raises(CheckError, match="no 'violations'"):
        silk_fix._violations({"counts": {}})


def test_silk_clear_validation():
    good = [{"op": "silk_clear", "ref": "C1", "over_pads": True},
            {"op": "silk_clear", "ref": "C1", "edge_mm": 0.25},
            {"op": "silk_clear", "ref": "C1", "at": [1.0, 2]},
            {"op": "silk_clear", "ref": "C1", "uuid": ["abc"]}]
    assert len(place_edit.validate_ops({"version": 1, "ops": good})) == 4
    for bad, msg in ((("over_pads", 1), "over_pads must be a boolean"),
                     (("edge_mm", -1), "edge_mm must be"),
                     (("edge_mm", True), "edge_mm must be"),
                     (("at", [1]), "at must be"),
                     (("uuid", "abc"), "uuid must be"),
                     (("uuid", []), "uuid must be")):
        with pytest.raises(CheckError, match=msg):
            place_edit.validate_ops({"version": 1, "ops": [
                {"op": "silk_clear", "ref": "C1", bad[0]: bad[1]}]})


# ------------------------------------------------------------ live

@pytest.fixture
def kicad():
    cli = env.find_kicad_cli()
    if cli is None or env.find_kicad_python(cli) is None:
        pytest.skip("KiCad bundled python not installed")
    return cli


def _line(x0, y0, x1, y1) -> str:
    return (f'\t\t(fp_line\n\t\t\t(start {x0} {y0})\n\t\t\t(end {x1} {y1})\n'
            '\t\t\t(stroke\n\t\t\t\t(width 0.15)\n\t\t\t\t(type solid)\n'
            f'\t\t\t)\n\t\t\t(layer "F.SilkS")\n\t\t\t(uuid "{uuid.uuid4()}")'
            '\n\t\t)\n')


def _faulty_core(tmp_path: Path) -> tuple[Path, Path]:
    """A clean copy of pinned core and a copy with C1's silk broken the
    way the core scripts found it: the refdes on its pads, a footprint line
    across the pads and one past the left board edge (x 34.59)."""
    clean = tmp_path / "clean"
    shutil.copytree(CORE, clean)
    bad = tmp_path / "kicad"
    shutil.copytree(CORE, bad)
    pcb = bad / "core.kicad_pcb"
    s = pcb.read_text(encoding="utf-8")
    ref = s.index('(property "Reference" "C1"')
    k = s.index("(at ", ref)
    s = s[:k] + "(at 0 0 90)" + s[s.index(")", k) + 1:]       # C1 at 43.166
    a = s.index("\t\t(attr", ref)
    s = s[:a] + _line(-1.2, -0.6, 1.2, 0.6) + _line(-9, 0, -8.4, 0) + s[a:]
    pcb.write_text(s, encoding="utf-8")
    return clean / "core.kicad_pcb", pcb


def _silk(counts_by_check: dict) -> int:
    return sum(n for c, n in counts_by_check.items()
               if c in silk_fix.SILK_CHECKS)


def test_live_silk_fix_clears_the_findings_on_a_copy_of_core(kicad,
                                                              tmp_path):
    import kc
    clean, pcb = _faulty_core(tmp_path)
    base = kc.run_drc(kicad, clean)
    found = silk_fix._by_check(kc.run_drc(kicad, pcb)["violations"])
    assert found.get("silk_over_copper") and found.get("silk_overlap") \
        and found.get("silk_edge_clearance")

    payload, _ = silk_fix.run(["--pcb", str(pcb)])
    assert payload["status"] == "pass", payload
    assert payload["silk_before"] == _silk(found) and payload["silk_after"] == 0
    assert payload["left"] == []
    assert [r.get("removed") for r in payload["results"]] == [None, 2]
    assert kc.run_drc(kicad, pcb)["counts"] == base["counts"]

    again, _ = silk_fix.run(["--pcb", str(pcb)])       # nothing left to do
    assert again["status"] == "pass" and again["applied"] == 0


def test_live_over_pads_and_edge_mm_on_a_copy_of_core(kicad, tmp_path):
    """silk_clear.py and silk_edge.py as place_edit ops."""
    import kc
    clean, pcb = _faulty_core(tmp_path)
    base = kc.run_drc(kicad, clean)
    ops = [{"op": "silk_clear", "ref": "C1", "over_pads": True},
           {"op": "silk_clear", "ref": "C1", "edge_mm": 0.25},
           {"op": "set_text", "ref": "C1", "field": "reference",
            "hide": True}]
    payload, _ = place_edit.run(["--pcb", str(pcb), "--op", json.dumps(ops)])
    over, edge, _ = payload["results"]
    assert over["removed"] >= 1 and edge["removed"] == 1
    assert kc.run_drc(kicad, pcb)["counts"] == base["counts"]
    # the rest of C1's outline (away from pads and edge) is still there
    text = pcb.read_text(encoding="utf-8")
    c1 = text[text.index('(property "Reference" "C1"'):]
    c1 = c1[:c1.index("\n\t)")]
    assert '(start 1.96 0.75)' in c1
