"""Connector mating check (lib/matinglib.py, check_mating.py, the place gate's
"mating" family).

Each synthetic case builds its own board and asserts both the connector or
part that is flagged and the one that is not. The two real-board cases are
the owner's: PCB-0021-A lipo-boost J4 (USB-A, mouth into the board toward L1)
and PCB-0018-A bldc-motor-driver J701/J702 (mouths into the board, backs at
the edge), read from trimmed copies frozen before and after each fix under
tests/fixtures/mating/ (README there), never from the live boards repo. The
golden corpus's planted fault (usb-faces-inward) is checked here too, and
PCB-0023-A's edge-launch SMAs, whose 3D models face into the board, are read
from the boards repo through tests/_boards.py (skipped when it is absent).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import check_mating  # noqa: E402
import matinglib  # noqa: E402
import place_metrics  # noqa: E402
import placelib  # noqa: E402
import yaml  # noqa: E402
from _boards import real_board  # noqa: E402

FIXTURES = REPO / "tests" / "fixtures" / "mating"
GOLDEN = REPO / "tests" / "golden"


def _pad(num, x, y, w, h, kind="smd rect", layers='"F.Cu"'):
    return (f'    (pad "{num}" {kind} (at {x} {y}) (size {w} {h})'
            f' (layers {layers}))\n')


def _fp(ref, fpid, x, y, angle=0.0, pads="", cy=(-1.0, -1.0, 1.0, 1.0),
        layer="F.Cu", extra=""):
    crt = "F.CrtYd" if layer == "F.Cu" else "B.CrtYd"
    at = f"(at {x} {y} {angle})" if angle else f"(at {x} {y})"
    return (f'  (footprint "t:{fpid}" (layer "{layer}")\n    {at}\n'
            f'    (property "Reference" "{ref}" (at 0 0 0))\n'
            f'    (attr smd)\n'
            f'    (fp_rect (start {cy[0]} {cy[1]}) (end {cy[2]} {cy[3]})'
            f' (stroke (width 0.05)) (fill no) (layer "{crt}"))\n'
            f'{pads}{extra})\n')


def _board(tmp_path, body, w=30.0, h=20.0) -> Path:
    p = tmp_path / "b.kicad_pcb"
    p.write_text(f"""(kicad_pcb
  (version 20260206) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
  (setup)
  (gr_rect (start 0 0) (end {w} {h}) (stroke (width 0.1)) (fill no)
    (layer "Edge.Cuts"))
{body})
""", encoding="utf-8")
    return p


# A USB-C-like receptacle: the contact row at the back (local y = -2), two
# shell tabs further forward, body reaching to y = +4 - the mouth is +y.
USBC_PADS = "".join(_pad(str(i + 1), -1.5 + 0.5 * i, -2.0, 0.3, 1.0)
                    for i in range(7)) + \
    _pad("S1", -4.0, 1.0, 1.2, 1.8) + _pad("S2", 4.0, 1.0, 1.2, 1.8)
USBC_CY = (-4.5, -3.0, 4.5, 4.0)


def usbc(ref, x, y, angle=0.0):
    return _fp(ref, "USB-C_TEST", x, y, angle, USBC_PADS, USBC_CY)


def small(ref, x, y, fpid="R0603_H0.5", layer="F.Cu"):
    return _fp(ref, fpid, x, y, pads=_pad("1", -0.5, 0, 0.5, 0.5)
               + _pad("2", 0.5, 0, 0.5, 0.5), layer=layer)


def _run(pcb):
    return matinglib.violations(placelib.PlaceModel(pcb), pcb)


def test_family_table_matches_and_skips():
    assert matinglib.family_of("aiee:USB-A-TH_U-A-24DD-Y-13")["family"] == "usb_a"
    assert matinglib.family_of(
        "aiee:CONN-SMD_SH-SM08B-GHS-TB")["family"] == "wire_to_board_side"
    assert matinglib.family_of("x:FPC-SMD_24P-P0.50")["family"] == "fpc_ffc"
    assert matinglib.family_of("aiee:HDR-TH_4P-P2.54-V-M")["entry"] == "vertical"
    # the DO-214AC diode package is called SMA too - not a connector
    assert matinglib.family_of("aiee:SMA_L4.3-W2.6-LS5.2-RD") is None
    assert matinglib.family_of("aiee:CONN-TH_P5.08_KF128-5.08-2P") is None
    # BAT Wireless KE = "PCB-end" edge launch (PCB-0023-A), KWE = right
    # angle; both enter parallel to the board. Plain SMA-SMD is vertical.
    for name in ("aiee:SMA-SMD_BWSMA-KE-P001", "aiee:SMA-TH_BWSMA-KWE-Z001"):
        assert matinglib.family_of(name)["family"] == "sma_edge", name
    assert matinglib.family_of("aiee:SMA-SMD_BWSMA-KHD-P001")["family"] \
        == "sma_vertical"


def test_mouth_read_from_pad_layout_and_rotation(tmp_path):
    pcb = _board(tmp_path, usbc("J1", 15, 17) + usbc("J2", 26, 10, 90))
    m = placelib.PlaceModel(pcb)
    assert matinglib.mouth_local(m.footprints["J1"]) == "+y"
    _, facts = matinglib.violations(m, pcb)
    by = {f["ref"]: f for f in facts}
    assert by["J1"]["mouth"] == "+y"
    # angle 90 turns local +y to board +x (abs = pos + R(-angle).local)
    assert by["J2"]["mouth"] == "+x"


def test_mouth_at_edge_passes_and_faces_inward_fails(tmp_path):
    # J1: mouth (+y) over the bottom edge. J2: back at the top edge, mouth
    # down into 11 mm of board.
    pcb = _board(tmp_path, usbc("J1", 8, 17) + usbc("J2", 22, 4.5))
    vs, facts = _run(pcb)
    assert not [v for v in vs if "J1" in v["refs"]]
    inward = [v for v in vs if v["kind"] == "mating_faces_inward"]
    assert [v["refs"] for v in inward] == [["J2"]]
    assert inward[0]["nearest_edge"] == "-y" and inward[0]["mouth"] == "+y"


def test_mouth_inset_beyond_tolerance(tmp_path):
    # front of the courtyard at y = 18: 2 mm inside the bottom edge (> 1.0)
    pcb = _board(tmp_path, usbc("J1", 8, 14) + usbc("J2", 22, 16.5))
    vs, _ = _run(pcb)
    assert [v["refs"] for v in vs if v["kind"] == "mating_mouth_inset"] \
        == [["J1"]]  # J2 sits 0.5 mm inside: within tolerance
    assert not [v for v in vs if "J2" in v["refs"]]


def test_insertion_zone_blocked_by_part_in_front_only(tmp_path):
    # J1 faces into the board from the top edge; R1 in front of its mouth
    # blocks, R2 off to the side does not, R3 in front but on the back side
    # does not.
    pcb = _board(tmp_path, usbc("J1", 15, 4.5) + small("R1", 15, 14)
                 + small("R2", 3, 14) + small("R3", 16, 16, layer="B.Cu"))
    vs, facts = _run(pcb)
    blocked = [v for v in vs if v["kind"] == "mating_zone_blocked"]
    assert len(blocked) == 1 and blocked[0]["blocked_by"] == ["R1"]


def test_vertical_header_needs_finger_room(tmp_path):
    hdr = "".join(_pad(str(i + 1), 2.54 * i, 0, 1.7, 1.7, "thru_hole circle",
                       '"*.Cu"') for i in range(4))
    body = (_fp("J1", "HDR-TH_4P-P2.54-V-M", 5, 10, pads=hdr,
                cy=(-1.3, -1.3, 8.9, 1.3))
            + small("C1", 8, 13, fpid="CAP_D6.3-H5.4")     # tall, 1.7 mm off
            + small("R1", 10, 13)                           # short, close
            + small("C2", 8, 18, fpid="CAP_D6.3-H5.4")     # tall, far
            + _fp("J2", "HDR-TH_4P-P2.54-V-M", 5, 13, pads=hdr,
                  cy=(-1.3, -1.3, 8.9, 1.3)))              # header beside
    pcb = _board(tmp_path, body)
    vs, facts = _run(pcb)
    blocked = [v for v in vs if v["kind"] == "mating_zone_blocked"]
    assert sorted(v["connector"] for v in blocked) == ["J1", "J2"]
    assert all(v["blocked_by"] == ["C1"] for v in blocked)


def test_symmetric_pads_warn_not_guess(tmp_path):
    pads = _pad("1", -1, 0, 0.6, 0.6) + _pad("2", 1, 0, 0.6, 0.6)
    pcb = _board(tmp_path, _fp("J1", "USB-C_SYM", 15, 10, pads=pads,
                               cy=(-2, -2, 2, 2)))
    vs, _ = _run(pcb)
    assert [(v["kind"], v["severity"]) for v in vs] == [
        ("mating_direction_unknown", "warning")]


def test_place_gate_and_verify_check_carry_it(tmp_path):
    pcb = _board(tmp_path, usbc("J1", 15, 4.5) + usbc("J2", 8, 17))
    payload, _ = place_metrics.run(["--pcb", str(pcb)])
    assert "mating" in payload["coverage"]["failed"]
    assert [f["ref"] for f in payload["metrics"]["mating"]] == ["J1", "J2"]
    rep, _ = check_mating.run(["--pcb", str(pcb)])
    assert rep["status"] == "violations"
    assert {v["connector"] for v in rep["violations"]} == {"J1"}
    assert all(v["source"] == "check_mating" for v in rep["violations"])


# ---------------------------------------------------- the owner's two boards

def _frozen(name):
    return _run(FIXTURES / f"{name}.kicad_pcb")


def test_lipo_boost_fails_on_j4_before_its_fix():
    vs, _ = _frozen("lipo_boost_before")
    j4 = {v["kind"]: v for v in vs if v["connector"] == "J4"}
    assert set(j4) == {"mating_faces_inward", "mating_zone_blocked"}
    assert j4["mating_faces_inward"]["nearest_edge"] == "+x"
    assert j4["mating_faces_inward"]["mouth"] == "-x"
    assert j4["mating_zone_blocked"]["blocked_by"] == ["L1"]
    # J2, the battery plug at the bottom edge, mates fine
    assert {v["connector"] for v in vs} == {"J4"}


def test_lipo_boost_passes_once_j4_faces_the_edge():
    vs, facts = _frozen("lipo_boost_after")
    assert vs == []
    j4 = next(f for f in facts if f["ref"] == "J4")
    assert j4["mouth"] == j4["nearest_edge"] == "+x"


def test_bldc_motor_driver_fails_on_j701_and_j702_before_their_fix():
    vs, _ = _frozen("bldc_motor_driver_before")
    inward = {v["connector"]: v for v in vs
              if v["kind"] == "mating_faces_inward"}
    assert set(inward) == {"J701", "J702"}
    for v in inward.values():
        assert v["nearest_edge"] == "-y" and v["mouth"] == "+y"
    blocked = {v["connector"]: v["blocked_by"] for v in vs
               if v["kind"] == "mating_zone_blocked"}
    assert blocked == {"J701": ["F701"], "J702": ["U301"]}
    # J601, the vertical box header beside them, mates fine
    assert not [v for v in vs if v["connector"] == "J601"]


def test_bldc_motor_driver_passes_once_j701_and_j702_face_the_edge():
    vs, facts = _frozen("bldc_motor_driver_after")
    assert vs == []
    for f in facts:
        if f["ref"] in ("J701", "J702"):
            assert f["mouth"] == f["nearest_edge"] == "-y"


# ------------------------------------------------ the golden corpus mutant

def test_golden_usb_faces_inward_mutant_caught():
    m = yaml.safe_load((GOLDEN / "manifest.yaml").read_text(
        encoding="utf-8"))["mutants"]["usb-faces-inward"]
    assert m["check"] == "check_mating"
    rep, _ = check_mating.run(["--pcb", str(
        GOLDEN / "mutants" / "usb-faces-inward" / f"{m['board']}.kicad_pcb")])
    inward = [v for v in rep["violations"] if v["kind"] == m["expect"]["kind"]]
    assert [v["refs"] for v in inward] == [[m["expect"]["ref"]]]
    assert inward[0]["mouth"] == "+x" and inward[0]["nearest_edge"] == "-x"
    # the golden it was made from mates fine
    gold, _ = check_mating.run(["--pcb", str(
        GOLDEN / m["board"] / f"{m['board']}.kicad_pcb")])
    assert gold["status"] == "pass"


# ---------------------------------- the straddle class and the 3D model's way

# An edge-launch SMA like PCB-0023-A's BWSMA-KE-P001: five SMD pads at
# x = 0 (three on F.Cu, two on B.Cu, the legs clamping the board edge), the
# flange and barrel reaching to +x - the mouth is +x.
STRADDLE_PADS = "".join(_pad(str(i), 0, y, 4.8, 1.2)
                        for i, y in ((1, 2.55), (2, -2.55), (5, 0.0))) + \
    "".join(_pad(str(i), 0, y, 4.8, 1.2, layers='"B.Cu"')
            for i, y in ((3, -2.55), (4, 2.55)))
STRADDLE_CY = (-2.65, -3.25, 4.42, 3.25)


def _box_wrl(path, x0, x1, y0, y1, z0, z1):
    """A one-shape VRML box (mm in, VRML units of 2.54 mm out)."""
    pts = ", ".join(f"{x / 2.54} {y / 2.54} {z / 2.54}"
                    for x in (x0, x1) for y in (y0, y1) for z in (z0, z1))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#VRML V2.0 utf8\nShape { geometry IndexedFaceSet {"
                    f" coord Coordinate {{ point [ {pts} ] }} }} }}\n",
                    encoding="utf-8")
    return path


def _sma(ref, fpid, x, y, angle=0.0, model=None, rot=(0, 0, 0)):
    extra = ""
    if model is not None:
        extra = (f'    (model "{model}" (offset (xyz 0 0 0))'
                 f' (scale (xyz 1 1 1))'
                 f' (rotate (xyz {rot[0]} {rot[1]} {rot[2]})))\n')
    return _fp(ref, fpid, x, y, angle, STRADDLE_PADS, STRADDLE_CY,
               extra=extra)


def test_pads_on_both_sides_make_a_vertical_name_horizontal(tmp_path):
    # named like a vertical SMA, but its legs straddle the board: J1 faces
    # the right edge, J2 beside it (turned 180) faces into 25 mm of board
    pcb = _board(tmp_path, _sma("J1", "SMA-SMD_TEST", 27.5, 5)
                 + _sma("J2", "SMA-SMD_TEST", 27.5, 15, 180))
    vs, facts = _run(pcb)
    by = {f["ref"]: f for f in facts}
    for ref in ("J1", "J2"):
        assert by[ref]["family"] == "sma_vertical"
        assert by[ref]["entry"] == "horizontal"
        assert by[ref]["entry_from"] == "pads on both outer layers"
    assert by["J1"]["mouth"] == by["J1"]["nearest_edge"] == "+x"
    inward = [v for v in vs if v["kind"] == "mating_faces_inward"]
    assert [v["connector"] for v in inward] == ["J2"]
    assert not [v for v in vs if v["connector"] == "J1"]


def test_one_sided_vertical_sma_stays_vertical(tmp_path):
    pads = "".join(_pad(str(i), x, y, 1.0, 1.0) for i, (x, y) in
                   enumerate(((0, 0), (-2, -2), (2, -2), (-2, 2), (2, 2))))
    pcb = _board(tmp_path, _fp("J1", "SMA-SMD_TEST", 15, 10, pads=pads,
                               cy=(-3, -3, 3, 3)))
    _, facts = _run(pcb)
    assert facts[0]["entry"] == "vertical" and "entry_from" not in facts[0]


def test_model_turned_round_is_caught_and_a_shifted_one_is_not(tmp_path):
    # The model is drawn mouth +x from the leg line, like the copper. J1
    # carries it as drawn; J2 turned 180 about z (PCB-0023-A's fault: it
    # then lies wholly behind the legs); J3 pushed 9 mm back by its offset,
    # so its centre sits behind the legs but it still reaches ahead of them
    # (PCB-0025-A J101's case, 5.6 mm back): misplaced, not turned round.
    wrl = _box_wrl(tmp_path / "lib" / "t.3dshapes" / "sma.wrl",
                   0.45, 13.95, -3.25, 3.25, -3.25, 3.25)
    shifted = _sma("J3", "SMA-SMD_BWSMA-KE-P001", 27.5, 17, model=wrl)
    shifted = shifted.replace("(offset (xyz 0 0 0))", "(offset (xyz -9 0 0))")
    pcb = _board(tmp_path, _sma("J1", "SMA-SMD_BWSMA-KE-P001", 27.5, 3,
                                model=wrl)
                 + _sma("J2", "SMA-SMD_BWSMA-KE-P001", 27.5, 10, model=wrl,
                        rot=(0, 0, 180)) + shifted)
    vs, facts = _run(pcb)
    by = {f["ref"]: f for f in facts}
    assert by["J1"]["family"] == "sma_edge"
    assert by["J1"]["model_span_mm"] == [0.45, 13.95]
    assert by["J2"]["model_span_mm"] == [-13.95, -0.45]
    assert by["J3"]["model_span_mm"] == [-8.55, 4.95]
    assert [(v["kind"], v["connector"]) for v in vs] == [
        ("mating_model_reversed", "J2")]
    v = vs[0]
    assert v["mouth"] == "+x" and v["model_mouth"] == "-x"
    assert v["severity"] == "error"


def test_model_found_beside_the_board_and_absent_is_not_failed(tmp_path):
    # a path baked in another checkout resolves to the workspace's
    # lib/*.3dshapes; no .wrl at all is a fact, never a failure
    ws = tmp_path / "ws"
    _box_wrl(ws / "lib" / "t.3dshapes" / "sma.wrl",
             0.45, 13.95, -3.25, 3.25, -3.25, 3.25)
    (ws / "kicad").mkdir()
    pcb = _board(ws / "kicad", _sma(
        "J1", "SMA-SMD_BWSMA-KE-P001", 27.5, 5,
        model="/elsewhere/lib/t.3dshapes/sma.wrl", rot=(0, 0, 180))
        + _sma("J2", "SMA-SMD_BWSMA-KE-P001", 27.5, 15,
               model="/elsewhere/lib/t.3dshapes/gone.step"))
    vs, facts = _run(pcb)
    by = {f["ref"]: f for f in facts}
    assert [v["connector"] for v in vs] == ["J1"]
    assert by["J2"]["model_span_mm"] is None


def test_gan_rf_inverter_smas_face_their_edges_with_models_turned_round():
    """PCB-0023-A J101/J102/J501 (owner, 2026-10-06: "SMAs backwards"): the
    copper faces each SMA's edge, the 3D model (rotate 0 270 180) faces
    into the board. Before the fix the name read as a vertical SMA and the
    direction was never checked."""
    ws = real_board("gan-rf-inverter")
    pcb = next((ws / "kicad").glob("*.kicad_pcb"))
    vs, facts = _run(pcb)
    smas = {f["ref"]: f for f in facts if f["ref"] in ("J101", "J102", "J501")}
    assert set(smas) == {"J101", "J102", "J501"}
    for f in smas.values():
        assert f["family"] == "sma_edge" and f["entry"] == "horizontal"
        assert f["mouth"] == f["nearest_edge"]
    copper = [v for v in vs if v["connector"] in smas
              and v["kind"] != "mating_model_reversed"]
    assert copper == []
    # the model fault is flagged for exactly the SMAs whose model still lies
    # behind the legs, so this holds before and after the board is fixed
    reversed_ = {v["connector"] for v in vs
                 if v["kind"] == "mating_model_reversed"}
    assert reversed_ == {r for r, f in smas.items()
                         if f["model_span_mm"] and f["model_span_mm"][1] < 1}
    assert all(f["model_span_mm"] is not None for f in smas.values())
