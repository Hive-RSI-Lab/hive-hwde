"""Every footprint's 3D model sits on its own pads (check_model_fit.py and
lib/modelgeom.py). The hit: EasyEDA's TYPE-C-31-M-12 model sits 2.2 mm off
lite-main J1's footprint. Everything here runs on small synthetic models;
one test reads a stock KiCad STEP and skips without it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import check_model_fit as cmf  # noqa: E402
import modelgeom  # noqa: E402

IN = 2.54  # mm per VRML unit


def _cube(x0, y0, z0, x1, y1, z1):
    """A box's 8 corners and 6 quads, model frame (mm, Y up)."""
    pts = [(x, y, z) for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)]
    quads = [(0, 1, 3, 2), (4, 5, 7, 6), (0, 1, 5, 4), (2, 3, 7, 6),
             (0, 2, 6, 4), (1, 3, 7, 5)]
    return pts, quads


def _wrl(path: Path, boxes) -> Path:
    shapes = []
    for b in boxes:
        pts, quads = _cube(*b)
        p = ", ".join(" ".join(f"{v / IN:.6f}" for v in q) for q in pts)
        idx = ", ".join(",".join(str(i) for i in q) + ",-1" for q in quads)
        shapes.append("Shape { geometry IndexedFaceSet {\n coord Coordinate {"
                      f" point [ {p} ] }}\n coordIndex [ {idx} ]\n}} }}\n")
    path.write_text("#VRML V2.0 utf8\n" + "".join(shapes), encoding="utf-8")
    return path


# a 1206-ish chip: terminals on the board at x +-1.3..1.6, a raised body
CHIP = [(-1.6, -0.8, 0.0, -1.1, 0.8, 0.6), (1.1, -0.8, 0.0, 1.6, 0.8, 0.6),
        (-1.1, -0.8, 0.05, 1.1, 0.8, 0.6)]
# a SOT-23: two leads at y -1.1 (model frame, Y up), one at y +1.1
SOT = [(-1.2, -1.3, 0.0, -0.7, -0.9, 0.1), (0.7, -1.3, 0.0, 1.2, -0.9, 0.1),
       (-0.25, 0.9, 0.0, 0.25, 1.3, 0.1), (-1.45, -0.65, 0.1, 1.45, 0.65, 1.0)]
# a 1x3 header: pins 0.64 square to 3 mm under the board at y 0, 2.54, 5.08
# (file frame, Y down: model y 0, -2.54, -5.08)
HDR = [(-0.32, -y - 0.32, -3.0, 0.32, -y + 0.32, 8.5) for y in (0, 2.54, 5.08)] \
    + [(-1.27, -6.35, 0.0, 1.27, 1.27, 2.5)]


def _smd(*spec):
    from shapely.geometry import box
    return [(n, False, box(x - w / 2, y - h / 2, x + w / 2, y + h / 2))
            for n, x, y, w, h in spec]


def _tht(*spec):
    from shapely.geometry import Point
    return [(n, True, Point(x, y).buffer(d / 2)) for n, x, y, d in spec]


CHIP_PADS = _smd(("1", -1.45, 0, 0.9, 1.7), ("2", 1.45, 0, 0.9, 1.7))
SOT_PADS = _smd(("1", -0.95, 1.1, 0.6, 0.7), ("2", 0.95, 1.1, 0.6, 0.7),
                ("3", 0, -1.1, 0.6, 0.7))
HDR_PADS = _tht(("1", 0, 0, 1.0), ("2", 0, 2.54, 1.0), ("3", 0, 5.08, 1.0))

Z, ONE = (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)


def _kinds(found):
    return sorted(h["kind"] for h in found)


def _mesh(tmp_path, boxes, name="m.wrl"):
    return modelgeom.load(_wrl(tmp_path / name, boxes))


def test_vrml_reads_in_tenths_of_an_inch(tmp_path):
    m = _mesh(tmp_path, CHIP)
    assert m.kind == "vrml"
    assert m.box == pytest.approx((-1.6, -0.8, 0.0, 1.6, 0.8, 0.6), abs=1e-5)
    assert len(m.faces) == 18


def test_placed_model_follows_kicads_transform(tmp_path):
    m = _mesh(tmp_path, [(0, 0, 0, 2, 1, 1)])
    # offset is in the Y-up model frame: +1 in y is -1 in the file frame
    assert modelgeom.placed_box(m, (1, 1, 0)) == pytest.approx(
        (1, -2, 0, 3, -1, 1), abs=1e-5)
    # KiCad turns by MINUS rotate.z in its Y-up frame
    assert modelgeom.placed_box(m, rotate=(0, 0, 90)) == pytest.approx(
        (0, 0, 0, 1, 2, 1), abs=1e-5)
    # a back-side part's file frame is the library's with y negated
    assert modelgeom.placed_box(m, back=True) == pytest.approx((0, 0, 0, 2, 1, 1), abs=1e-5)


@pytest.mark.parametrize("model,pads", [(CHIP, CHIP_PADS), (SOT, SOT_PADS),
                                        (HDR, HDR_PADS)])
def test_a_model_on_its_pads_passes(tmp_path, model, pads):
    assert cmf.fit_model(_mesh(tmp_path, model), Z, ONE, Z, False, pads) == []


@pytest.mark.parametrize("model,pads,off", [
    (CHIP, CHIP_PADS, (0.6, 0.0, 0.0)),
    (SOT, SOT_PADS, (0.0, 1.0, 0.0)),
    (HDR, HDR_PADS, (0.0, 1.27, 0.0)),     # half a pitch: every pin off
    (HDR, HDR_PADS, (2.2, -0.4, 0.0))])
def test_an_offset_model_is_found_and_its_fit_passes(tmp_path, model, pads, off):
    m = _mesh(tmp_path, model)
    found = cmf.fit_model(m, off, ONE, Z, False, pads)
    assert _kinds(found) == ["model_offset"]
    fit = found[0]["fit"]
    assert fit["offset"][:2] == pytest.approx([0.0, 0.0], abs=0.13)
    assert cmf.fit_model(m, tuple(fit["offset"]), ONE, tuple(fit["rotate"]),
                         False, pads) == []


@pytest.mark.parametrize("turn", [90, 180, 270])
def test_a_turned_model_is_found(tmp_path, turn):
    m = _mesh(tmp_path, SOT)
    found = cmf.fit_model(m, Z, ONE, (0, 0, turn), False, SOT_PADS)
    assert _kinds(found) == ["model_rotation"]
    assert found[0]["fit"]["rotate"][2] % 360 == 0


def test_a_back_side_part_is_read_mirrored(tmp_path):
    m = _mesh(tmp_path, SOT)
    back = [(n, t, __import__("shapely").affinity.scale(p, 1, -1, origin=(0, 0)))
            for n, t, p in SOT_PADS]
    assert cmf.fit_model(m, Z, ONE, Z, True, back) == []
    found = cmf.fit_model(m, (0.8, 0, 0), ONE, Z, True, back)
    assert _kinds(found) == ["model_offset"]
    assert cmf.fit_model(m, tuple(found[0]["fit"]["offset"]), ONE, Z, True,
                         back) == []


def test_scale_and_a_floating_model_are_found(tmp_path):
    m = _mesh(tmp_path, CHIP)
    assert "model_scale" in _kinds(cmf.fit_model(m, Z, (1, 1, 1.5), Z, False,
                                                 CHIP_PADS))
    found = cmf.fit_model(m, (0, 0, 0.5), ONE, Z, False, CHIP_PADS)
    assert _kinds(found) == ["model_floats"]
    assert found[0]["fit"]["offset"] == [0, 0, 0]


def test_a_wrong_model_is_unmatched(tmp_path):
    # a SOT-23's model on a big three-pad part: no slide or turn fits it
    found = cmf.fit_model(_mesh(tmp_path, SOT), Z, ONE, Z, False,
                          _smd(("1", -4, 0, 1.0, 1.2), ("2", 4, 0, 1.0, 1.2),
                               ("3", 0, 5, 1.0, 1.2)))
    assert _kinds(found) == ["model_unmatched"]


def test_a_triangulated_body_underside_is_no_lead(tmp_path):
    # EasyEDA cuts a body's underside into small triangles; merged, it is
    # the body, and sliding it over the pads must not look like a better fit
    pts = []
    for i in range(12):
        x = -3 + i * 0.5
        pts.append((x, -1.5, x + 0.5, 1.5))
    path = tmp_path / "tri.wrl"
    body = ", ".join(f"{a / IN:.6f} {b / IN:.6f} 0.05" for x0, y0, x1, y1 in pts
                     for a, b in ((x0, y0), (x1, y0), (x1, y1)))
    idx = ", ".join(f"{3 * k},{3 * k + 1},{3 * k + 2},-1" for k in range(12))
    _wrl(path, CHIP[:2])
    path.write_text(path.read_text() + "Shape { geometry IndexedFaceSet {\n"
                    f" coord Coordinate {{ point [ {body} ] }}\n"
                    f" coordIndex [ {idx} ]\n}} }}\n", encoding="utf-8")
    assert cmf.fit_model(modelgeom.load(path), Z, ONE, Z, False, CHIP_PADS) == []


STOCK = Path("/hive/tools/kicad-10.0.6/share/kicad/3dmodels/"
             "Resistor_SMD.3dshapes/R_0603_1608Metric.step")


@pytest.mark.skipif(not STOCK.is_file(), reason="no stock KiCad models here")
def test_a_stock_step_model_reads_to_size_and_fits():
    m = modelgeom.load(STOCK)
    assert m.kind == "step"
    assert m.box[:2] == pytest.approx((-0.8, -0.4), abs=0.01)
    assert m.box[3:5] == pytest.approx((0.8, 0.4), abs=0.01)
    pads = _smd(("1", -0.825, 0, 0.8, 0.95), ("2", 0.825, 0, 0.8, 0.95))
    assert cmf.fit_model(m, Z, ONE, Z, False, pads) == []
    assert _kinds(cmf.fit_model(m, Z, ONE, (0, 0, 90), False, pads)) \
        == ["model_rotation"]


def _board(tmp_path, model_path, offset="0 0 0", hide=""):
    pcb = tmp_path / "b.kicad_pcb"
    pcb.write_text(f"""(kicad_pcb (version 20241229) (generator "t")
 (general (thickness 1.6))
 (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
 (gr_rect (start 90 90) (end 110 110) (layer "Edge.Cuts") (stroke (width 0.1)))
 (footprint "t:HDR" (layer "F.Cu") (at 100 100 90)
  (property "Reference" "J1" (at 0 -2 0))
  (pad "1" thru_hole circle (at 0 0 90) (size 1.7 1.7) (drill 1.0) (layers "*.Cu"))
  (pad "2" thru_hole circle (at 0 2.54 90) (size 1.7 1.7) (drill 1.0) (layers "*.Cu"))
  (pad "3" thru_hole circle (at 0 5.08 90) (size 1.7 1.7) (drill 1.0) (layers "*.Cu"))
  (model "{model_path}" {hide}(offset (xyz {offset})) (scale (xyz 1 1 1))
   (rotate (xyz 0 0 0)))
 )
)
""", encoding="utf-8")
    return pcb


def test_check_reports_a_board_part(tmp_path):
    wrl = _wrl(tmp_path / "hdr.wrl", HDR)
    payload, _ = cmf.run(["--pcb", str(_board(tmp_path, wrl))])
    assert payload["status"] == "pass" and payload["checked"] == 1
    payload, _ = cmf.run(["--pcb", str(_board(tmp_path, wrl, "0 1.27 0"))])
    (v,) = payload["violations"]
    assert (v["kind"], v["refs"], v["pos"]) == ("model_offset", ["J1"], [100, 100])
    assert v["fit"]["offset"] == pytest.approx([0, 0, 0], abs=0.03)
    assert "(offset (xyz " in v["fix"]


def test_a_sunk_model_is_in_the_board(tmp_path):
    wrl = _wrl(tmp_path / "hdr.wrl", HDR)
    payload, _ = cmf.run(["--pcb", str(_board(tmp_path, wrl, "0 0 -1"))])
    assert [v["kind"] for v in payload["violations"]] == ["model_in_board"]


def test_hidden_and_missing_models_are_skipped(tmp_path):
    wrl = _wrl(tmp_path / "hdr.wrl", HDR)
    payload, _ = cmf.run(["--pcb", str(_board(tmp_path, wrl, "0 1.27 0",
                                              hide="(hide yes) "))])
    assert payload["status"] == "pass" and payload["hidden"] == 1
    payload, _ = cmf.run(["--pcb", str(_board(tmp_path, tmp_path / "gone.wrl"))])
    assert payload["status"] == "pass" and payload["missing"]
