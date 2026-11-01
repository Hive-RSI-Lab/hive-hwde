"""check_antenna and check_straps (issue #130): the two checks the first-spin
review of hifi-receiver-2's core did by hand (review-2 DIGITAL-19 and
DIGITAL-1/2), on synthetic boards and netlists."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import check_antenna  # noqa: E402
import check_straps  # noqa: E402
import verify_all  # noqa: E402


# ------------------------------------------------------------- the antenna

def _module(at: str, value: str = "ESP32-WROOM-32E") -> str:
    """An 18 x 26 mm module: pads down both long sides from local y -6 to
    +12, so the pad-free end is local -y: 13 - 6.45 = 6.55 mm, less the 1 mm
    PAD_MARGIN_MM by the end pads, 5.55 mm. A pad's
    (at) angle carries the footprint's, as KiCad writes it."""
    rot = at.split()[2] if len(at.split()) > 2 else "0"
    pads = []
    for i, y in enumerate(range(-6, 13, 2)):
        pads.append(f'(pad "{i + 1}" smd rect (at -8.5 {y} {rot}) (size 1 0.9) '
                    f'(layers "F.Cu") (net "GND"))')
        pads.append(f'(pad "{i + 21}" smd rect (at 8.5 {y} {rot}) (size 1 0.9) '
                    f'(layers "F.Cu") (net "GND"))')
    return f"""  (footprint "t:ESP32-WROOM" (at {at}) (layer "F.Cu")
    (property "Reference" "U1" (at 0 0 0) (layer "F.SilkS"))
    (property "Value" "{value}" (at 0 0 0) (layer "F.Fab"))
    (fp_rect (start -9 -13) (end 9 13) (stroke (width 0.05)) (fill no)
      (layer "F.CrtYd"))
    {' '.join(pads)})
"""


def _plane(layer: str) -> str:
    pts = "(pts (xy 0 0) (xy 60 0) (xy 60 40) (xy 0 40))"
    return (f'  (zone (net "GND") (layer "{layer}") (polygon {pts})\n'
            f'    (filled_polygon (layer "{layer}") {pts}))\n')


def _pcb(tmp_path, body: str) -> Path:
    p = tmp_path / "ant.kicad_pcb"
    p.write_text(f"""(kicad_pcb (version 20260206) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
  (setup)
  (gr_rect (start 0 0) (end 60 40) (stroke (width 0.1)) (fill no)
    (layer "Edge.Cuts"))
{body})
""", encoding="utf-8")
    return p


def _antenna(pcb, constraints=None):
    payload, _ = check_antenna.run(
        ["--pcb", str(pcb)] + (["--constraints", str(constraints)]
                               if constraints else []))
    return payload, {v["kind"] for v in payload["violations"]}


def test_antenna_over_the_edge_and_clear_passes(tmp_path):
    payload, kinds = _antenna(_pcb(tmp_path, _module("30 10")))
    assert payload["status"] == "pass", json.dumps(payload["violations"])
    mod = payload["checked"][0]["modules"][0]
    assert mod["antenna"] == "pcb" and abs(mod["depth_mm"] - 5.55) < 0.01
    # local y -13..-7.45 at (30, 10): board y -3..2.55, 3 mm over the edge
    assert mod["antenna_bbox"] == [21.0, -3.0, 39.0, 2.55]


def test_planes_under_the_antenna_fail_on_every_layer(tmp_path):
    """core's DIGITAL-19: GND fill under the antenna on each layer."""
    pcb = _pcb(tmp_path, _module("30 10") + _plane("F.Cu") + _plane("B.Cu"))
    payload, kinds = _antenna(pcb)
    errs = [v for v in payload["violations"] if v["kind"] == "antenna_copper"]
    assert sorted(v["layer"] for v in errs) == ["B.Cu", "F.Cu"]
    assert all(v["severity"] == "error" and v["refs"] == ["U1"] for v in errs)
    # 18 x 2.55 mm of the antenna is over the board
    assert abs(errs[0]["area_mm2"] - 18 * 2.55) < 0.5
    assert "antenna_side_clearance" in kinds


def test_a_track_under_the_antenna_fails(tmp_path):
    body = _module("30 10") + (
        '  (segment (start 25 1) (end 25 30) (width 0.2) (layer "B.Cu") '
        '(net "/I2S_DIN"))\n')
    payload, _ = _antenna(_pcb(tmp_path, body))
    (v,) = [v for v in payload["violations"] if v["kind"] == "antenna_copper"]
    assert v["layer"] == "B.Cu" and v["net"] == "/I2S_DIN"


def test_antenna_mid_board_warns(tmp_path):
    payload, kinds = _antenna(_pcb(tmp_path, _module("30 20")))
    assert kinds == {"antenna_inboard"}
    (v,) = payload["violations"]
    assert v["severity"] == "warning" and abs(v["inset_mm"] - 7.0) < 0.01


def test_rotated_module_finds_its_antenna_end(tmp_path):
    """At 90 degrees the pad-free end turns with the part: the antenna is
    5.55 mm deep along x, 18 mm wide along y, and touches no pad."""
    payload, _ = _antenna(_pcb(tmp_path, _module("20 20 90")))
    x0, y0, x1, y1 = payload["checked"][0]["modules"][0]["antenna_bbox"]
    assert abs((x1 - x0) - 5.55) < 0.01 and abs((y1 - y0) - 18) < 0.01


def test_value_decides_over_the_footprint_name(tmp_path):
    """core's U7: a WROVER-E (PCB antenna) on a footprint named -IE (u.FL)."""
    mods = check_antenna.load_modules()
    assert check_antenna.antenna_kind(
        "ESP32-WROVER-E-N8R8", "aiee:WIFI-SMD_ESP32-WROVER-IE8MB",
        mods)[0] == "pcb"
    assert check_antenna.antenna_kind(
        "", "aiee:WIFI-SMD_ESP32-WROVER-IE8MB", mods)[0] == "external"
    for ext in ("ESP32-WROOM-32UE", "ESP32-S3-WROOM-1U-N8R8",
                "ESP32-C3-MINI-1U"):
        assert check_antenna.antenna_kind(ext, "", mods)[0] == "external"
    for pcb in ("ESP32-S3-WROOM-1-N16R8", "ESP32-C3-MINI-1", "ESP-12F"):
        assert check_antenna.antenna_kind(pcb, "", mods)[0] == "pcb"
    assert check_antenna.antenna_kind("RFM95W-868S2", "", mods)[0] is None


def test_external_antenna_and_constraints_override(tmp_path):
    pcb = _pcb(tmp_path, _module("30 20", "ESP32-WROOM-32UE")
               + _plane("F.Cu"))
    payload, _ = _antenna(pcb)
    assert payload["status"] == "pass"
    cons = tmp_path / "constraints.json"
    cons.write_text(json.dumps({"antennas": {"U1": "pcb"}}),
                    encoding="utf-8")
    _, kinds = _antenna(pcb, cons)
    assert "antenna_copper" in kinds


# --------------------------------------------------------------- the straps

def _netlist(tmp_path, comps: dict, nets: dict) -> Path:
    """comps {ref: value}; nets {name: [(ref, pin, pinfunction)]}."""
    c = " ".join(f'(comp (ref "{r}") (value "{v}"))' for r, v in comps.items())
    n = []
    for i, (name, nodes) in enumerate(nets.items()):
        nd = " ".join(f'(node (ref "{r}") (pin "{p}") (pinfunction "{f}") '
                      f'(pintype "passive"))' for r, p, f in nodes)
        n.append(f'(net (code "{i + 1}") (name "{name}") {nd})')
    p = tmp_path / "s.net"
    p.write_text(f'(export (version "E") (components {c}) '
                 f'(nets {" ".join(n)}))', encoding="utf-8")
    pcb = tmp_path / "s.kicad_pcb"
    pcb.write_text("""(kicad_pcb (version 20260206) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
  (setup)
  (gr_rect (start 0 0) (end 60 40) (stroke (width 0.1)) (fill no)
    (layer "Edge.Cuts")))
""", encoding="utf-8")
    return pcb


def _core(tmp_path, expander="TCA9555", reset_pin=("3", "A2_3", "GND"),
          r45="100k", r_io12=None):
    """core's ESP32 / RMII oscillator / expander loop, cut down."""
    comps = {"U7": "ESP32-WROVER-E-N8R8", "U17": expander, "Y2": "50MHz",
             "R31": "10k", "R60": "33R", "R45": r45, "R43": "100k",
             "R6": "1k", "R7": "100k", "Q2": "2N7002", "C32": "1uF"}
    nets = {
        "+3V3": [("U7", "2", "3V3_2"), ("U17", "24", "VCC_24"),
                 ("Y2", "4", "VDD_4"), ("R31", "2", "")],
        "GND": [("U7", "1", "GND_1"), ("Y2", "2", "GND_2"),
                ("R45", "2", ""), ("R43", "2", ""), ("R7", "2", ""),
                ("C32", "2", "")],
        "/mcu/ESP_EN": [("U7", "3", "EN_3"), ("C32", "1", "")],
        "/ETH_CLK": [("U7", "25", "IO0_25"), ("R31", "1", ""),
                     ("R60", "2", "")],
        "/ethernet/ETH_OSC": [("Y2", "3", "OUTPUT_3"), ("R60", "1", "")],
        "/PHY_RST": [("Y2", "1", "Tri-state_1"), ("U17", "9", "P05_9"),
                     ("R45", "1", "")],
        "/PVDD_EN": [("U17", "7", "P03_7"), ("R43", "1", ""),
                     ("R6", "1", "")],
        "/PVDD_GATE": [("R6", "2", ""), ("R7", "1", ""), ("Q2", "1", "G")],
        "/I2S_DOUT_SUB": [("U7", "14", "IO12_14")],
    }
    ref, pin, net = reset_pin
    nets.setdefault(net, []).append(("U17", ref, pin))
    if r_io12:
        comps["R99"] = r_io12
        nets["/I2S_DOUT_SUB"].append(("R99", "1", ""))
        nets["+3V3"].append(("R99", "2", ""))
    pcb = _netlist(tmp_path, comps, nets)
    payload, _ = check_straps.run(["--pcb", str(pcb), "--netlist",
                                   str(tmp_path / "s.net")])
    return payload


def _by_kind(payload):
    out = {}
    for v in payload["violations"]:
        out.setdefault(v["kind"], []).append(v)
    return out


def test_core_gpio0_latches_the_oscillator(tmp_path):
    """DIGITAL-1/2 as built: P05's 100 k pull-up against R45 100 k is
    1.65 V on the oscillator's OE, and P05 holds through an ESP32 reset."""
    k = _by_kind(_core(tmp_path))
    (strap,) = k["strap_level"]
    assert strap["severity"] == "error" and strap["strap"] == "IO0"
    assert strap["events"] == ["mcu_reset", "por"]
    assert "maybe a clock" in strap["states"]["por"]
    assert "firmware" in strap["states"]["mcu_reset"]
    undef = {v["net"]: v["volts"] for v in k["io_reset_undefined"]}
    assert abs(undef["/PHY_RST"] - 1.65) < 0.01
    # 100 k up against 100 k || (1 k + 100 k) down: 1.10 V on PVDD_EN
    assert abs(undef["/PVDD_EN"] - 1.10) < 0.01
    assert [v["refs"] for v in k["io_reset_holds"]] == [["U17", "U7"]]
    # POWER-5: Q2's gate (through R6 1 k) at 1.10 V against 2N7002's 1.0 V
    (fet,) = k["fet_on_at_reset"]
    assert fet["refs"] == ["Q2", "U17"] and abs(fet["volts"] - 1.10) < 0.01


def test_core_fixed_with_tca9539_on_esp_en(tmp_path):
    """The review's fix: TCA9539 (no pull-ups) with RESET on ESP_EN, and
    R45 10 k. OE is low at power-on and after every ESP32 reset."""
    payload = _core(tmp_path, expander="TCA9539PWR",
                    reset_pin=("3", "~{RESET}_3", "/mcu/ESP_EN"), r45="10k")
    assert payload["status"] == "pass", json.dumps(payload["violations"])
    io0 = [s for s in payload["checked"][0]["straps"] if s["strap"] == "IO0"]
    assert io0[0]["por"].startswith("3.") and "high" in io0[0]["por"]


def test_mtdi_pulled_high_fails(tmp_path):
    k = _by_kind(_core(tmp_path, expander="TCA9539PWR",
                       reset_pin=("3", "~{RESET}_3", "/mcu/ESP_EN"),
                       r45="10k", r_io12="10k"))
    (v,) = k["strap_level"]
    assert v["strap"] == "IO12" and v["severity"] == "error"
    assert "1.8 V" in v["msg"]


def test_pin_names_normalise():
    assert check_straps.pin_name("IO0_25", "25") == "IO0"
    assert check_straps.pin_name("~{INT}_1", "1") == "INT"
    assert check_straps.pin_name("GPIO12", "14") == "IO12"


def test_no_netlist_with_a_known_mcu_warns(tmp_path):
    """blinky2's U1 is an STM32F103, which has a row since #602."""
    pcb = tmp_path / "b.kicad_pcb"
    pcb.write_text((REPO / "tests" / "golden" / "blinky2" /
                    "blinky2.kicad_pcb").read_text(encoding="utf-8"),
                   encoding="utf-8")
    payload, _ = check_straps.run(["--pcb", str(pcb)])
    (v,) = payload["violations"]
    assert v["kind"] == "no_netlist" and v["refs"] == ["U1"]


def test_both_checks_join_verify_all():
    names = {c["name"] for c in verify_all.CHECKS}
    assert {"check_antenna", "check_straps"} <= names


# ------------------------------------------- pins the chip pulls at reset

def _c3(tmp_path, gpio6: list, comps: dict | None = None, net="GPIO6"):
    """minuet v3 cut down: an ESP32-C3 whose GPIO6 (MTCK, pulled up by the
    chip from reset) reaches `gpio6`."""
    c = {"U4": "ESP32-C3-WROOM-02-N4", "R9": "10k", **(comps or {})}
    nets = {
        "+3.3V": [("U4", "1", "3V3_1"), ("R9", "2", "")],
        "GND": [("U4", "19", "GND_19")],
        "BOOT": [("U4", "8", "GPIO9_8"), ("R9", "1", "")],
        net: [("U4", "5", "GPIO6_5")] + gpio6,
    }
    pcb = _netlist(tmp_path, c, nets)
    payload, _ = check_straps.run(["--pcb", str(pcb), "--netlist",
                                   str(tmp_path / "s.net")])
    return _by_kind(payload), payload


def test_c3_gpio6_bare_on_a_connector_warns(tmp_path):
    """minuet-4: GPIO6 to the EXPANSION header with nothing but the
    chip's own pull-up; the accessory's LED strip comes on at reset."""
    k, _ = _c3(tmp_path, [("J4", "5", "Pin_5_5")], {"J4": "Conn_02x08"})
    (v,) = k["reset_pull_offboard"]
    assert v["net"] == "GPIO6" and v["pin"] == "IO6"
    assert "J4.5" in v["msg"] and "high" in v["msg"]
    assert "strap_level" not in k


def test_c3_gpio6_with_its_own_pull_is_quiet(tmp_path):
    """minuet v4's fix shape: a board pull on the line (here a pull-down
    that beats the chip's 45 k) says the idle level was chosen."""
    k, payload = _c3(tmp_path, [("J4", "5", "Pin_5_5"), ("R13", "1", "")],
                     {"J4": "Conn_02x08", "R13": "10k"})
    assert "reset_pull_offboard" not in k
    pulls = payload["checked"][0]["reset_pulls"]
    assert [p for p in pulls if p["name"] == "IO6"]


def test_c3_gpio6_on_a_jtag_header_is_quiet(tmp_path):
    k, _ = _c3(tmp_path, [("J9", "3", "TCK_3")], {"J9": "JTAG"},
               net="/MTCK")
    assert "reset_pull_offboard" not in k


def test_c3_gpio6_drives_a_fet_gate_on(tmp_path):
    """The on-board shape: GPIO6 through 1 k to a 2N7002's gate, no
    pull-down, so the chip's pull-up turns the strip's low side on."""
    c = {"R1": "1k", "Q1": "2N7002"}
    pcb_nodes = [("R1", "1", "")]
    k, _ = _c3(tmp_path, pcb_nodes, c)
    assert not k  # the gate isn't wired yet: R1's far side is open
    tmp2 = tmp_path / "b"
    tmp2.mkdir()
    comps = {"U4": "ESP32-C3-WROOM-02-N4", "R1": "1k", "Q1": "2N7002"}
    nets = {"+3.3V": [("U4", "1", "3V3_1")], "GND": [("Q1", "2", "S")],
            "GPIO6": [("U4", "5", "GPIO6_5"), ("R1", "1", "")],
            "/LED_GATE": [("R1", "2", ""), ("Q1", "1", "G")]}
    pcb = _netlist(tmp2, comps, nets)
    payload, _ = check_straps.run(["--pcb", str(pcb), "--netlist",
                                   str(tmp2 / "s.net")])
    (v,) = _by_kind(payload)["load_on_at_reset"]
    assert v["refs"] == ["Q1", "U4"] and v["volts"] > 3.0
    # a 10 k pull-down on the gate holds it at 0.6 V, under VGS(th)
    tmp3 = tmp_path / "c"
    tmp3.mkdir()
    comps["R2"] = "10k"
    nets["/LED_GATE"].append(("R2", "1", ""))
    nets["GND"].append(("R2", "2", ""))
    pcb = _netlist(tmp3, comps, nets)
    payload, _ = check_straps.run(["--pcb", str(pcb), "--netlist",
                                   str(tmp3 / "s.net")])
    assert "load_on_at_reset" not in _by_kind(payload)


def test_c3_gpio6_on_a_load_switch_enable(tmp_path):
    k, _ = _c3(tmp_path, [("U8", "3", "EN_3")], {"U8": "TPS22918"})
    (v,) = k["load_on_at_reset"]
    assert v["part_pin"] == "EN"
    # an active-low enable pulled high leaves its part off
    tmp2 = tmp_path / "b"
    tmp2.mkdir()
    k, _ = _c3(tmp2, [("U8", "3", "~{EN}_3")], {"U8": "AP2553"})
    assert "load_on_at_reset" not in k


def test_part_on_polarity():
    assert check_straps.part_on("EN", False, "high") is True
    assert check_straps.part_on("EN", True, "high") is False
    assert check_straps.part_on("NEN", False, "low") is True
    assert check_straps.part_on("SHDN", False, "high") is True
    assert check_straps.part_on("SHDN", False, "low") is False
    assert check_straps.part_on("DRVOFF", False, "low") is True
    assert check_straps.part_on("SDA", False, "high") is None
    assert check_straps.part_on("ONE", False, "high") is None
