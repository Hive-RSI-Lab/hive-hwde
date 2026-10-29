"""T6 p4: crystal-island placement template + warn-only membership audit.

Pure tests (no toolchain): template dispatch from placement.groups[].template,
symmetric load-cap slots with the shared-net pad facing its crystal pad (GND
outboard), resistor flanking, generic fallback on inference failure, and the
membership audit that would have caught the carrier's R35/R36 omission.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import placelib  # noqa: E402
import place_seed  # noqa: E402
import placetemplates  # noqa: E402
from geom import _rot  # noqa: E402


# ---- synthetic board helpers (test_place.py conventions) -------------------

def _fp(ref, x, y, pads="", cy=(-1.0, -1.0, 1.0, 1.0)):
    court = (f'    (fp_rect (start {cy[0]} {cy[1]}) (end {cy[2]} {cy[3]})'
             f' (stroke (width 0.05)) (fill no) (layer "F.CrtYd"))\n')
    return (f'  (footprint "t:{ref}" (layer "F.Cu")\n    (at {x} {y})\n'
            f'    (property "Reference" "{ref}" (at 0 0 0))\n'
            f'    (attr smd)\n{court}{pads})\n')


def _pad(num, x, y, net, size=0.6):
    return (f'    (pad "{num}" smd rect (at {x} {y}) (size {size} {size})'
            f' (layers "F.Cu") (net "{net}"))\n')


def _board(tmp_path_factory, name, body, w=40.0, h=30.0):
    text = f"""(kicad_pcb
  (version 20260206) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
  (setup)
  (gr_rect (start 0 0) (end {w} {h}) (stroke (width 0.1)) (fill no)
    (layer "Edge.Cuts"))
{body})
"""
    p = tmp_path_factory.mktemp(name) / f"{name}.kicad_pcb"
    p.write_text(text, encoding="utf-8")
    return p


def _xtal_board(tmp_path_factory, name="xtal"):
    body = _fp("Y1", 15, 10, _pad("1", -1.1, 0, "XI") + _pad("2", 1.1, 0, "XO"),
               cy=(-2, -1.5, 2, 1.5))
    body += _fp("C7", 25, 15, _pad("1", -0.5, 0, "XI") + _pad("2", 0.5, 0, "GND"))
    body += _fp("C8", 25, 5, _pad("1", -0.5, 0, "XO") + _pad("2", 0.5, 0, "GND"))
    body += _fp("R35", 5, 15, _pad("1", -0.5, 0, "XO") + _pad("2", 0.5, 0, "OSC_DRV"))
    body += _fp("R36", 5, 5, _pad("1", -0.5, 0, "XI") + _pad("2", 0.5, 0, "OSC2"))
    body += _fp("U1", 30, 10, _pad("1", -1, 0, "OSC_DRV") + _pad("2", 1, 0, "XI")
                + _pad("3", 0, 1, "GND"), cy=(-2, -2, 2, 2))
    return _board(tmp_path_factory, name, body)


GROUP = {"name": "xtal", "anchor": "Y1", "members": ["C7", "C8", "R35"],
         "template": "crystal"}
PL = {"groups": [GROUP]}


def _xtal_cluster(model, placement=PL):
    clusters, _w = placelib.build_clusters(model, {}, placement)
    return next(c for c in clusters if c.anchor == "Y1")


# ============================================================ template tag

def test_template_tag_reaches_cluster(tmp_path_factory):
    m = placelib.PlaceModel(_xtal_board(tmp_path_factory, "tag"))
    c = _xtal_cluster(m)
    assert c.template == "crystal"
    assert [s.ref for s in c.satellites] == ["C7", "C8", "R35"]
    # no template key -> None (generic behavior, backward compatible)
    pl2 = {"groups": [{k: v for k, v in GROUP.items() if k != "template"}]}
    c2 = _xtal_cluster(m, pl2)
    assert c2.template is None


# ============================================================ geometry

def test_crystal_caps_symmetric_gnd_outboard(tmp_path_factory):
    m = placelib.PlaceModel(_xtal_board(tmp_path_factory, "geom"))
    c = _xtal_cluster(m)
    warns: list[str] = []
    slots = place_seed.layout_satellites(m, c, warns)
    assert set(slots) == {"C7", "C8", "R35"}
    (s7, r7), (s8, r8) = slots["C7"], slots["C8"]
    # mirrored about the pad-pair midpoint (here the local origin), on-axis
    assert s7[0] == pytest.approx(-s8[0], abs=0.01)
    assert abs(s7[1]) < 0.01 and abs(s8[1]) < 0.01
    assert math.dist(s7, (0, 0)) == pytest.approx(math.dist(s8, (0, 0)),
                                                  abs=0.01)
    # shared-net pad faces its crystal pad -> C7 (XI, outboard -x) rot 180
    fp7 = m.footprints["C7"]
    xi = next(p for p in fp7.pads if p.net == "XI")
    cc = fp7.center_local()
    off = _rot(xi.local[0] - cc[0], xi.local[1] - cc[1], -r7)
    assert off[0] > 0        # XI pad points back toward the crystal (+x)
    # resistor flanks on the perpendicular axis
    s35, _r35 = slots["R35"]
    assert abs(s35[0]) < 0.01 and abs(s35[1]) > 1.0


def test_crystal_island_is_seed_legal(tmp_path_factory):
    pcb = _xtal_board(tmp_path_factory, "legal")
    con = {"placement": PL}
    ops, viol, facts, model = place_seed.seed(pcb, con, {}, 1.0, 0.8)
    assert viol == []
    assert placelib.legality_violations(model, PL) == []
    # island rides as one cluster: caps end adjacent to the crystal
    y1 = model.footprints["Y1"].center_abs()
    for ref in ("C7", "C8"):
        assert math.dist(model.footprints[ref].center_abs(), y1) < 5.0


# ============================================================ fallbacks

def test_unknown_template_warns_and_falls_back(tmp_path_factory):
    m = placelib.PlaceModel(_xtal_board(tmp_path_factory, "unk"))
    pl = {"groups": [{**GROUP, "template": "flux_capacitor"}]}
    c = _xtal_cluster(m, pl)
    warns: list[str] = []
    slots = place_seed.layout_satellites(m, c, warns)
    assert set(slots) == {"C7", "C8", "R35"}   # generic slotting still works
    assert any("unknown placement template" in w for w in warns)


def test_crystal_inference_failure_falls_back(tmp_path_factory):
    # anchor with ONE osc net (not two) -> warn + generic
    body = _fp("Y1", 15, 10, _pad("1", -1.1, 0, "XI") + _pad("2", 1.1, 0, "GND"))
    body += _fp("C7", 25, 15, _pad("1", -0.5, 0, "XI") + _pad("2", 0.5, 0, "GND"))
    m = placelib.PlaceModel(_board(tmp_path_factory, "onenet", body))
    pl = {"groups": [{"name": "x", "anchor": "Y1", "members": ["C7"],
                      "template": "crystal"}]}
    c = _xtal_cluster(m, pl)
    warns: list[str] = []
    slots = place_seed.layout_satellites(m, c, warns)
    assert set(slots) == {"C7"}
    assert any("generic slotting used" in w for w in warns)


# ============================================================ audit

def test_membership_audit_names_the_omission(tmp_path_factory):
    """The carrier R35/R36 class of failure: a 2-pad part on an oscillator
    net left out of the group is NAMED (warn-only, never fatal)."""
    m = placelib.PlaceModel(_xtal_board(tmp_path_factory, "audit"))
    warns: list[str] = []
    placetemplates.audit_groups(m, PL, warns)
    assert any("R36" in w and "not a member" in w for w in warns)
    # the driver IC (multi-pad) is legitimately not flagged
    assert not any("U1" in w for w in warns)
    # complete membership -> silent
    warns2: list[str] = []
    pl_full = {"groups": [{**GROUP, "members": ["C7", "C8", "R35", "R36"]}]}
    placetemplates.audit_groups(m, pl_full, warns2)
    assert warns2 == []


def test_audit_runs_inside_seed(tmp_path_factory):
    pcb = _xtal_board(tmp_path_factory, "auditseed")
    _ops, _viol, facts, _model = place_seed.seed(
        pcb, {"placement": PL}, {}, 1.0, 0.8)
    assert any("R36" in w for w in facts["warnings"])
