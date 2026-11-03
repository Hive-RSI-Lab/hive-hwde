"""CPL placement per part: the LCSC model's pin 1 on the board's pad 1.

Offline: the models are cached EasyEDA records in tests/fixtures/easyeda
(C116592 TPS563201 SOT-23-6, C106675 a THT electrolytic, C106245 a 0603 cap),
plus one synthetic LED model whose numbering is the reverse of KiCad's. The
board is a tiny hand-written .kicad_pcb, so nothing needs KiCad or a network.
"""
from __future__ import annotations

import csv
import json
import math
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import bom_cpl  # noqa: E402
import checklib  # noqa: E402
import cpl_render  # noqa: E402
import cpl_verify as cv  # noqa: E402
import dfm_check  # noqa: E402
import easyeda  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "easyeda"

# The imported (easyeda2kicad) footprint keeps the model's own geometry.
IMPORTED_SOT236 = [("1", 1.35, 0.95), ("2", 1.35, 0.0), ("3", 1.35, -0.95),
                   ("4", -1.35, -0.95), ("5", -1.35, 0.0), ("6", -1.35, 0.95)]
# KiCad's library SOT-23-6: pins 1-3 down the left, pin 1 top-left.
KICAD_SOT236 = [("1", -1.1375, -0.95), ("2", -1.1375, 0.0),
                ("3", -1.1375, 0.95), ("4", 1.1375, 0.95), ("5", 1.1375, 0.0),
                ("6", 1.1375, -0.95)]


def _fp(ref, fpid, x, y, r, pads, size=(1.1, 0.6), layer="F.Cu"):
    if layer == "B.Cu":
        # KiCad's flip: a bottom footprint's local pads are stored mirrored in y
        pads = [(n, px, -py) for n, px, py in pads]
    pad_txt = "".join(
        f'(pad "{n}" smd rect (at {px} {py} {r}) (size {size[0]} {size[1]}) '
        f'(layers "{layer}"))' for n, px, py in pads)
    silk = '(fp_line (start -1 -1) (end 1 -1) (layer "F.SilkS"))'
    return (f'(footprint "{fpid}" (layer "{layer}") (at {x} {y} {r}) '
            f'(property "Reference" "{ref}") {silk} {pad_txt})')


BOARD = "(kicad_pcb (version 20240108) " + " ".join([
    _fp("U1", "aiee:SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BR", 10, 10, 90,
        IMPORTED_SOT236),
    _fp("U2", "Package_TO_SOT_SMD:SOT-23-6", 20, 10, 0, KICAD_SOT236),
    _fp("D1", "LED_SMD:LED_0603_1608Metric", 30, 10, 0,
        [("1", -0.7875, 0), ("2", 0.7875, 0)], size=(0.875, 0.95)),
    _fp("C1", "aiee:C0603", 40, 10, 0, [("1", -0.7, 0), ("2", 0.7, 0)],
        size=(0.8, 0.9)),
    _fp("C2", "aiee:CAP-TH_BD13.0-P5.00-D1.2-FD", 50, 10, 0,
        [("1", -2.54, 0), ("2", 2.54, 0)], size=(2.0, 2.0)),
    _fp("X1", "aiee:SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BR", 60, 10, 0,
        IMPORTED_SOT236),
    _fp("U3", "aiee:SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BR", 70, 10, 0,
        IMPORTED_SOT236, layer="B.Cu"),
    _fp("C3", "aiee:C0603", 80, 10, 90, [("1", -0.7, 0), ("2", 0.7, 0)],
        size=(0.8, 0.9), layer="B.Cu"),
    _fp("C4", "aiee:C0603", 90, 10, 45, [("1", -0.7, 0), ("2", 0.7, 0)],
        size=(0.8, 0.9), layer="B.Cu"),
    _fp("D2", "LED_SMD:LED_0603_1608Metric", 100, 10, 0,
        [("1", -0.7875, 0), ("2", 0.7875, 0)], size=(0.875, 0.95),
        layer="B.Cu"),
    # a 0201-style land: two unnumbered paste-only pads beside the copper ones
    _fp("C5", "aiee:C0603", 110, 10, 90,
        [("", -0.72, 0), ("", 0.72, 0), ("1", -0.7, 0), ("2", 0.7, 0)],
        size=(0.8, 0.9), layer="B.Cu"),
]) + ")"

# A synthetic LED whose pad 1 is the ANODE (KiCad's LED pad 1 is the cathode).
LED_MODEL = {"lcsc": "C900001", "title": "TEST-LED", "package": "LED0603",
             "origin": [4000, 3000],
             "pads": ["PAD~RECT~3997.244~3000~3.4~3.7~1~~1~0~~0~a",
                      "PAD~RECT~4002.756~3000~3.4~3.7~1~~2~0~~0~b"],
             "silk": [],
             "pins": ["P~show~0~1~-30~0~180~g1~0^^-30~0^^M -30 0 h 10~#880000"
                      "^^0~-17~3~0~A~start~~~#0000FF^^0~-21~-1~0~1~end",
                      "P~show~0~2~30~0~0~g2~0^^30~0^^M 30 0 h -10~#880000"
                      "^^0~17~3~0~K~end~~~#0000FF^^0~21~-1~0~2~start"]}

LCSC = {"U1": "C116592", "U2": "C116592", "D1": "C900001", "C1": "C106245",
        "C2": "C106675", "U3": "C116592", "C3": "C106245", "C4": "C106245", "C5": "C106245",
        "D2": "C900001"}


@pytest.fixture
def ws(tmp_path):
    cache = tmp_path / "easyeda"
    shutil.copytree(FIX, cache)
    (cache / "C900001.json").write_text(json.dumps(LED_MODEL))
    pcb = tmp_path / "t.kicad_pcb"
    pcb.write_text(BOARD)
    return pcb, cache


def _cpl(**rots):
    """CPL rows at each footprint's own position (board y down -> CPL y up)."""
    pos = {"U1": 10, "U2": 20, "D1": 30, "C1": 40, "C2": 50, "X1": 60,
           "U3": 70, "C3": 80, "C4": 90, "D2": 100, "C5": 110}
    return {r: {"x": pos[r], "y": 10.0, "rot": float(v), "layer": "Top"}
            for r, v in rots.items()}


def _verdicts(pcb, cache, cpl):
    rep = cv.verify(pcb, cpl, LCSC, cache, fetch=False)
    return {p["ref"]: p for p in rep["parts"]}, rep


def test_imported_footprint_needs_no_correction(ws):
    """The owner's case: an easyeda2kicad SOT-23-6 at 90 deg must ship at 90.
    The '^SOT-23,180' table row shipped 270 - pin 1 opposite the mark."""
    pcb, cache = ws
    v, _ = _verdicts(pcb, cache, _cpl(U1=90))
    assert v["U1"]["verdict"] == "ok" and v["U1"]["expected_rot"] == [90.0]
    v, rep = _verdicts(pcb, cache, _cpl(U1=270))
    assert v["U1"]["verdict"] == "wrong_rotation"
    assert rep["failed"] == ["U1"] and rep["status"] == "violations"


def test_kicad_library_footprint_needs_180_for_this_model(ws):
    pcb, cache = ws
    v, _ = _verdicts(pcb, cache, _cpl(U2=180))
    assert v["U2"]["verdict"] == "ok"
    v, _ = _verdicts(pcb, cache, _cpl(U2=0))
    assert v["U2"]["verdict"] == "wrong_rotation"
    assert v["U2"]["expected_rot"] == [180.0]


def test_polarity_uses_pin_roles_not_numbers(ws):
    """The LED model's pad 1 is its anode. By number it fits at 0 deg, but
    that puts the anode on KiCad's cathode pad: the role fit wins."""
    pcb, cache = ws
    v, _ = _verdicts(pcb, cache, _cpl(D1=0))
    assert v["D1"]["verdict"] == "wrong_polarity"
    assert v["D1"]["corrections"] == [180.0]
    assert v["D1"]["number_corrections"] == [0.0]
    assert "cathode" in v["D1"]["why"]
    v, _ = _verdicts(pcb, cache, _cpl(D1=180))
    assert v["D1"]["verdict"] == "ok"
    v, _ = _verdicts(pcb, cache, _cpl(D1=90))
    assert v["D1"]["verdict"] == "wrong_rotation"


def test_nonpolar_two_pad_part_either_way_round(ws):
    pcb, cache = ws
    for rot in (0, 180):
        v, _ = _verdicts(pcb, cache, _cpl(C1=rot))
        assert v["C1"]["verdict"] == "ok", rot
    v, _ = _verdicts(pcb, cache, _cpl(C1=90))
    assert v["C1"]["verdict"] == "wrong_rotation"


def test_polar_cap_is_strict_about_180(ws):
    """The electrolytic's model names its pins 1/2, so polarity falls back to
    pad numbers - and a 180-degree turn is NOT accepted the way it is for C1."""
    pcb, cache = ws
    v, _ = _verdicts(pcb, cache, _cpl(C2=0))
    assert v["C2"]["verdict"] == "ok" and v["C2"]["polar"]
    assert v["C2"]["basis"].startswith("pad number")
    v, _ = _verdicts(pcb, cache, _cpl(C2=180))
    assert v["C2"]["verdict"] == "wrong_rotation"


def test_no_data_fails_loudly(ws):
    pcb, cache = ws
    v, rep = _verdicts(pcb, cache, _cpl(X1=0, U3=180, U1=90))
    assert v["X1"]["verdict"] == "no_model"
    assert v["U3"]["verdict"] == "ok"    # read from below, 180 - 0 + 0
    assert v["U1"]["verdict"] == "ok"
    assert sorted(rep["failed"]) == ["X1"]


def test_bottom_two_pad_part_either_way_round(ws):
    """C3 at KiCad 90: 180 - 90 + 0 = 90, a non-polar part also at 270."""
    pcb, cache = ws
    for r in (90, 270):
        v, _ = _verdicts(pcb, cache, _cpl(C3=r))
        assert v["C3"]["verdict"] == "ok", r
    v, _ = _verdicts(pcb, cache, _cpl(C3=0))
    assert v["C3"]["verdict"] == "wrong_rotation"
    assert sorted(v["C3"]["expected_rot"]) == [90.0, 270.0]


def test_bottom_two_pad_part_with_paste_pads(ws):
    """Unnumbered paste-only pads aren't pads a lead lands on: still two pads."""
    pcb, cache = ws
    v, _ = _verdicts(pcb, cache, _cpl(C5=90))
    assert v["C5"]["verdict"] == "ok", v["C5"]


def test_bottom_multi_pad_part_gets_real_verdicts(ws):
    """twin-side's R1: a bottom IC at KiCad 0 goes in at 180. At 0 it's wrong."""
    pcb, cache = ws
    v, rep = _verdicts(pcb, cache, _cpl(U3=180))
    assert v["U3"]["verdict"] == "ok" and v["U3"]["expected_rot"] == [180.0]
    assert rep["bottom_preview"]["refs"] == ["U3"]
    assert "preview" in rep["bottom_preview"]["note"]
    assert "not from a JLC statement" in rep["bottom_preview"]["note"]
    v, rep = _verdicts(pcb, cache, _cpl(U3=0))
    assert v["U3"]["verdict"] == "wrong_rotation"
    assert "needs 180" in v["U3"]["why"]
    assert rep["bottom_preview"] is None


def test_bottom_polar_part_wrong_polarity(ws):
    """The LED model's pad 1 is its anode, KiCad's the cathode: the role fit
    decides, on the bottom as on the top (test_polarity_uses_pin_roles...)."""
    pcb, cache = ws
    v, _ = _verdicts(pcb, cache, _cpl(D2=180))
    assert v["D2"]["verdict"] == "wrong_polarity"
    assert v["D2"]["expected_rot"] == [0.0]
    v, _ = _verdicts(pcb, cache, _cpl(D2=0))
    assert v["D2"]["verdict"] == "ok"
    pads = [("1", -0.7875, 0.0), ("2", 0.7875, 0.0)]
    # no roles in this model, so polarity goes by number: 180 only, never 0
    named = _model(pads, package="LED0603")
    fp = _kfp("D3", [(n, x, -y) for n, x, y in pads], name="aiee:LED0603")
    fp.side = "bottom"
    a = cv.analyse(fp, named)
    assert a["verdict"] == "ok" and a["corrections"] == [0.0]
    assert cv.derive_rotation(fp, named) == 180.0


def test_top_regression_formula_has_no_bottom_term(ws):
    pcb, cache = ws
    fps = cv.board_footprints(pcb)
    model = easyeda.get("C116592", cache, fetch=False)
    assert cv.derive_rotation(fps["U1"], model) == 90.0   # KiCad 90 + 0
    assert cv.derive_rotation(fps["U2"], model) == 180.0  # KiCad 0 + 180
    v, rep = _verdicts(pcb, cache, _cpl(U1=90, U2=180))
    assert v["U1"]["verdict"] == v["U2"]["verdict"] == "ok"
    assert rep["bottom_preview"] is None
    assert cv.cpl_rotation("top", 90.0, 180.0) == 270.0
    assert cv.cpl_rotation("bottom", 90.0, 180.0) == 270.0
    assert cv.cpl_rotation("bottom", 0.0, 90.0) == 270.0
    assert cv.cpl_rotation("bottom", 30.0, 0.0) == 150.0


ASYM = [("1", -1.0, -0.65), ("2", -1.0, 0.65), ("3", 1.0, 0.65),
        ("4", 1.0, 0.0), ("5", 1.0, -0.65)]


def _flipped_fp(r, c=0.0):
    """ASYM's land pattern (the model turned by c), flipped to the bottom as
    KiCad does it, at KiCad rotation r."""
    land = [(n, *cv.rot(x, y, c)) for n, x, y in ASYM]
    fp = _kfp("U9", [(n, x, -y) for n, x, y in land], rot=float(r))
    fp.side = "bottom"
    return fp


@pytest.mark.parametrize("r", [0, 45, 90, 135, 180, 270, 315])
@pytest.mark.parametrize("c", [0, 90, 180, 270])
def test_bottom_rotation_lands_every_pin_seen_from_below(r, c):
    """The oracle doesn't use the fit: board pads from the footprint's own
    transform, mirrored (x -> -x), against the model turned by what the check
    says. Every pin lands on its own pad, and no other turn lands them."""
    model = _model(ASYM)
    fp = _flipped_fp(r, c)
    assert cv.analyse(fp, model)["corrections"] == [float(c)]
    got = cv.derive_rotation(fp, model)
    assert got == (180 - r + c) % 360
    board = {p.number: cv.rot(p.x, p.y, fp.rot) for p in fp.pads}
    below = {n: (-x, y) for n, (x, y) in board.items()}
    for th in range(0, 360, 45):
        err = max(math.hypot(cv.rot(p.x, p.y, th)[0] - below[p.number][0],
                             cv.rot(p.x, p.y, th)[1] - below[p.number][1])
                  for p in model.pads)
        assert (err < 1e-6) == (th == got), (th, got, err)


def test_bottom_footprint_not_flipped_has_no_fit():
    """A land pattern stored unmirrored on the bottom is the part's mirror
    image. No turn seats it, so it's never passed."""
    fp = _kfp("U9", ASYM)
    fp.side = "bottom"
    assert cv.analyse(fp, _model(ASYM))["verdict"] == "no_fit"


def test_bottom_placement_offset_mirrors_the_model_centre():
    """A footprint origin off the part's centre on the bottom. The model's
    centre comes back from the view below with x negated, and the offset that
    results puts its pads on the board's."""
    pads = [("1", -1.5, -1.0), ("2", 1.5, -1.0), ("3", -1.5, 1.0), ("4", 1.0, 1.0)]
    model = _model(pads)
    # the land pattern sits 1.87 mm down the footprint's y, and 1.2 across,
    # flipped, then turned 90
    fp = _kfp("J2", [(n, x + 1.2, -(y + 1.87)) for n, x, y in pads], rot=90.0)
    fp.side = "bottom"
    want = cv.derive_rotation(fp, model)
    assert want == 90.0
    dx, dy = cv.placement_offset(fp, model, want)
    mc = [sum(p.x for p in model.pads) / 4, sum(p.y for p in model.pads) / 4]
    ex, ey = cv.rot(*mc, want)
    kc = [sum(p.x for p in fp.pads) / 4, sum(p.y for p in fp.pads) / 4]
    kx, ky = cv.rot(*kc, fp.rot)
    assert abs((dx - ex) - kx) < 1e-6 and abs((dy + 0) - (ky - ey)) < 1e-6
    # at the moved position the check reads no offset
    row = cv.check_part(fp, model, {"x": fp.x + dx, "y": fp.y + dy, "rot": want})
    assert row["verdict"] == "ok" and row["offset_mm"] == 0.0
    # at the footprint origin it reads the whole offset
    row = cv.check_part(fp, model, {"x": fp.x, "y": fp.y, "rot": want})
    assert row["offset_mm"] == pytest.approx(math.hypot(dx, dy), abs=1e-3)


def test_bom_cpl_bottom_rows_pass_cpl_verify_and_use_the_table_term(ws, tmp_path):
    """bom_cpl writes, cpl_verify agrees; a part with no model takes the
    package table's correction on the 180 - r base."""
    pcb, cache = ws
    parts = [{"ref": "U3", "package": "x", "x": 70.0, "y": -10.0, "rot": 0.0,
              "side": "bottom"},
             {"ref": "C3", "package": "x", "x": 80.0, "y": -10.0, "rot": 90.0,
              "side": "bottom"},
             {"ref": "Q1", "package": "SOT-223", "x": 1.0, "y": 1.0, "rot": 90.0,
              "side": "bottom"},
             {"ref": "Q2", "package": "SOT-223", "x": 1.0, "y": 1.0, "rot": 90.0,
              "side": "top"}]
    derive = bom_cpl.model_deriver(pcb, {r: {"lcsc": c} for r, c in LCSC.items()},
                                   cache, False)
    rules = [(__import__("re").compile("SOT-223"), 180.0)]
    rows, audit = bom_cpl.build_cpl(parts, rules, derive)
    by = {r["Designator"]: r for r in rows}
    assert float(by["U3"]["Rotation"]) == 180.0
    assert float(by["C3"]["Rotation"]) == 90.0
    assert float(by["Q1"]["Rotation"]) == (180 - 90 + 180) % 360
    assert float(by["Q2"]["Rotation"]) == (90 + 180) % 360
    aud = {a["ref"]: a for a in audit}
    assert aud["U3"]["correction"] == 0.0 and aud["U3"]["source"] == "lcsc_model"
    assert aud["Q1"]["correction"] == 180.0 and aud["Q1"]["source"] == "table"
    v, _ = _verdicts(pcb, cache, {r: {"x": float(by[r]["Mid X"]),
                                      "y": -float(by[r]["Mid Y"]),
                                      "rot": float(by[r]["Rotation"]),
                                      "layer": "Bottom"} for r in ("U3", "C3")})
    assert v["U3"]["verdict"] == v["C3"]["verdict"] == "ok"
    assert v["U3"]["offset_mm"] < 0.5


def test_dfm_bottom_verdicts_and_preview_warning(ws, tmp_path):
    pcb, cache = ws
    cpl = tmp_path / "CPL.csv"
    _cpl_csv(cpl, [("U3", 70, 180), ("U1", 10, 90)])
    pj = tmp_path / "parts.json"
    pj.write_text(json.dumps({"U3": {"lcsc": "C116592"}, "U1": {"lcsc": "C116592"}}))
    vios: list = []
    dfm_check.check_placement(pcb, cpl, pj, None, cache, False, vios)
    prev = [v for v in vios if v["kind"] == "cpl_bottom_preview"]
    assert len(prev) == 1 and prev[0]["severity"] == "warning"
    assert prev[0]["refs"] == ["U3"] and "order preview" in prev[0]["msg"]
    assert not [v for v in vios if v["severity"] == "error"]

    _cpl_csv(cpl, [("U3", 70, 0)])
    vios = []
    dfm_check.check_placement(pcb, cpl, pj, None, cache, False, vios)
    assert [(v["kind"], v["severity"]) for v in vios] == [("cpl_rotation", "error")]


def test_render_draws_a_bottom_part_mirrored(ws, tmp_path):
    pcb, cache = ws
    fp = cv.board_footprints(pcb)["U3"]
    model = easyeda.get("C116592", cache, fetch=False)
    pad1 = next(p for p in cpl_render.board_shapes(fp)["pads"]
                if p["number"] == "1")["center"]
    right = cpl_render.model_shapes(model, _cpl(U3=180)["U3"], "bottom")
    wrong = cpl_render.model_shapes(model, _cpl(U3=0)["U3"], "bottom")
    assert math.hypot(right["pin1"][0] - pad1[0], right["pin1"][1] - pad1[1]) < 0.05
    assert math.hypot(wrong["pin1"][0] - pad1[0], wrong["pin1"][1] - pad1[1]) > 1.0
    # every model pad on its own number, not just pin 1
    board = {p["number"]: p["center"] for p in cpl_render.board_shapes(fp)["pads"]}
    for p in right["pads"]:
        assert math.hypot(p["center"][0] - board[p["number"]][0],
                          p["center"][1] - board[p["number"]][1]) < 0.3

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    seen = {}
    real_close = plt.close

    def close(fig=None):
        for ax in fig.axes:
            if ax.get_title():
                seen[ax.get_title().split()[0]] = (ax.get_title(), ax.get_xlim())
        real_close(fig)
    plt.close = close
    try:
        idx = cpl_render.render(pcb, _cpl(U3=180, U1=90), LCSC, cache,
                                tmp_path / "r", fetch=False)
    finally:
        plt.close = real_close
    assert idx["parts"]["U3"]["side"] == "bottom"
    assert idx["parts"]["U1"]["side"] == "top"
    assert "BOTTOM, from below" in seen["U3"][0]
    assert "BOTTOM" not in seen["U1"][0]
    assert seen["U3"][1][0] > seen["U3"][1][1]   # x runs right to left
    assert seen["U1"][1][0] < seen["U1"][1][1]


def test_cache_is_read_without_network(ws, monkeypatch):
    pcb, cache = ws

    def boom(_):
        raise AssertionError("network touched")
    monkeypatch.setattr(easyeda, "_fetch_raw", boom)
    assert easyeda.get("C116592", cache, fetch=True).package.startswith(
        "SOT-23-6")
    assert easyeda.get("C999999", cache, fetch=False) is None


def test_fetch_caches_trimmed_record(tmp_path):
    raw = {"title": "X", "dataStr": {"shape": LED_MODEL["pins"]},
           "packageDetail": {"title": "LED0603", "dataStr": {
               "head": {"x": 4000, "y": 3000},
               "shape": LED_MODEL["pads"] + ["SVGNODE~huge"]}}}
    m = easyeda.get("c900001", tmp_path, fetcher=lambda _: raw)
    assert m.pin_names == {"1": "A", "2": "K"}
    cached = json.loads((tmp_path / "C900001.json").read_text())
    assert "SVGNODE~huge" not in json.dumps(cached)
    # A part EasyEDA does not know is not cached, so a later run asks again.
    assert easyeda.get("C1", tmp_path, fetcher=lambda _: {}) is None
    assert not (tmp_path / "C1.json").exists()


def test_bom_cpl_uses_the_model_and_falls_back_to_the_table(ws):
    pcb, cache = ws
    fps = cv.board_footprints(pcb)
    rules = bom_cpl.load_rotations(bom_cpl.REF_ROTATIONS)
    parts = [{"ref": r, "x": fps[r].x, "y": -fps[r].y, "rot": fps[r].rot,
              "side": "top", "package": fps[r].name}
             for r in ("U1", "U2", "X1")]
    derive = bom_cpl.model_deriver(
        pcb, {r: {"lcsc": c} for r, c in LCSC.items()}, cache, fetch=False)
    cpl, audit = bom_cpl.build_cpl(parts, rules, derive)
    got = {a["ref"]: a for a in audit}
    assert got["U1"]["source"] == "lcsc_model" and got["U1"]["final_rot"] == 90
    assert got["U2"]["source"] == "lcsc_model" and got["U2"]["final_rot"] == 180
    # No model: the table decides (an imported footprint name -> 0).
    assert got["X1"]["source"] == "table" and got["X1"]["final_rot"] == 0


def test_table_no_longer_turns_imported_sot23_5_and_6():
    rules = bom_cpl.load_rotations(bom_cpl.REF_ROTATIONS)
    for name in ("SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BL",
                 "SOT-23-5_L2.9-W1.6-P0.95-LS2.8-BL",
                 "LQFP-48_L7.0-W7.0-P0.50-LS9.0-BL"):
        assert bom_cpl.correct_rotation(name, 90.0, rules)[0] == 90.0, name
    assert bom_cpl.correct_rotation("SOT-23", 0.0, rules)[0] == 180.0


def test_merge_visual_verdicts():
    parts = [{"ref": "U1", "verdict": "ok"}, {"ref": "U2", "verdict": "ok"},
             {"ref": "D1", "verdict": "wrong_polarity"},
             {"ref": "C1", "verdict": "ok"}, {"ref": "C2", "verdict": "ok"}]
    vis = {"model": "claude-sonnet-5-5", "parts": {
        "U1": {"pin1": "match", "polarity": "n/a"},
        "U2": {"pin1": "mismatch", "polarity": "n/a"},
        "D1": {"pin1": "match", "polarity": "mismatch"},
        "C1": {"pin1": "unclear", "polarity": "n/a"}}}
    got = {r["ref"]: r["agreement"] for r in cv.merge_visual(parts, vis)}
    assert got == {"U1": "agree", "U2": "disagree", "D1": "agree",
                   "C1": "unclear", "C2": "missing"}
    assert {r["agreement"] for r in cv.merge_visual(parts, None)} == {
        "not_run"}


def test_dfm_placement_errors_and_visual_disagreement(ws, tmp_path):
    pcb, cache = ws
    cpl = tmp_path / "CPL.csv"
    with cpl.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        w.writerow(["U1", 10, -10, "Top", 270])   # the owner's 180-off part
        w.writerow(["U2", 20, -10, "Top", 180])   # right
    vis = tmp_path / "cpl_visual.json"
    vis.write_text(json.dumps({"parts": {
        "U1": {"pin1": "mismatch", "polarity": "n/a"},
        "U2": {"pin1": "mismatch", "polarity": "n/a"}}}))
    pj = tmp_path / "parts.json"
    pj.write_text(json.dumps({"U1": {"lcsc": "C116592"},
                              "U2": {"lcsc": "C116592"}}))
    vios: list = []
    facts = dfm_check.check_placement(pcb, cpl, pj, vis, cache, False, vios)
    kinds = sorted((v["kind"], v["refs"][0]) for v in vios)
    assert kinds == [("cpl_rotation", "U1"), ("cpl_visual_disagree", "U2")]
    assert all(v["severity"] == "error" for v in vios)
    assert facts["failed"] == ["U1"]
    # Both layers say U1 is wrong: that is agreement, not a second error.
    assert {p["ref"]: p["agreement"] for p in facts["parts"]} == {
        "U1": "agree", "U2": "disagree"}


def test_render_puts_pin1_dot_on_pad1_only_when_right(ws, tmp_path):
    pcb, cache = ws
    fp = cv.board_footprints(pcb)["U1"]
    model = easyeda.get("C116592", cache, fetch=False)
    pad1 = next(p["center"] for p in cpl_render.board_shapes(fp)["pads"]
                if p["number"] == "1")
    right = cpl_render.model_shapes(model, _cpl(U1=90)["U1"])["pin1"]
    wrong = cpl_render.model_shapes(model, _cpl(U1=270)["U1"])["pin1"]
    assert abs(right[0] - pad1[0]) < 0.05 and abs(right[1] - pad1[1]) < 0.05
    assert abs(wrong[0] - pad1[0]) > 1.0 or abs(wrong[1] - pad1[1]) > 1.0

    idx = cpl_render.render(pcb, _cpl(U1=90, U2=180, D1=0, X1=0), LCSC,
                            cache, tmp_path / "r", fetch=False, per_image=3)
    assert [i["refs"] for i in idx["images"]] == [["D1", "U1", "U2"], ["X1"]]
    for i in idx["images"]:
        png = tmp_path / "r" / i["file"]
        assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert idx["parts"]["X1"]["has_model"] is False
    assert idx["parts"]["D1"]["polar"] is True


# ---------------------------------------------- cache resolution, fetch errors

def test_cache_order_env_then_board_dir_then_shared(tmp_path, monkeypatch):
    home = tmp_path / "home"
    board = tmp_path / "board"
    board.mkdir()
    parts = board / "parts.json"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("HWDE_EASYEDA_CACHE", raising=False)
    monkeypatch.delenv("AIEE_EASYEDA_CACHE", raising=False)
    shared = home / ".cache" / "hwde" / "easyeda"
    # A board's own dir is its cache, before its first fetch makes it too:
    # models are kept in the board, not only in one agent's HOME.
    assert cv.default_cache(parts) == board / "easyeda"
    assert cv.default_cache(None) == shared
    # The env var wins over both.
    monkeypatch.setenv("HWDE_EASYEDA_CACHE", str(tmp_path / "env"))
    assert cv.default_cache(parts) == tmp_path / "env"
    assert cv.default_cache(None) == tmp_path / "env"


def test_failed_fetch_is_fetch_failed_not_no_model(ws, tmp_path, monkeypatch):
    """A fetch that raises must not read as a missing model (and so not as a
    pin-1 result); an API with no data for the part stays no_model."""
    pcb, _ = ws
    monkeypatch.setattr(easyeda.time, "sleep", lambda s: None)

    def boom(lcsc):
        raise OSError("HTTP Error 403: rate limited")
    monkeypatch.setattr(easyeda, "_fetch_raw", boom)
    rep = cv.verify(pcb, _cpl(U1=90), LCSC, tmp_path / "empty", fetch=True)
    row = rep["parts"][0]
    assert row["verdict"] == "fetch_failed"
    assert "403" in row["why"] and "pin-1" in row["why"]
    assert rep["failed"] == ["U1"] and rep["status"] == "violations"
    assert "fetch_failed" in cv.FAIL_VERDICTS

    # The 403 latched the rate limit for this process; a fresh run starts
    # without it.
    easyeda.reset_rate_limit()
    monkeypatch.setattr(easyeda, "_fetch_raw", lambda lcsc: {})
    rep = cv.verify(pcb, _cpl(U1=90), LCSC, tmp_path / "empty2", fetch=True)
    assert rep["parts"][0]["verdict"] == "no_model"


# ------------------------------------- rate limit: 403/429 stop the fetching

def _http(code):
    import urllib.error

    def fetch(lcsc):
        raise urllib.error.HTTPError(easyeda.API.format(lcsc=lcsc), code,
                                     "Forbidden", None, None)
    return fetch


class _Calls:
    """A fetcher that records each LCSC number it is asked for."""

    def __init__(self, fetch):
        self.fetch, self.asked = fetch, []

    def __call__(self, lcsc):
        self.asked.append(lcsc)
        return self.fetch(lcsc)


@pytest.mark.parametrize("code", [403, 429])
def test_rate_limit_latches_and_later_parts_are_not_fetched(tmp_path, code,
                                                             capsys):
    calls = _Calls(_http(code))
    errs: dict = {}
    assert easyeda.get("C1", tmp_path, fetcher=calls, errors=errs) is None
    assert errs["C1"] == f"HTTP {code} from EasyEDA (rate limited)"
    assert easyeda.rate_limited() == errs["C1"]
    # Every later uncached part is skipped, not asked again.
    for c in ("C2", "C3"):
        assert easyeda.get(c, tmp_path, fetcher=calls, errors=errs) is None
        assert errs[c].startswith("not fetched: HTTP") and "rate" in errs[c]
    assert calls.asked == ["C1"]
    # Said once on stderr, not per part.
    assert capsys.readouterr().err.count("rate limited") == 1
    # A cached part is still read.
    shutil.copy(FIX / "C106245.json", tmp_path / "C106245.json")
    assert easyeda.get("C106245", tmp_path, fetcher=calls) is not None


def test_404_or_empty_is_no_model_and_network_error_is_not(tmp_path):
    import urllib.error
    errs: dict = {}
    assert easyeda.get("C1", tmp_path, fetcher=_http(404), errors=errs) is None
    assert easyeda.get("C2", tmp_path, fetcher=lambda _: {},
                       errors=errs) is None
    assert errs == {} and easyeda.rate_limited() is None

    def down(lcsc):
        raise urllib.error.URLError("Temporary failure in name resolution")
    calls = _Calls(down)
    for c in ("C3", "C4"):
        assert easyeda.get(c, tmp_path, fetcher=calls, errors=errs) is None
    assert errs["C3"].startswith("network error: URLError")
    # A network error is not a rate limit: the next part is still asked.
    assert calls.asked == ["C3", "C4"] and easyeda.rate_limited() is None
    assert easyeda.get("C5", tmp_path, fetcher=_http(500), errors=errs) is None
    assert errs["C5"] == "HTTP 500 from EasyEDA"


def test_cpl_verify_403_fails_first_part_and_skips_the_rest(ws, tmp_path,
                                                            monkeypatch):
    pcb, _ = ws
    monkeypatch.setattr(easyeda.time, "sleep", lambda s: None)
    calls = _Calls(_http(403))
    monkeypatch.setattr(easyeda, "_fetch_raw", calls)
    lcsc = {"U1": "C116592", "C1": "C106245", "C2": "C106675"}
    rep = cv.verify(pcb, _cpl(U1=90, C1=0, C2=0), lcsc, tmp_path / "empty",
                    fetch=True)
    rows = {r["ref"]: r for r in rep["parts"]}
    assert {r["verdict"] for r in rows.values()} == {"fetch_failed"}
    assert calls.asked == ["C106245"]   # first in natural order, then none
    assert "HTTP 403" in rows["C1"]["fetch_error"]
    assert rows["U1"]["fetch_error"].startswith("not fetched")
    assert rep["rate_limited"] == "HTTP 403 from EasyEDA (rate limited)"
    assert rep["fetch_failed"] == ["C1", "C2", "U1"]
    assert rep["status"] == "violations"


def test_dfm_reports_a_refused_fetch_once_never_as_no_model(ws, tmp_path,
                                                            monkeypatch):
    pcb, _ = ws
    monkeypatch.setattr(easyeda.time, "sleep", lambda s: None)
    monkeypatch.setattr(easyeda, "_fetch_raw", _http(403))
    cpl = tmp_path / "CPL.csv"
    with cpl.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        w.writerow(["U1", 10, -10, "Top", 90])
        w.writerow(["C1", 40, -10, "Top", 0])
    pj = tmp_path / "parts.json"
    pj.write_text(json.dumps({"U1": {"lcsc": "C116592"},
                              "C1": {"lcsc": "C106245"}}))
    vios: list = []
    facts = dfm_check.check_placement(pcb, cpl, pj, None, tmp_path / "empty",
                                      True, vios)
    assert [v["kind"] for v in vios] == ["cpl_fetch_failed"]
    v = vios[0]
    assert v["severity"] == "error" and sorted(v["refs"]) == ["C1", "U1"]
    assert "HTTP 403" in v["msg"] and "rate limited" in v["msg"]
    assert "rerun" in v["msg"] and v["rate_limited"] is True
    assert facts["fetch_failed"] == ["C1", "U1"]
    assert "cpl_fetch_failed" in dfm_check.DFM_FAMILIES["placement"]


def test_bom_cpl_says_fetch_failed_not_none_and_fails_the_run(ws, tmp_path,
                                                              monkeypatch):
    pcb, _ = ws
    monkeypatch.setattr(easyeda.time, "sleep", lambda s: None)
    calls = _Calls(_http(403))
    monkeypatch.setattr(easyeda, "_fetch_raw", calls)
    fps = cv.board_footprints(pcb)
    pos = tmp_path / "pos.csv"
    lines = ["Ref,Val,Package,PosX,PosY,Rot,Side"]
    for r in ("U1", "X1", "C1"):
        f = fps[r]
        lines.append(f"{r},v,{f.name},{f.x},{-f.y},{f.rot},top")
    pos.write_text("\n".join(lines) + "\n")
    pj = tmp_path / "parts.json"
    pj.write_text(json.dumps({"U1": {"lcsc": "C116592"},
                              "X1": {"lcsc": "C116593"},
                              "C1": {"lcsc": "C106245"}}))
    rep = bom_cpl.run(pcb, tmp_path / "fab", pos=pos, parts_json=pj,
                      easyeda_cache=tmp_path / "empty", fetch_models=True)
    got = {a["ref"]: a for a in rep["rotation_audit"]}
    assert {a["source"] for a in got.values()} == {"fetch_failed"}
    assert "HTTP 403" in got["C1"]["fetch_error"]
    assert len(calls.asked) == 1
    assert rep["model_fetch_failed"] == ["C1", "U1", "X1"]
    assert rep["easyeda_rate_limited"].startswith("HTTP 403")
    assert rep["status"] == "violations"
    vio = [v for v in rep["violations"]
           if v["kind"] == "cpl_model_fetch_failed"]
    assert len(vio) == 1 and "rate limited" in vio[0]["message"]


def test_render_records_fetch_error_apart_from_no_model(ws, tmp_path,
                                                        monkeypatch):
    pcb, _ = ws
    monkeypatch.setattr(easyeda.time, "sleep", lambda s: None)
    easyeda.reset_rate_limit()

    def forbidden(lcsc):
        raise OSError("HTTP Error 403: rate limited")
    monkeypatch.setattr(easyeda, "_fetch_raw", forbidden)
    idx = cpl_render.render(pcb, _cpl(U1=90), LCSC, tmp_path / "empty",
                            tmp_path / "r", fetch=True)
    part = idx["parts"]["U1"]
    assert part["has_model"] is False and "403" in part["fetch_error"]
    assert idx["fetch_failed"] == ["U1"] and idx["rate_limited"]
    assert "rerun" in idx["note"]
    easyeda.reset_rate_limit()

    monkeypatch.setattr(easyeda, "_fetch_raw", lambda lcsc: {})
    idx = cpl_render.render(pcb, _cpl(U1=90), LCSC, tmp_path / "empty2",
                            tmp_path / "r2", fetch=True)
    assert idx["parts"]["U1"]["has_model"] is False
    assert "fetch_error" not in idx["parts"]["U1"]
    assert idx["fetch_failed"] == [] and "note" not in idx
    easyeda.reset_rate_limit()


# ------------------------- models kept in the board; a refused fetch is unknown

def _raw(model):
    return {"title": model["title"], "dataStr": {"shape": model["pins"]},
            "packageDetail": {"title": model["package"], "dataStr": {
                "head": {"x": model["origin"][0], "y": model["origin"][1]},
                "shape": model["pads"]}}}


def test_board_cache_hit_never_asks_easyeda(ws, tmp_path):
    """A board that carries its models (a fresh checkout or a scratch copy of
    it) verifies with EasyEDA refusing every request."""
    pcb, cache = ws
    calls = _Calls(_http(403))
    errs: dict = {}
    assert easyeda.get("C116592", cache, fetcher=calls, errors=errs)
    assert calls.asked == [] and errs == {} and easyeda.rate_limited() is None


def test_fetch_keeps_the_model_in_the_board_and_the_shared_cache(tmp_path,
                                                                 monkeypatch):
    shared = tmp_path / "shared"
    monkeypatch.setenv("HWDE_EASYEDA_CACHE", str(shared))
    monkeypatch.setattr(easyeda, "shared_cache", lambda: shared)
    board = tmp_path / "board" / "parts" / "easyeda"   # not there yet
    m = easyeda.get("C900001", board, fetcher=lambda _: _raw(LED_MODEL))
    assert m is not None and m.pin_names == {"1": "A", "2": "K"}
    assert (board / "C900001.json").is_file()
    assert (shared / "C900001.json").is_file()
    # The next run of the board reads it there, with EasyEDA refusing.
    calls = _Calls(_http(403))
    assert easyeda.get("C900001", board, fetcher=calls) is not None
    assert calls.asked == []


def test_shared_cache_fills_the_board_cache(tmp_path, monkeypatch):
    """A model this machine fetched for another board is read, and copied
    into this board's cache so the board carries it from then on."""
    shared = tmp_path / "shared"
    shared.mkdir()
    shutil.copy(FIX / "C116592.json", shared / "C116592.json")
    monkeypatch.setattr(easyeda, "shared_cache", lambda: shared)
    board = tmp_path / "board" / "easyeda"
    calls = _Calls(_http(403))
    assert easyeda.get("C116592", board, fetch=False) is not None
    assert (board / "C116592.json").is_file()
    assert easyeda.get("C116592", board, fetcher=calls) is not None
    assert calls.asked == []


def _cpl_csv(path, rows):
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for ref, x, rot in rows:
            w.writerow([ref, x, -10, "Top", rot])
    return path


def test_dfm_403_with_no_cache_is_unverified_not_failed(ws, tmp_path,
                                                        monkeypatch):
    pcb, _ = ws
    monkeypatch.setattr(easyeda.time, "sleep", lambda s: None)
    monkeypatch.setattr(easyeda, "_fetch_raw", _http(403))
    cpl = _cpl_csv(tmp_path / "CPL.csv", [("U1", 10, 90), ("C1", 40, 0)])
    pj = tmp_path / "parts.json"
    pj.write_text(json.dumps({"U1": {"lcsc": "C116592"},
                              "C1": {"lcsc": "C106245"}}))
    vios: list = []
    dfm_check.check_placement(pcb, cpl, pj, None, tmp_path / "empty", True,
                              vios)
    g = dfm_check.grade(vios)
    assert g["status"] == "error"          # exit 2, never 1
    assert sorted(g["unverified"]["refs"]) == ["C1", "U1"]
    assert "HTTP 403" in g["unverified"]["reason"]
    assert g["error"].startswith("placement unverified, not failed")
    assert checklib.emit({**g}, str(tmp_path / "r.json")) == 2
    # A real finding beside it still fails the board.
    vios.append({"severity": "error", "kind": "dfm_trace_width", "refs": []})
    assert dfm_check.grade(vios) == {"status": "violations"}
    # Warnings alone pass.
    assert dfm_check.grade([{"severity": "warning", "kind": "cpl_offset"}]) \
        == {"status": "pass"}


def test_dfm_rotation_mismatch_from_the_cache_still_fails(ws, tmp_path,
                                                          monkeypatch):
    pcb, cache = ws
    monkeypatch.setattr(easyeda, "_fetch_raw", _http(403))
    cpl = _cpl_csv(tmp_path / "CPL.csv", [("U1", 10, 270)])
    pj = tmp_path / "parts.json"
    pj.write_text(json.dumps({"U1": {"lcsc": "C116592"}}))
    vios: list = []
    dfm_check.check_placement(pcb, cpl, pj, None, cache, True, vios)
    assert [v["kind"] for v in vios] == ["cpl_rotation"]
    assert dfm_check.grade(vios) == {"status": "violations"}
    assert easyeda.rate_limited() is None   # never asked


# ------- imported (Eagle) libraries: hw-lab eda-crossover -------

def _model(pads, names=None, package="X"):
    from types import SimpleNamespace as NS
    return NS(lcsc="C1", package=package, pin_names=names or {},
              pads=[NS(number=n, x=x, y=y) for n, x, y in pads])


def _kfp(ref, pads, rot=0.0, name="SparkFun:PART"):
    return cv.Footprint(ref, name, 10.0, 10.0, rot, "top",
                        [cv.KPad(n, x, y, 0.6, 0.6) for n, x, y in pads])


def test_eagle_pads_a_c_are_anode_cathode():
    """A board pad named C beside one named A is a cathode: the LED that the
    package table would have placed backwards (K on A) gets 180."""
    fp = _kfp("LED1", [("A", -0.88, 0), ("C", 0.88, 0)], name="SparkFun:LED-0603")
    model = _model([("1", 0.8, 0), ("2", -0.8, 0)], {"1": "A", "2": "K"}, "LED0603")
    a = cv.analyse(fp, model)
    assert a["verdict"] == "ok" and "role" in a["basis"]
    assert a["corrections"] == [180.0]
    fp = _kfp("LED1", [("A", 0.88, 0), ("C", -0.88, 0)], name="SparkFun:LED-0603")
    assert cv.analyse(fp, model)["corrections"] == [0.0]
    assert cv.board_roles(fp, model) == {"A": "A", "C": "K"}


def test_eagle_p1_pads_match_model_numbers():
    pads = [("1", -0.85, 0.7), ("2", 0.85, 0.7), ("3", 0.85, -0.7), ("4", -0.85, -0.7)]
    fp = _kfp("Y1", [("P" + n, x, y) for n, x, y in pads])
    assert cv.analyse(fp, _model(pads))["verdict"] == "ok"


def test_switch_pads_fit_by_geometry_not_numbers():
    """LCSC's KMR2 model numbers its pins across the short side; SparkFun's
    footprint along the long one. A switch only has to land on its pads;
    anything else with that mismatch stays no_fit."""
    model = _model([("1", 2.05, -0.8), ("2", 2.05, 0.8), ("3", -2.05, 0.8), ("4", -2.05, -0.8)])
    board = [("1", -2.05, 0.8), ("2", 2.05, 0.8), ("3", 2.05, -0.8), ("4", -2.05, -0.8)]
    a = cv.analyse(_kfp("S1", board), model)
    assert a["verdict"] == "ok" and "geometry" in a["basis"] and set(a["corrections"]) == {0.0, 180.0}
    # the turn with pin 1 on pad 1 first: model 1 is (2.05, -0.8), board 1 (-2.05, 0.8)
    assert a["corrections"][0] == 180.0
    assert cv.analyse(_kfp("U1", board), model)["verdict"] == "no_fit"
    wider = [("1", -3.0, 0.8), ("2", 3.0, 0.8), ("3", 3.0, -0.8), ("4", -3.0, -0.8)]
    assert cv.analyse(_kfp("S1", wider), model)["verdict"] == "no_fit"


def test_placement_offset_ignores_a_thermal_pad_split_differently():
    """ESP32-S3-WROOM-1: pad 41 is 21 entries on the board and 9 in the LCSC
    model, not all centred. The perimeter pads coincide, so there is no offset;
    a plain mean called it 0.6 mm and the CPL put the module off its pads."""
    ring = [("1", -4.0, -4.0), ("2", 4.0, -4.0), ("3", 4.0, 4.0), ("4", -4.0, 4.0)]
    model = _model(ring + [("41", x, 0.0) for x in (-1.0, 0.0, 1.0)])
    pads = ring + [("41", x * 0.5 + 1.0, y * 0.5 + 1.0) for x in (-1, 0, 1) for y in (-1, 0, 1)]
    fp = _kfp("U1", pads)
    assert cv.placement_offset(fp, model, 0.0) is None
    row = cv.check_part(fp, model, {"rot": 0.0, "x": 10.0, "y": 10.0})
    assert row["offset_mm"] == 0.0


def test_placement_offset_when_origin_isnt_the_part_centre():
    """A connector whose footprint origin is off its pads' centre: the CPL has
    to carry the difference (SparkFun's Qwiic 1.87 mm), or JLC places it off
    its pads. A small one is left alone."""
    model = _model([("1", -1.5, -1.0), ("2", 1.5, -1.0), ("3", -1.5, 1.0), ("4", 1.5, 1.0)])
    pads = [(n, x, y + 1.87) for n, x, y in
            [("1", -1.5, -1.0), ("2", 1.5, -1.0), ("3", -1.5, 1.0), ("4", 1.5, 1.0)]]
    dx, dy = cv.placement_offset(_kfp("J2", pads), model, 0.0)
    assert abs(dx) < 1e-6 and abs(dy - 1.87) < 1e-6
    near = [(n, x, y - 1.87 + 0.1) for n, x, y in pads]
    assert cv.placement_offset(_kfp("J2", near), model, 0.0) is None


# ------------------------------------------- Standard PCBA for parts on both sides

def _pcba_board(tmp_path, w, h, fiducials=0, margin=8.0):
    """A w x h board with two pads `margin` in from the edges, and fiducials
    in the margin."""
    edges = "".join(
        f'(gr_line (start {a} {b}) (end {c} {d}) (layer "Edge.Cuts") '
        '(stroke (width 0.1) (type solid)))'
        for a, b, c, d in ((0, 0, w, 0), (w, 0, w, h), (w, h, 0, h), (0, h, 0, 0)))
    pad = ('(footprint "t:R" (layer "F.Cu") (at {x} {y}) (property "Reference" '
           '"{r}" (at 0 0) (layer "F.SilkS")) (pad "1" smd rect (at 0 0) '
           '(size 1 1) (layers "F.Cu")))')
    body = pad.format(x=margin, y=margin, r="R1") \
        + pad.format(x=w - margin, y=h - margin, r="R2")
    for i in range(fiducials):
        body += ('(footprint "Fiducial:Fiducial_1mm" (layer "F.Cu") '
                 f'(at {2 + i} 2) (property "Reference" "FID{i}" (at 0 0) '
                 '(layer "F.SilkS")) (pad "" smd circle (at 0 0) (size 1 1) '
                 '(layers "F.Cu")))')
    f = tmp_path / "b.kicad_pcb"
    f.write_text('(kicad_pcb (version 20240108) (generator "t")'
                 '(layers (0 "F.Cu" signal) (31 "B.Cu" signal) '
                 '(44 "Edge.Cuts" user))' + edges + body + ")")
    import geom
    return geom.load_board(f)


def _layers_csv(tmp_path, *layers):
    cpl = tmp_path / "CPL.csv"
    with cpl.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for i, lay in enumerate(layers):
            w.writerow([f"R{i}", 1, -1, lay, 0])
    return cpl


def _pcba_kinds(bg, cpl):
    vios: list = []
    dfm_check._standard_pcba(bg, cpl, vios)
    return vios


def test_both_sides_small_board_warns_with_what_it_lacks(tmp_path):
    """twin-side's R2: 70 x 58, no fiducials, no rails. Warns, never fails."""
    bg = _pcba_board(tmp_path, 70, 58, margin=2.0)
    v = _pcba_kinds(bg, _layers_csv(tmp_path, "Top", "Bottom"))
    assert [(x["kind"], x["severity"]) for x in v] == [
        ("both_sides_needs_panel", "warning")]
    for word in ("70.0 x 58.0", "rails", "0 fiducials", "Standard"):
        assert word in v[0]["msg"], word


def test_one_sided_or_no_cpl_says_nothing(tmp_path):
    bg = _pcba_board(tmp_path, 40, 40)
    assert _pcba_kinds(bg, _layers_csv(tmp_path, "Top", "Top")) == []
    assert _pcba_kinds(bg, _layers_csv(tmp_path, "Bottom")) == []
    assert _pcba_kinds(bg, tmp_path / "missing.csv") == []
    assert _pcba_kinds(bg, None) == []


def test_both_sides_board_that_is_its_own_panel_passes(tmp_path):
    bg = _pcba_board(tmp_path, 80, 70, fiducials=3)
    assert _pcba_kinds(bg, _layers_csv(tmp_path, "Top", "Bottom")) == []
    # each thing on its own is still missed
    for kw, word in (({"fiducials": 2}, "2 fiducials"),
                     ({"fiducials": 3, "margin": 2.0}, "rails")):
        bg = _pcba_board(tmp_path, 80, 70, **kw)
        v = _pcba_kinds(bg, _layers_csv(tmp_path, "Top", "Bottom"))
        assert len(v) == 1 and word in v[0]["msg"]
    bg = _pcba_board(tmp_path, 80, 60, fiducials=3)
    v = _pcba_kinds(bg, _layers_csv(tmp_path, "Top", "Bottom"))
    assert len(v) == 1 and "under 70 mm" in v[0]["msg"]


@pytest.mark.parametrize("sub", ["panel", "."])
def test_a_declared_panel_is_accepted(tmp_path, sub):
    """panel.json as eda-crossover's panel generator writes it, under fab/panel
    or beside the CPL, stands in for the small board."""
    bg = _pcba_board(tmp_path, 70, 58, margin=2.0)
    cpl = _layers_csv(tmp_path, "Top", "Bottom")
    d = tmp_path / sub
    d.mkdir(exist_ok=True)
    good = {"width_mm": 70.0, "height_mm": 128.0,
            "rail_mm": {"sides": 5.0, "top_bottom": 5.0},
            "fiducials": [[10, 3.85], [60, 3.85], [15, 124.15]]}
    (d / "panel.json").write_text(json.dumps(good))
    assert _pcba_kinds(bg, cpl) == []
    (d / "panel.json").write_text(json.dumps({**good, "height_mm": 60.0}))
    v = _pcba_kinds(bg, cpl)
    assert len(v) == 1 and "panel.json" in v[0]["msg"] and "under 70" in v[0]["msg"]
    (d / "panel.json").write_text(json.dumps({**good, "rail_mm": 0}))
    assert "rails" in _pcba_kinds(bg, cpl)[0]["msg"]
    (d / "panel.json").write_text(json.dumps({**good, "fiducials": []}))
    assert "0 fiducials" in _pcba_kinds(bg, cpl)[0]["msg"]


# ---------------------------------------- an LED* footprint, K on pad 1 (C2286)

# boards/twin-side/parts/easyeda/C2286.json on hw-lab h2/twin-side-route:
# KT-0603R, pad 2 = K west, pad 1 = A east, as the LCSC model has it.
C2286 = {"lcsc": "C2286", "title": "KT-0603R",
         "package": "LED-SMD_L1.6-W0.8-R-RD", "origin": [4000.8828, 3003.1398],
         "pads": ["PAD~RECT~3997.93~3003.14~3.1496~3.1496~1~~2~0~~0~gge5",
                  "PAD~RECT~4003.836~3003.14~3.1496~3.1496~1~~1~0~~0~gge15"],
         "silk": [],
         "pins": ["P~show~0~2~-20~-5~180~gge28~0^^-20~-5^^M -20 -5 h 15~#800"
                  "^^0~-1~-5~0~K~start~~~#800^^0~-9~-9~0~2~end~~~#800",
                  "P~show~0~1~20~-5~0~gge37~0^^20~-5^^M 20 -5 h -15~#800"
                  "^^0~1~-5~0~A~end~~~#800^^0~9~-9~0~1~start~~~#800"]}
PASSIVES_LED = [("1", -0.7875, 0.0), ("2", 0.7875, 0.0)]


def test_led_footprint_is_k_on_pad_1_and_the_model_turns_to_match():
    """Device:LED on passives:LED0603, no pinfunctions: pad 1 = K on the west,
    as the model's K is. Correction 0. The number fit (180) put it on backwards
    with verdict ok."""
    model = easyeda.parse(C2286)
    fp = _kfp("D102", PASSIVES_LED, name="passives:LED0603")
    a = cv.analyse(fp, model)
    assert a["verdict"] == "ok" and a["corrections"] == [0.0]
    assert a["basis"].startswith("pin role")
    assert a["number_corrections"] == [180.0]
    assert cv.derive_rotation(fp, model) == 0.0
    row = cv.check_part(fp, model, {"x": 10.0, "y": 10.0, "rot": 180.0})
    assert row["verdict"] == "wrong_polarity"
    assert cv.check_part(fp, model, {"x": 10.0, "y": 10.0, "rot": 0.0})["verdict"] == "ok"


def test_led_footprint_on_the_bottom_follows_the_bottom_rule():
    model = easyeda.parse(C2286)
    fp = _kfp("D301", [(n, x, -y) for n, x, y in PASSIVES_LED], rot=-90.0,
              name="passives:LED0603")
    fp.side = "bottom"
    assert cv.analyse(fp, model)["corrections"] == [0.0]
    assert cv.derive_rotation(fp, model) == 270.0     # 180 + 90 + 0


def test_imported_led_footprint_keeps_its_models_numbering():
    """The LED rule mustn't reach a footprint pulled from the part itself."""
    model = easyeda.parse(C2286)
    pads = [("2", -0.75, 0.0), ("1", 0.75, 0.0)]
    fp = _kfp("D1", pads, name="mcu:LED-SMD_L1.6-W0.8-R-RD")
    assert cv.board_roles(fp, model) == {"2": "K", "1": "A"}
    a = cv.analyse(fp, model)
    assert a["corrections"] == [0.0] and "number_corrections" not in a


@pytest.mark.parametrize("ref,pads,name", [
    ("U1", PASSIVES_LED, "passives:LED0603"),                  # not a D/LED ref
    ("D1", PASSIVES_LED + [("3", 0.0, 1.0)], "x:LED-RGB"),     # not two pads
    ("D1", PASSIVES_LED, "passives:D0603"),                    # not LED*
])
def test_led_rule_stays_narrow(ref, pads, name):
    assert cv.board_roles(_kfp(ref, pads, name=name), None) == {}
