"""netlist_audit precision: the hi-fi receiver's false findings, at the source.

Each test plants the shape a real pinned board has (where the rule used to
fire falsely) next to the fault the rule is for, and asserts the first stays
quiet while the second still fires:

  missing_ref         core H1-H4: mounting holes marked board_only on the
                      board, never in the schematic
  power_no_consumers  core/lite-main +3V3A: DAC/codec supply pins (AVCC_L,
                      CPVDD, VDDA, HPVDD) the symbols type passive
  power_no_consumers  ultra-front /BL_K: backlight cathode switched by Q1
  power_undeclared    ultra-front /IR_VCC: an RC-filtered tap of +3V3
  power_undeclared    haos-voice /power_in/BUCK_BOOT: a buck's bootstrap net
  diffpair_unpaired   ultra-front /FRONT_INT_N: an active-low interrupt
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import netlist_audit as na  # noqa: E402


def _netlist(nets: dict, comps=()) -> str:
    """Synthetic kicadsexpr netlist; a node is (ref, pin, pintype[, func])."""
    comp_s = "".join(f'(comp (ref "{r}") (value "{v}") (footprint "{f}"))'
                     for r, v, f in comps)
    net_s = ""
    for i, (name, nodes) in enumerate(nets.items(), 1):
        node_s = ""
        for node in nodes:
            r, p, t = node[:3]
            func = f' (pinfunction "{node[3]}")' if len(node) > 3 else ""
            node_s += f'(node (ref "{r}") (pin "{p}"){func} (pintype "{t}"))'
        net_s += f'(net (code "{i}") (name "{name}") {node_s})'
    return f'(export (version "E") (components {comp_s}) (nets {net_s}))'


def _audit(tmp_path, nets, constraints, comps=(), pcb_text=None, tag="a"):
    d = tmp_path / tag
    d.mkdir()
    (d / "board.net").write_text(_netlist(nets, comps), encoding="utf-8")
    (d / "constraints.json").write_text(json.dumps(constraints),
                                        encoding="utf-8")
    argv = ["--constraints", str(d / "constraints.json")]
    if pcb_text is not None:
        (d / "board.kicad_pcb").write_text(pcb_text, encoding="utf-8")
        argv += ["--pcb", str(d / "board.kicad_pcb")]
    else:
        argv += ["--netlist", str(d / "board.net")]
    payload, _ = na.run(argv)
    return payload


def _fired(payload, kind):
    return sorted((v.get("net") or "", tuple(v.get("refs") or ()))
                  for v in payload["violations"] if v["kind"] == kind)


def _fp(ref, lib, attrs):
    return (f'\t(footprint "{lib}"\n\t\t(layer "F.Cu")\n'
            f'\t\t(property "Reference" "{ref}"\n\t\t\t(at 0 0 0)\n\t\t)\n'
            f'\t\t(attr {attrs})\n'
            f'\t\t(pad "1" thru_hole circle (at 0 0) (size 6 6))\n\t)\n')


# ------------------------------------------------------------ missing_ref

_REF_NETS = {"/A": [("U1", "1", "passive"), ("R1", "1", "passive")],
             "GND": [("U1", "2", "passive"), ("R1", "2", "passive")]}
_REF_COMPS = [("U1", "MCU", "LQFP"), ("R1", "10k", "R0603")]
_REF_CONS = {"placement": {"fixed": ["H1", "H2", "U9"],
                           "edges": [{"ref": "U1", "edge": "left"}]}}
_REF_PCB = ("(kicad_pcb\n"
            + _fp("H1", "MountingHole:MountingHole_3.2mm_M3_Pad",
                  "board_only exclude_from_pos_files exclude_from_bom")
            + _fp("H2", "MountingHole:MountingHole_3.2mm_M3",
                  "board_only exclude_from_pos_files exclude_from_bom")
            # U9 on the board but not marked board_only: a part the
            # schematic lost (a rename/split) - the rule's real target
            + _fp("U9", "Package_SO:SOIC-8", "smd")
            + ")\n")


def test_board_only_mounting_holes_are_not_missing_refs(tmp_path):
    payload = _audit(tmp_path, _REF_NETS, _REF_CONS, _REF_COMPS,
                     pcb_text=_REF_PCB)
    assert _fired(payload, "missing_ref") == [("", ("U9",))]


def test_without_the_board_a_hole_ref_still_fires(tmp_path):
    # the netlist alone can't tell a mounting hole from a lost part
    payload = _audit(tmp_path, _REF_NETS, _REF_CONS, _REF_COMPS)
    assert {r for _n, (r,) in _fired(payload, "missing_ref")} == {
        "H1", "H2", "U9"}


def test_board_only_refs_reads_both_reference_spellings(tmp_path):
    pcb = tmp_path / "old.kicad_pcb"
    pcb.write_text("(kicad_pcb\n  (footprint \"MountingHole:MH\"\n"
                   "    (attr board_only)\n"
                   "    (fp_text reference \"MH3\" (at 0 0))\n  )\n"
                   "  (footprint \"R:R0603\"\n    (attr smd)\n"
                   "    (fp_text reference \"R7\" (at 0 0))\n  )\n)\n",
                   encoding="utf-8")
    assert na._board_only_refs(pcb) == {"MH3"}
    assert na._board_only_refs(tmp_path / "absent.kicad_pcb") == set()


# ------------------------------------------------------------ supply pins typed passive

def _rail(consumer):
    """+3V3A: LDO U6 output, two decoupling caps, and the consumer node."""
    return {"+3V3A": [("U6", "5", "passive", "OUT"),
                      ("C19", "1", "passive"), ("C50", "1", "passive"),
                      consumer],
            "+5V": [("U6", "1", "power_in", "VIN"), ("U10", "1", "passive")],
            "/X": [("U6", "3", "passive", "EN"), ("U10", "2", "passive")],
            "GND": [("U6", "2", "power_in", "GND"), ("C19", "2", "passive"),
                    ("C50", "2", "passive"), ("U10", "3", "passive"),
                    ("R5", "2", "passive")],
            "/Y": [("R5", "1", "passive")]}


_RAIL_CONS = {"power": [{"net": "+3V3A", "current_a": 0.05},
                        {"net": "+5V", "current_a": 0.2}]}


def test_dac_supply_pins_typed_passive_are_consumers(tmp_path):
    for i, func in enumerate(("AVCC_L", "CPVDD", "VDDA", "HPVDD", "VCCA")):
        payload = _audit(tmp_path, _rail(("U10", "15", "passive", func)),
                         _RAIL_CONS, tag=f"s{i}")
        assert _fired(payload, "power_no_consumers") == [], func
        fact = {p["net"]: p for p in payload["power"]}["+3V3A"]
        assert fact["supply_pins_typed_passive"] == ["U10"]


def test_dead_rail_with_only_caps_and_a_non_supply_pin_still_fires(tmp_path):
    # the IC's pin on the rail is a signal (OUTL), not a supply: dead rail
    payload = _audit(tmp_path, _rail(("U10", "15", "passive", "OUTL")),
                     _RAIL_CONS)
    assert [n for n, _r in _fired(payload, "power_no_consumers")] == [
        "+3V3A"]


def test_a_two_pin_part_named_vdd_is_not_a_consumer(tmp_path):
    # R5's far pin is /Y, a dangling net - but R5 is a 2-pin part, so a
    # 'VDD' pin name on it says nothing; the series-escape rule judges it
    nets = _rail(("C51", "1", "passive", "VDD"))
    nets["GND"].append(("C51", "2", "passive"))
    payload = _audit(tmp_path, nets, _RAIL_CONS)
    assert [n for n, _r in _fired(payload, "power_no_consumers")] == [
        "+3V3A"]


# ------------------------------------------------------------ switched rail

def _bl(gate_net):
    return {"/BL_K": [("J2", "11", "passive"), ("Q1", "3", "passive", "D")],
            gate_net: [("Q1", "1", "passive", "G")]
            + ([("R9", "1", "passive")] if gate_net == "/BL_GATE" else []),
            "GND": [("Q1", "2", "passive", "S"), ("R9", "2", "passive")]}


def test_low_side_switched_backlight_cathode_has_a_consumer(tmp_path):
    payload = _audit(tmp_path, _bl("/BL_GATE"),
                     {"power": [{"net": "/BL_K", "current_a": 0.06}]})
    assert _fired(payload, "power_no_consumers") == []
    fact = {p["net"]: p for p in payload["power"]}["/BL_K"]
    assert fact["passive_fed_via"] == ["Q1"]


def test_transistor_with_its_gate_tied_off_is_no_escape(tmp_path):
    for i, gate in enumerate(("GND", "unconnected-(Q1-Pad1)")):
        nets = _bl(gate)
        if gate == "GND":
            nets = {"/BL_K": nets["/BL_K"],
                    "GND": nets["GND"] + [("Q1", "1", "passive", "G")]}
        payload = _audit(tmp_path, nets,
                         {"power": [{"net": "/BL_K", "current_a": 0.06}]},
                         tag=f"g{i}")
        assert [n for n, _r in _fired(payload, "power_no_consumers")] == [
            "/BL_K"], gate


# ------------------------------------------------------------ filtered sub-rail

def _ir(via_ref, upstream):
    return {"/IR_VCC": [("C5", "1", "passive"), (via_ref, "2", "passive"),
                        ("U2", "4", "power_in", "VCC")],
            upstream: [(via_ref, "1", "passive"),
                       ("U2", "2", "passive", "OUT")],
            "GND": [("C5", "2", "passive"), ("U2", "3", "power_in", "GND")]}


def test_rc_filtered_tap_of_a_declared_rail_is_declared(tmp_path):
    for i, via in enumerate(("R10", "L3", "FB1")):
        payload = _audit(tmp_path, _ir(via, "+3V3"),
                         {"power": [{"net": "+3V3", "current_a": 0.1}]},
                         tag=f"f{i}")
        assert _fired(payload, "power_undeclared") == [], via


def test_tap_of_an_undeclared_net_or_through_a_cap_still_fires(tmp_path):
    cons = {"power": [{"net": "+3V3", "current_a": 0.1}]}
    # fed through R10 from a net nobody declared
    p1 = _audit(tmp_path, _ir("R10", "/SOMEWHERE"), cons, tag="u1")
    # a cap to +3V3 is no supply path
    p2 = _audit(tmp_path, _ir("C9", "+3V3"), cons, tag="u2")
    for p in (p1, p2):
        assert _fired(p, "power_undeclared") == [("/IR_VCC", ("U2",))]


# ------------------------------------------------------------ bootstrap net

def _boot(cap_to, extra=()):
    """haos-voice U102 (TPS56637): BOOT (7) through C113 to SW (6)."""
    return {"/BUCK_BOOT": [("C113", "1", "passive"),
                           ("U102", "7", "power_in", "BOOT"), *extra],
            cap_to: [("C113", "2", "passive"), ("L101", "1", "passive"),
                     ("U102", "6", "passive", "SW")],
            "+9V": [("U102", "2", "power_in", "VIN"), ("R9", "1", "passive")],
            "/OTHER": [("R9", "2", "passive")],
            "GND": [("U102", "3", "power_in", "PGND")]}


def test_bootstrap_net_rides_on_the_switch_node(tmp_path):
    for i, (name, func) in enumerate((("BOOT", "SW"), ("BST", "LX"),
                                      ("BS", "PH"))):
        nets = _boot("/BUCK_SW")
        nets["/BUCK_BOOT"][1] = ("U102", "7", "power_in", name)
        nets["/BUCK_SW"][2] = ("U102", "6", "passive", func)
        payload = _audit(tmp_path, nets, {"power": [
            {"net": "+9V", "current_a": 1.0}]}, tag=f"b{i}")
        assert _fired(payload, "power_undeclared") == [], name


def test_boot_pin_not_on_its_own_switch_node_still_fires(tmp_path):
    cons = {"power": [{"net": "+9V", "current_a": 1.0}]}
    # the cap goes to a net without the part's switch pin
    nets = _boot("/BUCK_SW")
    nets["/BUCK_SW"] = nets["/BUCK_SW"][:2]
    nets["/BUCK_SW2"] = [("U102", "6", "passive", "SW")]
    p1 = _audit(tmp_path, nets, cons, tag="n1")
    # something else hangs on the boot net
    p2 = _audit(tmp_path, _boot("/BUCK_SW", [("R9", "3", "passive")]),
                cons, tag="n2")
    # a plain supply pin, not a boot pin, through a cap to SW
    nets3 = _boot("/BUCK_SW")
    nets3["/BUCK_BOOT"][1] = ("U102", "7", "power_in", "VCC")
    p3 = _audit(tmp_path, nets3, cons, tag="n3")
    for p in (p1, p2, p3):
        assert _fired(p, "power_undeclared") == [("/BUCK_BOOT", ("U102",))]


# ------------------------------------------------------------ active-low _N

def test_active_low_interrupt_is_not_half_a_pair(tmp_path):
    nets = {name: [("U1", str(i), "passive"), ("J1", str(i), "passive")]
            for i, name in enumerate(
                ("/FRONT_INT_N", "/CODEC_RST_N", "/SPI_CS_N", "/PWR_EN_N",
                 "/LVDS_CLK_N", "/USB_DP", "/AUD_P"), 1)}
    payload = _audit(tmp_path, nets, {})
    # real half-pairs still fire; the active-low controls don't
    assert [n for n, _r in _fired(payload, "diffpair_unpaired")] == [
        "/AUD_P", "/LVDS_CLK_N", "/USB_DP"]
