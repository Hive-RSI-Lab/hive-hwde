"""Nets across boards through a cable (#485): check_mate_pins.py mount
"cable" with a `cable` description, lib/cablelib.py.

Known answers for the FFC rule (straight when Type B and the two sockets have
the same handedness, or Type A and they differ; reversed otherwise), derived
by hand for a flat cable between two facing sockets on side-by-side boards:
both top-contact means the exposed face is up at both ends, so the cable is
Type A, and the conductor at the low-x edge meets pin 1 of one socket and pin
N of the other (turned 180 degrees): reversed. Swap one socket for a
bottom-contact one and the flat cable must be Type B, still reversed.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import cablelib  # noqa: E402
import check_mate_pins  # noqa: E402

FFC = "aiee:FPC-SMD_6P-P0.50_TEST_FFC6"
FFC_OTHER = "aiee:FPC-SMD_6P-P0.50_OTHER_FFC6"
IDC6 = "Connector_IDC:IDC-Header_2x03_P2.54mm_Vertical"


def _ffc(ref, fpid, x, y, nets, rot=0, layer="F.Cu"):
    """A 1xN FFC socket: signal leads along x at y -1.29, two GND tabs at
    y +1.29 (as the XUNPU FPC-05F footprint). On B.Cu the locals are
    mirrored in y, as KiCad writes a flipped footprint."""
    n = len(nets)
    sy = -1.0 if layer == "B.Cu" else 1.0
    cu = "B" if layer == "B.Cu" else "F"
    pads = []
    for i, net in enumerate(nets):
        px = -0.25 * (n - 1) + 0.5 * i
        pads.append((str(i + 1), px, -1.29 * sy, net))
    span = 0.25 * (n - 1) + 1.7
    pads += [(str(n + 1), span, 1.29 * sy, "GND"),
             (str(n + 2), -span, 1.29 * sy, "GND")]
    body = "".join(
        f'    (pad "{num}" smd rect (at {px} {py} {rot}) (size 0.3 1.25)'
        f' (layers "{cu}.Cu" "{cu}.Mask")' + (f' (net "{net}")' if net else "")
        + ")\n" for num, px, py, net in pads)
    return (f'  (footprint "{fpid}" (layer "{layer}") (at {x} {y} {rot})\n'
            f'    (property "Reference" "{ref}" (at 0 -3 0))\n'
            f'    (attr smd)\n'
            f'    (fp_rect (start -{span + 1} -2.5) (end {span + 1} 2.5)'
            f' (stroke (width 0.05)) (fill no) (layer "{cu}.CrtYd"))\n'
            f'{body}  )\n')


def _idc(ref, x, y, nets):
    """2xN IDC header, pin 2k-1 at (k-1)*2.54, 0 and pin 2k under it."""
    body = "".join(
        f'    (pad "{i + 1}" thru_hole circle (at {2.54 * (i // 2)} '
        f'{2.54 * (i % 2)}) (size 1.7 1.7) (drill 1) (layers "*.Cu" "*.Mask")'
        + (f' (net "{n}")' if n else "") + ")\n" for i, n in enumerate(nets))
    return (f'  (footprint "{IDC6}" (layer "F.Cu") (at {x} {y})\n'
            f'    (property "Reference" "{ref}" (at 0 -2 0))\n'
            f'    (attr through_hole)\n{body}  )\n')


def _board(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"""(kicad_pcb
  (version 20260206) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
  (setup)
  (gr_rect (start 0 0) (end 40 30) (stroke (width 0.1)) (fill no)
    (layer "Edge.Cuts"))
{body})
""", encoding="utf-8")
    return path


def _run(tmp, main_fp, mate_fp, cable, net_map=None, wireviz=False):
    main = _board(tmp / "main.kicad_pcb", main_fp)
    _board(tmp / "front" / "front.kicad_pcb", mate_fp)
    pair = {"ref": "J3", "mate_ref": "J1", "mate_pcb": "front/front.kicad_pcb",
            "mount": "cable", "cable": cable}
    if net_map:
        pair["net_map"] = net_map
    cons = tmp / "constraints.json"
    cons.write_text(json.dumps({"mating_pairs": [pair]}), encoding="utf-8")
    argv = ["--pcb", str(main), "--constraints", str(cons)]
    if wireviz:
        argv += ["--wireviz", str(tmp / "harness.yaml")]
    payload, _ = check_mate_pins.run(argv)
    return payload


# a pinout whose reversal puts +3V3 on GND (pin 1 <-> pin 6)
NETS = ["+3V3", "/SDA", "/SCL", "/INT", "/IR", "GND"]
FRONT = ["+3V3", "SDA", "SCL", "INT", "IR", "GND"]


def _kinds(payload):
    return sorted({v["kind"] for v in payload["violations"]})


# ---------------------------------------------------------- FFC known answers

def test_same_part_type_b_is_straight(tmp_path):
    p = _run(tmp_path, _ffc("J3", FFC, 10, 10, NETS),
             _ffc("J1", FFC, 10, 10, FRONT, rot=180),
             {"kind": "ffc", "type": "B"})
    assert p["violations"] == []
    assert p["pairs"][0]["cable"]["name"] == "straight"
    assert p["pairs"][0]["cable"]["note"] == "the same part at both ends"


def test_same_part_type_a_is_reversed_and_3v3_meets_gnd(tmp_path):
    p = _run(tmp_path, _ffc("J3", FFC, 10, 10, NETS),
             _ffc("J1", FFC, 10, 10, FRONT, rot=180),
             {"kind": "ffc", "type": "A"})
    by_pin = {v["pin"]: v for v in p["violations"]}
    assert set(by_pin) == {"1", "2", "3", "4", "5", "6"}
    assert by_pin["1"]["mate_pin"] == "6" and by_pin["1"]["mate_net"] == "GND"
    assert by_pin["1"]["supply_short"] and by_pin["6"]["supply_short"]
    assert "supply short" in by_pin["1"]["msg"]
    assert not by_pin["2"]["supply_short"]          # SDA meets IR: a signal
    assert p["pairs"][0]["cable"]["name"] == "reversed"


@pytest.mark.parametrize("layer,rot", [("F.Cu", 0), ("F.Cu", 90),
                                       ("B.Cu", 0), ("B.Cu", 270)])
def test_same_part_rule_holds_whatever_the_placement(tmp_path, layer, rot):
    """The handedness is the part's: turning or flipping the mate board
    changes nothing (computed from the pads, with socket data given)."""
    ends = {"J3": {"contact": "bottom", "entry": "tabs"},
            "J1": {"contact": "bottom", "entry": "tabs"}}
    p = _run(tmp_path, _ffc("J3", FFC, 10, 10, NETS),
             _ffc("J1", FFC_OTHER, 10, 10, FRONT, rot=rot, layer=layer),
             {"kind": "ffc", "type": "B", "ends": ends})
    assert p["violations"] == [], p["violations"]
    assert "bottom-contact" in p["pairs"][0]["cable"]["note"]


# contact sides x entry x cable type -> map (the module's rule)
KNOWN = [
    ("top", "tabs", "top", "tabs", "A", "reversed"),
    ("top", "tabs", "top", "tabs", "B", "straight"),
    ("top", "tabs", "bottom", "tabs", "A", "straight"),
    ("top", "tabs", "bottom", "tabs", "B", "reversed"),
    ("bottom", "tabs", "bottom", "leads", "A", "straight"),
    ("bottom", "leads", "top", "tabs", "B", "straight"),
]


@pytest.mark.parametrize("ca,ea,cb,eb,ctype,want", KNOWN)
def test_contact_sides_vs_cable_type(tmp_path, ca, ea, cb, eb, ctype, want):
    ends = {"J3": {"contact": ca, "entry": ea},
            "J1": {"contact": cb, "entry": eb}}
    p = _run(tmp_path, _ffc("J3", FFC, 10, 10, NETS),
             _ffc("J1", FFC_OTHER, 10, 10, FRONT, rot=180),
             {"kind": "ffc", "type": ctype, "ends": ends})
    assert p["pairs"][0]["cable"]["name"] == want
    assert (p["violations"] == []) == (want == "straight")


def test_hand_derived_facing_sockets():
    """The docstring's case, straight from the geometry: socket 1 at the
    origin opening to +y, socket 2 turned 180 degrees opening to -y. Both
    top-contact give the same handedness, so a Type A cable (the one a flat
    run needs) is reversed; one bottom-contact flips it, and the flat run's
    Type B cable is reversed too."""
    def pads(rot180):
        s = -1 if rot180 else 1
        row = [(str(i + 1), None, s * (-1.25 + 0.5 * i), s * -1.29)
               for i in range(6)]
        return row + [("7", None, 3.0, s * 1.29), ("8", None, -3.0, s * 1.29)]
    s1 = cablelib.socket_handedness(pads(False), "front", "top", "tabs")
    s2 = cablelib.socket_handedness(pads(True), "front", "top", "tabs")
    s3 = cablelib.socket_handedness(pads(True), "front", "bottom", "tabs")
    assert s1 == s2 and s1 != s3
    assert cablelib.flat_map_name("A", s1, s2) == "reversed"
    assert cablelib.flat_map_name("B", s1, s3) == "reversed"


def test_different_parts_without_socket_data_are_unknown(tmp_path):
    p = _run(tmp_path, _ffc("J3", FFC, 10, 10, NETS),
             _ffc("J1", FFC_OTHER, 10, 10, FRONT),
             {"kind": "ffc", "type": "B"})
    assert _kinds(p) == ["cable_contact_unknown"]
    assert p["violations"][0]["severity"] == "error"
    assert "J3, J1" in p["violations"][0]["msg"]


def test_type_missing_says_which_type_is_straight(tmp_path):
    p = _run(tmp_path, _ffc("J3", FFC, 10, 10, NETS),
             _ffc("J1", FFC, 10, 10, FRONT), {"kind": "ffc"})
    assert _kinds(p) == ["cable_type_unknown"]
    assert "Type B cable is straight" in p["violations"][0]["msg"]


def test_declared_straight_with_type_a_is_a_mismatch(tmp_path):
    p = _run(tmp_path, _ffc("J3", FFC, 10, 10, NETS),
             _ffc("J1", FFC, 10, 10, FRONT),
             {"kind": "ffc", "type": "A", "pin_map": "straight"})
    assert _kinds(p) == ["cable_map_mismatch"]
    assert "needs Type B" in p["violations"][0]["msg"]


def test_dual_contact_is_a_warning(tmp_path):
    p = _run(tmp_path, _ffc("J3", FFC, 10, 10, NETS),
             _ffc("J1", FFC, 10, 10, FRONT),
             {"kind": "ffc", "type": "B",
              "ends": {"J1": {"contact": "dual", "entry": "tabs"}}})
    assert _kinds(p) == ["cable_orientation_ambiguous"]
    assert p["violations"][0]["severity"] == "warning"


def test_pin_count_mismatch(tmp_path):
    p = _run(tmp_path, _ffc("J3", FFC, 10, 10, NETS),
             _ffc("J1", FFC, 10, 10, FRONT[:4]), {"kind": "ffc", "type": "B"})
    assert _kinds(p) == ["cable_pins_mismatch"]


def test_net_names_differ_by_alias(tmp_path):
    front = ["+3V3", "SDA", "SCL", "FRONT_INT_N", "IR_OUT", "GND"]
    cable = {"kind": "ffc", "type": "B"}
    p = _run(tmp_path, _ffc("J3", FFC, 10, 10, NETS),
             _ffc("J1", FFC, 10, 10, front), cable)
    assert {v["pin"] for v in p["violations"]} == {"4", "5"}
    p = _run(tmp_path, _ffc("J3", FFC, 10, 10, NETS),
             _ffc("J1", FFC, 10, 10, front), cable,
             net_map={"INT": "FRONT_INT_N", "/IR": "IR_OUT"})
    assert p["violations"] == []


# ------------------------------------------------------- IDC and discrete

IDC_NETS = ["+5V", "GND", "/TX", "/RX", "GND", "/EN"]


def test_idc_straight_and_rows_swapped(tmp_path):
    p = _run(tmp_path, _idc("J3", 10, 10, IDC_NETS),
             _idc("J1", 10, 10, IDC_NETS), {"kind": "idc"})
    assert p["violations"] == []
    p = _run(tmp_path, _idc("J3", 10, 10, IDC_NETS),
             _idc("J1", 10, 10, IDC_NETS),
             {"kind": "idc", "pin_map": "rows_swapped"})
    by_pin = {v["pin"]: v for v in p["violations"]}
    assert by_pin["1"]["mate_pin"] == "2" and by_pin["1"]["supply_short"]
    assert by_pin["3"]["mate_net"] == "/RX"
    assert set(by_pin) == {"1", "2", "3", "4", "5", "6"}


def test_discrete_explicit_crossover(tmp_path):
    near = ["GND", "/TX", "/RX"]
    far = ["GND", "/RX", "/TX"]
    p = _run(tmp_path, _idc("J3", 10, 10, near), _idc("J1", 10, 10, far),
             {"kind": "discrete", "pin_map": {"1": "1", "2": "3", "3": "2"}})
    assert p["violations"] == []
    p = _run(tmp_path, _idc("J3", 10, 10, near), _idc("J1", 10, 10, far),
             {"kind": "discrete", "pin_map": {"1": "1", "2": "4"}})
    assert _kinds(p) == ["cable_pins_mismatch"]


def test_cable_mount_without_description_is_pin_n_to_n(tmp_path):
    """The old "cable" mount is unchanged."""
    main = _board(tmp_path / "m.kicad_pcb", _idc("J3", 10, 10, IDC_NETS))
    _board(tmp_path / "f" / "f.kicad_pcb", _idc("J1", 10, 10, IDC_NETS))
    cons = tmp_path / "c.json"
    cons.write_text(json.dumps({"mating_pairs": [
        {"ref": "J3", "mate_ref": "J1", "mate_pcb": "f/f.kicad_pcb",
         "mount": "cable"}]}), encoding="utf-8")
    p, _ = check_mate_pins.run(["--pcb", str(main), "--constraints",
                                str(cons)])
    assert p["violations"] == [] and "cable" not in p["pairs"][0]


# ------------------------------------------------------------------ WireViz

def test_wireviz_harness(tmp_path):
    import yaml
    _run(tmp_path, _ffc("J3", FFC, 10, 10, NETS),
         _ffc("J1", FFC, 10, 10, FRONT, rot=180),
         {"kind": "ffc", "type": "A", "name": "W1", "length_mm": 40,
          "pitch_mm": 0.5}, wireviz=True)
    doc = yaml.safe_load((tmp_path / "harness.yaml").read_text())
    assert set(doc["connectors"]) == {"main_J3", "front_J1"}
    assert doc["connectors"]["main_J3"]["pins"] == [1, 2, 3, 4, 5, 6]
    assert doc["connectors"]["main_J3"]["pinlabels"][0] == "+3V3"
    assert doc["cables"]["W1"] == {
        "wirecount": 6, "type": "FFC Type A, 0.5 mm pitch",
        "notes": "reversed (the same part at both ends)", "length": 0.04}
    assert doc["connections"] == [[{"main_J3": [1, 2, 3, 4, 5, 6]},
                                   {"W1": [1, 2, 3, 4, 5, 6]},
                                   {"front_J1": [6, 5, 4, 3, 2, 1]}]]
    wv = pytest.importorskip("wireviz.wireviz")
    wv.parse(tmp_path / "harness.yaml", output_formats=("gv", "tsv"),
             output_dir=tmp_path, output_name="harness")
    bom = (tmp_path / "harness.bom.tsv").read_text()
    assert "Cable, FFC Type A, 0.5 mm pitch, 6 wires" in bom


# --------------------------------------------- hifi-receiver core <-> front

HIFI = Path(os.environ.get("HIFI_RECEIVER",
                           Path.home() / "projects" / "hifi-receiver"))
HIFI_MAP = {"FFC_SCK": "LCD_SCK", "FFC_MOSI": "LCD_MOSI",
            "LCD_BL": "LCD_BL_PWM", "EXP_INT": "FRONT_INT_N",
            "IR_IN": "IR_OUT"}


@pytest.mark.parametrize("ctype", ["A", "B"])
def test_hifi_core_j3_to_front_j1(tmp_path, ctype):
    """The same XUNPU FPC-05F-20PH20 at both ends: Type B is pin N to pin N
    (what boards/ultra-front/README.md orders), Type A reversed. hifi's
    supplies are palindromic (1/20 GND, 2/19 +3V3, 3/18 +5V), so a reversed
    cable shorts no rail to GND, but ties GND to ENC_B, IR and LCD_BL."""
    core = HIFI / "boards" / "core" / "kicad" / "core.kicad_pcb"
    front = HIFI / "boards" / "ultra-front" / "kicad" / "ultra-front.kicad_pcb"
    if not (core.is_file() and front.is_file()):
        pytest.skip(f"needs hifi-receiver at {HIFI} (HIFI_RECEIVER)")
    cons = tmp_path / "constraints.json"
    cons.write_text(json.dumps({"mating_pairs": [{
        "ref": "J3", "mate_ref": "J1", "mate_pcb": str(front),
        "mount": "cable", "net_map": HIFI_MAP,
        "cable": {"kind": "ffc", "type": ctype}}]}), encoding="utf-8")
    p, _ = check_mate_pins.run(["--pcb", str(core), "--constraints",
                                str(cons)])
    mine = [v for v in p["violations"] if v["kind"] == "mate_pin_mismatch"]
    if ctype == "B":
        assert mine == [] and p["pairs"][0]["cable"]["name"] == "straight"
        return
    assert len(mine) == 14
    assert not any(v["supply_short"] for v in mine)
    assert {v["pin"] for v in mine if v["supply_on_signal"]} == {
        "4", "6", "10", "11", "15", "17"}
