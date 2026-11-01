"""check_body_clash (#608): 3D bodies against each other, their own courtyards,
the outline and mounting holes. Synthetic models; the known answer from a real
board is the shape of hifi core's RCA jack, an ear past a too-small courtyard.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import check_body_clash as cbc  # noqa: E402

IN = 2.54


def _wrl(path: Path, boxes, close=True) -> Path:
    shapes = []
    quads = [(0, 1, 3, 2), (4, 5, 7, 6), (0, 1, 5, 4), (2, 3, 7, 6),
             (0, 2, 6, 4), (1, 3, 7, 5)]
    if not close:
        quads = quads[:1]
    for x0, y0, z0, x1, y1, z1 in boxes:
        pts = [(x, y, z) for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)]
        p = ", ".join(" ".join(f"{v / IN:.6f}" for v in q) for q in pts)
        idx = ", ".join(",".join(str(i) for i in q) + ",-1" for q in quads)
        shapes.append("Shape { geometry IndexedFaceSet {\n coord Coordinate {"
                      f" point [ {p} ] }}\n coordIndex [ {idx} ]\n}} }}\n")
    path.write_text("#VRML V2.0 utf8\n" + "".join(shapes), encoding="utf-8")
    return path


def _fp(ref, x, y, model, *, angle=0, back=False, cy=None, pad=True, name="lib:P",
        extra=""):
    side = "B" if back else "F"
    court = ""
    if cy is not None:
        court = (f'(fp_rect (start {-cy} {-cy}) (end {cy} {cy}) (layer "{side}.CrtYd")'
                 ' (stroke (width 0.05)))')
    pads = f'(pad "1" smd rect (at 0 0) (size 0.5 0.5) (layers "{side}.Cu"))' if pad else ""
    mod = f'(model "{model}" (offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))' \
        if model else ""
    return (f'(footprint "{name}" (layer "{side}.Cu") (at {x} {y} {angle})\n'
            f' (property "Reference" "{ref}" (at 0 0) (layer "{side}.SilkS"))\n'
            f' {court} {pads} {extra} {mod})\n')


def _board(tmp_path, *fps):
    pcb = tmp_path / "b.kicad_pcb"
    pcb.write_text(
        '(kicad_pcb (version 20240108) (generator test)\n (general (thickness 1.6))\n'
        ' (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))\n'
        ' (gr_rect (start 0 0) (end 100 100) (layer "Edge.Cuts") (stroke (width 0.1)))\n'
        + "".join(fps) + ")\n", encoding="utf-8")
    return pcb


def _kinds(pcb, cons=None):
    viols, facts = cbc.check(pcb, cons)
    return sorted(v["kind"] for v in viols), viols, facts


# a jack: body +-8, and an ear reaching x +12 at 11-16 mm up, as on hifi core
JACK = [(-8, -8, 0, 8, 8, 20), (8, -2, 11, 12, 2, 16)]
POST = [(-4, -4, 0, 4, 4, 15)]


def test_the_ear_hits_the_neighbour_a_2d_check_cant_see(tmp_path):
    jack = _wrl(tmp_path / "jack.wrl", JACK)
    post = _wrl(tmp_path / "post.wrl", POST)
    # courtyards +-8 and +-4 stand 2 mm apart; the ear runs 2 mm into the post
    pcb = _board(tmp_path, _fp("J1", 50, 50, jack, cy=8),
                 _fp("J2", 64, 50, post, cy=4))
    kinds, viols, _ = _kinds(pcb)
    assert "body_clash" in kinds
    v = next(x for x in viols if x["kind"] == "body_clash")
    assert v["severity"] == "error" and v["refs"] == ["J1", "J2"]
    assert v["overlap"]["x"] == pytest.approx([60.0, 62.0], abs=0.25)
    assert v["overlap"]["z"] == pytest.approx([11.0, 15.0], abs=0.25)
    assert v["depth_mm"] == pytest.approx(2.0, abs=0.25)
    # and the cause: the courtyard left the ear out
    past = next(x for x in viols if x["kind"] == "body_past_courtyard")
    assert past["refs"] == ["J1"] and past["past_mm"] == pytest.approx(4.0, abs=0.2)


def test_moving_the_part_clears_it(tmp_path):
    jack = _wrl(tmp_path / "jack.wrl", JACK)
    post = _wrl(tmp_path / "post.wrl", POST)
    pcb = _board(tmp_path, _fp("J1", 50, 50, jack), _fp("J2", 67, 50, post))
    kinds, _, facts = _kinds(pcb)
    assert "body_clash" not in kinds and "body_tight" not in kinds
    assert facts["bodies"] == 2


def test_two_faces_touching_are_not_a_clash(tmp_path):
    a = _wrl(tmp_path / "a.wrl", [(-2, -2, 0, 2, 2, 2)])
    pcb = _board(tmp_path, _fp("R1", 50, 50, a), _fp("R2", 54, 50, a))
    kinds, _, _ = _kinds(pcb)
    assert "body_clash" not in kinds


def test_a_part_wholly_inside_another_is_a_clash(tmp_path):
    big = _wrl(tmp_path / "big.wrl", [(-5, -5, 0, 5, 5, 10)])
    small = _wrl(tmp_path / "small.wrl", [(-1, -1, 2, 1, 1, 4)])
    pcb = _board(tmp_path, _fp("U1", 50, 50, big), _fp("U2", 50, 50, small))
    kinds, viols, _ = _kinds(pcb)
    assert "body_clash" in kinds
    assert next(v for v in viols if v["kind"] == "body_clash")["depth_mm"] >= 1.5


def test_a_part_under_an_overhang_is_clear(tmp_path):
    # a low chip under the ear: the columns overlap, the solids don't
    jack = _wrl(tmp_path / "jack.wrl", JACK)
    chip = _wrl(tmp_path / "chip.wrl", [(-1, -1, 0, 1, 1, 2)])
    pcb = _board(tmp_path, _fp("J1", 50, 50, jack, cy=13), _fp("C1", 60, 50, chip))
    kinds, _, _ = _kinds(pcb)
    assert "body_clash" not in kinds


def test_a_pin_tail_meets_a_part_on_the_far_side(tmp_path):
    hdr = _wrl(tmp_path / "hdr.wrl", [(-0.4, -0.4, -5, 0.4, 0.4, 8)])
    chip = _wrl(tmp_path / "chip.wrl", [(-2, -2, 0, 2, 2, 2)])
    pcb = _board(tmp_path, _fp("J1", 50, 50, hdr), _fp("U1", 50, 50, chip, back=True))
    kinds, viols, _ = _kinds(pcb)
    assert "body_clash" in kinds
    z = next(v for v in viols if v["kind"] == "body_clash")["overlap"]["z"]
    assert z[0] == pytest.approx(-3.6, abs=0.25) and z[1] <= -1.5


def test_the_far_side_clear_of_a_short_tail_passes(tmp_path):
    hdr = _wrl(tmp_path / "hdr.wrl", [(-0.4, -0.4, -1.5, 0.4, 0.4, 8)])
    chip = _wrl(tmp_path / "chip.wrl", [(-2, -2, 0, 2, 2, 2)])
    pcb = _board(tmp_path, _fp("J1", 50, 50, hdr), _fp("U1", 50, 50, chip, back=True))
    kinds, _, _ = _kinds(pcb)
    assert "body_clash" not in kinds


def test_rotation_turns_the_ear(tmp_path):
    jack = _wrl(tmp_path / "jack.wrl", JACK)
    post = _wrl(tmp_path / "post.wrl", POST)
    # at 90 the footprint's +x points at board -y (file frame, Y down)
    pcb = _board(tmp_path, _fp("J1", 50, 50, jack, angle=90), _fp("J2", 50, 36, post))
    kinds, _, _ = _kinds(pcb)
    assert "body_clash" in kinds
    pcb = _board(tmp_path, _fp("J1", 50, 50, jack, angle=270), _fp("J2", 50, 36, post))
    kinds, _, _ = _kinds(pcb)
    assert "body_clash" not in kinds


def test_tall_parts_almost_touching_warn(tmp_path):
    a = _wrl(tmp_path / "a.wrl", [(-4, -4, 0, 4, 4, 12)])
    pcb = _board(tmp_path, _fp("J1", 50, 50, a), _fp("J2", 58.15, 50, a))
    kinds, viols, _ = _kinds(pcb)
    assert "body_tight" in kinds and "body_clash" not in kinds
    assert next(v for v in viols if v["kind"] == "body_tight")["gap_mm"] \
        == pytest.approx(0.15, abs=0.1)
    low = _wrl(tmp_path / "low.wrl", [(-4, -4, 0, 4, 4, 2)])
    pcb = _board(tmp_path, _fp("C1", 50, 50, low), _fp("C2", 58.15, 50, low))
    assert "body_tight" not in _kinds(pcb)[0]


def test_an_allowed_overlap_needs_its_why(tmp_path):
    big = _wrl(tmp_path / "big.wrl", [(-5, -5, 0, 5, 5, 10)])
    pcb = _board(tmp_path, _fp("BT1", 50, 50, big), _fp("J9", 52, 50, big))
    cons = {"mechanical": {"allow_body_overlap": [
        {"refs": ["J9", "BT1"], "why": "the cell sits in its holder"}]}}
    kinds, _, facts = _kinds(pcb, cons)
    assert "body_clash" not in kinds
    assert list(facts["allowed_overlaps"].values()) == ["the cell sits in its holder"]
    import checklib
    with pytest.raises(checklib.CheckError):
        cbc.check(pcb, {"mechanical": {"allow_body_overlap": [{"refs": ["J9", "BT1"]}]}})


def test_a_connector_mouth_past_the_edge_isnt_past_its_courtyard(tmp_path):
    sma = _wrl(tmp_path / "sma.wrl", [(-3, -3, 0, 12, 3, 6)])
    pcb = _board(tmp_path, _fp("J1", 95, 50, sma, cy=5.5))
    kinds, _, _ = _kinds(pcb)
    assert "body_past_courtyard" not in kinds and "body_off_board" not in kinds
    # the same overhang on something that isn't a connector
    pcb = _board(tmp_path, _fp("U1", 95, 50, sma, cy=20))
    assert "body_off_board" in _kinds(pcb)[0]


def test_a_body_over_a_mounting_hole(tmp_path):
    chip = _wrl(tmp_path / "chip.wrl", [(-1, -1, 0, 1, 1, 2)])
    hole = '(pad "" np_thru_hole circle (at 0 0) (size 3.2 3.2) (drill 3.2) (layers "*.Cu"))'
    # 1 mm of the chip inside the 5.76 mm head circle
    near = _fp("C1", 53.5, 50, chip)
    far = _fp("C1", 60, 50, chip)
    mh = _fp("H1", 50, 50, None, pad=False, extra=hole, name="lib:MountingHole_3.2mm")
    kinds, viols, _ = _kinds(_board(tmp_path, mh, near))
    assert "body_over_hole" in kinds
    assert next(v for v in viols if v["kind"] == "body_over_hole")["refs"] == ["C1", "H1"]
    assert "body_over_hole" not in _kinds(_board(tmp_path, mh, far))[0]
    # a high overhang only matters inside the drill's own width
    ear = _wrl(tmp_path / "ear.wrl", [(-1, -1, 8, 1, 1, 9)])
    kinds, _, _ = _kinds(_board(tmp_path, mh, _fp("U1", 53.5, 50, ear)))
    assert "body_over_hole" not in kinds
    kinds, _, _ = _kinds(_board(tmp_path, mh, _fp("U1", 51.5, 50, ear)))
    assert "body_over_hole" in kinds


def test_no_model_is_not_a_pass(tmp_path):
    pcb = _board(tmp_path, _fp("J1", 50, 50, tmp_path / "gone.wrl"), _fp("R1", 60, 50, None))
    kinds, viols, facts = _kinds(pcb)
    assert kinds == ["body_unjudged"] and viols[0]["refs"] == ["J1"]
    assert facts["not_judged"] == ["J1", "R1"] and facts["bodies"] == 0
    payload, _ = cbc.run(["--pcb", str(pcb)])
    assert payload["nothing_to_judge"]


def test_model_dir_finds_a_model_by_name(tmp_path):
    d = tmp_path / "other.3dshapes"
    d.mkdir()
    _wrl(d / "m.wrl", [(-1, -1, 0, 1, 1, 1)])
    pcb = _board(tmp_path, _fp("J1", 50, 50, "${KIPRJMOD}/nowhere/m.wrl"))
    assert cbc.check(pcb)[1]["bodies"] == 0
    assert cbc.check(pcb, None, [d])[1]["bodies"] == 1


def test_an_open_shell_is_reported_and_its_crossing_warned(tmp_path):
    sheet = _wrl(tmp_path / "s.wrl", [(-4, -4, 3, 4, 4, 3.0001)], close=False)
    solid = _wrl(tmp_path / "t.wrl", [(-1, -1, 0, 1, 1, 8)])
    # a real closed body has an inside; the lone sheet is thin so reads sealed
    pcb = _board(tmp_path, _fp("U1", 50, 50, sheet), _fp("U2", 50, 50, solid))
    assert cbc.check(pcb)[1]["bodies"] == 2
    box = _wrl(tmp_path / "u.wrl", [(-4, -4, 0, 4, 4, 8)], close=False)
    pcb = _board(tmp_path, _fp("U1", 50, 50, box))
    assert cbc.check(pcb)[1]["open_shells"] == []    # a flat face is thin too


def test_rasterise_fills_a_box_exactly():
    tris = []
    x0, y0, z0, x1, y1, z1 = 0, 0, 0, 4, 2, 1
    pts = np.array([(x, y, z) for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)],
                   dtype=np.float32)
    for q in ((0, 1, 3, 2), (4, 5, 7, 6), (0, 1, 5, 4), (2, 3, 7, 6),
              (0, 2, 6, 4), (1, 3, 7, 5)):
        tris += [(pts[q[0]], pts[q[1]], pts[q[2]]), (pts[q[0]], pts[q[2]], pts[q[3]])]
    b = cbc.Body("X", False, (0, 0), np.array(tris), (0.0, 0.0, 0.0, 4.0, 2.0, 1.0))
    cbc.rasterise(b)
    assert b.sealed
    # 40 x 20 x 10 cells of solid, give or take a cell of surface each side
    assert 40 * 20 * 10 <= int(b.cells.sum()) <= 42 * 22 * 12
    assert not b.cells[0, 0, 0]
