"""netlist_audit opamp_input_no_dc_path / opamp_input_dc_via_connector_only /
polarized_cap_reverse (reference/ingest/analog-audio.md): an amplifier input
with no DC path for its bias current, and an electrolytic reverse biased at
DC. Each case is the circuit the rule is about, drawn the way KiCad exports
it."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import netlist_audit as na  # noqa: E402

KINDS = ("opamp_input_no_dc_path", "opamp_input_dc_via_connector_only",
         "polarized_cap_reverse")


def netlist(tmp_path, nets, values, libs=None, fps=None):
    """nets: {name: [(ref, pin, pintype, pinfunction)]}; values {ref: value};
    libs {ref: (lib, part)}; fps {ref: footprint}."""
    libs, fps = libs or {}, fps or {}
    refs = sorted({r for nodes in nets.values() for r, *_ in nodes})
    cs = " ".join(
        f'(comp (ref "{r}") (value "{values.get(r, r)}") '
        f'(footprint "{fps.get(r, "x")}")'
        + (f' (libsource (lib "{libs[r][0]}") (part "{libs[r][1]}"))'
           if r in libs else "") + ")" for r in refs)
    ns = " ".join(
        f'(net (code "{i}") (name "{n}") ' + " ".join(
            f'(node (ref "{r}") (pin "{p}") (pinfunction "{f}") '
            f'(pintype "{t}"))' for r, p, t, f in nodes) + ")"
        for i, (n, nodes) in enumerate(nets.items(), 1))
    path = tmp_path / "b.net"
    path.write_text(f'(export (version "E") (components {cs}) (nets {ns}))')
    return na.parse_netlist(path)


def found(parsed, constraints=None):
    vios, facts = na.audit(parsed, constraints or {}, None)
    return [v for v in vios if v["kind"] in KINDS], facts


OPAMP = ("Amplifier_Operational", "NE5532")


def follower(bias=None, coupling="C1"):
    """A line input J1 -> coupling cap -> NE5532 follower; `bias` adds a
    resistor from the + input to that net."""
    nets = {
        "/VCC": [("U1", "8", "power_in", "V+")],
        "/VEE": [("U1", "4", "power_in", "V-")],
        "Net-(J1-Pin_1)": [("J1", "1", "passive", "Pin_1")],
        "GND": [("J1", "2", "passive", "Pin_2")],
        "/IN_P": [("U1", "3", "input", "+")],
        "/OUT": [("U1", "1", "output", ""), ("U1", "2", "input", "-"),
                 ("J2", "1", "passive", "Pin_1")],
    }
    if coupling:
        nets["Net-(J1-Pin_1)"].append((coupling, "1", "passive", ""))
        nets["/IN_P"].append((coupling, "2", "passive", ""))
    else:
        nets["Net-(J1-Pin_1)"] = [("J1", "1", "passive", "Pin_1"),
                                  ("U1", "3", "input", "+")]
        del nets["/IN_P"]
    if bias:
        nets.setdefault(bias, []).append(("R1", "1", "passive", ""))
        tgt = "/IN_P" if coupling else "Net-(J1-Pin_1)"
        nets[tgt].append(("R1", "2", "passive", ""))
    values = {"U1": "NE5532", "C1": "10u", "R1": "100k"}
    return nets, values, {"U1": OPAMP}


def test_ac_coupled_follower_without_bias_resistor_is_an_error(tmp_path):
    vs, facts = found(netlist(tmp_path, *follower()))
    (v,) = vs
    assert v["kind"] == "opamp_input_no_dc_path" and v["severity"] == "error"
    assert v["net"] == "/IN_P" and v["refs"] == ["U1", "C1"]
    assert "C1" in v["msg"] and "resistor" in v["msg"]
    # + and - both checked; - sits on the output and has its path
    assert facts["amp_inputs_dc_checked"] == 2


def test_bias_resistor_to_ground_clears_it(tmp_path):
    vs, _ = found(netlist(tmp_path, *follower(bias="GND")))
    assert vs == []


def test_bias_to_a_divider_midpoint_clears_it(tmp_path):
    nets, values, libs = follower(bias="/VBIAS")
    # single-supply bias point: 10k/10k from +5V
    nets["/VBIAS"] += [("R2", "2", "passive", ""), ("R3", "1", "passive", ""),
                       ("C2", "1", "passive", "")]
    nets["+5V"] = [("R2", "1", "passive", "")]
    nets["GND"] += [("R3", "2", "passive", ""), ("C2", "2", "passive", "")]
    values.update(R2="10k", R3="10k", C2="10u")
    vs, _ = found(netlist(tmp_path, nets, values, libs))
    assert vs == []


def test_bias_through_a_chain_of_resistors_to_another_input_is_still_none(
        tmp_path):
    # R1 runs from + to the other op-amp's + input, itself AC coupled: the
    # chain reaches only inputs and caps
    nets, values, libs = follower(bias="/IN2")
    nets["/IN2"] += [("U1", "5", "input", "+"), ("C3", "2", "passive", "")]
    nets["Net-(J1-Pin_1)"].append(("C3", "1", "passive", ""))
    nets["/OUT2"] = [("U1", "7", "output", ""), ("U1", "6", "input", "-")]
    values["C3"] = "1u"
    vs, _ = found(netlist(tmp_path, nets, values, libs))
    assert {v["net"] for v in vs} == {"/IN_P", "/IN2"}
    assert all(v["kind"] == "opamp_input_no_dc_path" for v in vs)


def test_dc_coupled_straight_from_a_connector_is_a_warning(tmp_path):
    vs, _ = found(netlist(tmp_path, *follower(coupling=None)))
    (v,) = vs
    assert v["kind"] == "opamp_input_dc_via_connector_only"
    assert v["severity"] == "warning" and v["connectors"] == ["J1"]


def test_inverting_stage_reaches_the_output_through_rf(tmp_path):
    nets = {
        "/VCC": [("U1", "8", "power_in", "V+")],
        "/VEE": [("U1", "4", "power_in", "V-")],
        "/IN": [("C1", "1", "passive", ""), ("J1", "1", "passive", "1")],
        "Net-(C1-Pad2)": [("C1", "2", "passive", ""),
                          ("R1", "1", "passive", "")],
        "/SUM": [("R1", "2", "passive", ""), ("R2", "1", "passive", ""),
                 ("U1", "2", "input", "-")],
        "/OUT": [("R2", "2", "passive", ""), ("U1", "1", "output", "")],
        "GND": [("U1", "3", "input", "+")],
    }
    vs, facts = found(netlist(tmp_path, nets, {"U1": "TL072", "C1": "1u",
                                               "R1": "10k", "R2": "10k"}))
    assert vs == [] and facts["amp_inputs_dc_checked"] == 2


def test_clamp_diodes_are_no_bias_path(tmp_path):
    nets, values, libs = follower()
    nets["/IN_P"] += [("D1", "1", "passive", "A"), ("D2", "2", "passive",
                                                    "K")]
    nets["/VCC"].append(("D1", "2", "passive", "K"))
    nets["/VEE"].append(("D2", "1", "passive", "A"))
    vs, _ = found(netlist(tmp_path, nets, values, libs))
    assert [v["kind"] for v in vs] == ["opamp_input_no_dc_path"]


def test_class_d_amp_and_supervisor_are_not_judged(tmp_path):
    # TPA3116 biases its own inputs: AC coupling with no resistor is right;
    # TLV803 is a supervisor, not an op-amp
    nets = {
        "/IN": [("C1", "1", "passive", ""), ("J1", "1", "passive", "1")],
        "/INP": [("C1", "2", "passive", ""), ("U1", "4", "input", "INPL")],
        "/INN": [("C2", "2", "passive", ""), ("U1", "5", "input", "INNL"),
                 ("U2", "3", "input", "-")],
        "GND": [("C2", "1", "passive", ""), ("J1", "2", "passive", "2")],
    }
    vs, facts = found(netlist(
        tmp_path, nets, {"U1": "TPA3116D2", "U2": "TLV803SDBZR"},
        {"U1": ("Amplifier_Audio", "TPA3116D2"), "U2": ("aiee", "TLV803")}))
    assert vs == [] and facts["amp_inputs_dc_checked"] == 0


@pytest.mark.parametrize("plus,minus,fires", [
    ("GND", "+12V", True),      # + side on ground, - on 12 V
    ("+12V", "GND", False),     # the right way round
    ("GND", "+0.3V", False),    # within the 0.5 V allowance
])
def test_polarized_cap_orientation(tmp_path, plus, minus, fires):
    nets = {plus: [("C1", "1", "passive", "")],
            minus: [("C1", "2", "passive", "")]}
    parsed = netlist(tmp_path, nets, {"C1": "470u"},
                     {"C1": ("Device", "C_Polarized")})
    vs, facts = found(parsed)
    assert facts["polarized_caps_checked"] == 1
    assert [v["kind"] for v in vs] == (["polarized_cap_reverse"] if fires
                                       else [])
    if fires:
        assert vs[0]["plus_v"] == 0 and vs[0]["minus_v"] == 12


def test_polarized_coupling_cap_between_two_bias_points(tmp_path):
    # an op-amp stage biased at 2.5 V couples into a load at ground: + must
    # face the 2.5 V side. Drawn backwards: + on the grounded load side.
    nets = {"+5V": [("R1", "1", "passive", "")],
            "/MID": [("R1", "2", "passive", ""), ("R2", "1", "passive", ""),
                     ("C1", "2", "passive", "")],
            "/LOAD": [("C1", "1", "passive", ""), ("R3", "1", "passive", "")],
            "GND": [("R2", "2", "passive", ""), ("R3", "2", "passive", "")]}
    parsed = netlist(tmp_path, nets, {"C1": "10u", "R1": "10k", "R2": "10k",
                                      "R3": "10k"},
                     fps={"C1": "Capacitor_SMD:CP_Elec_5x5.4"})
    (v,) = found(parsed)[0]
    assert v["refs"] == ["C1"] and v["minus_v"] == pytest.approx(2.5)


def test_named_pins_override_pin_one(tmp_path):
    nets = {"+12V": [("C1", "1", "passive", "-")],
            "GND": [("C1", "2", "passive", "+")]}
    parsed = netlist(tmp_path, nets, {"C1": "100u"},
                     {"C1": ("Device", "C_Polarized")})
    assert [v["kind"] for v in found(parsed)[0]] == ["polarized_cap_reverse"]


def test_unknown_level_is_skipped(tmp_path):
    nets = {"/PVDD": [("C1", "1", "passive", ""), ("U1", "1", "output", "")],
            "GND": [("C1", "2", "passive", "")]}
    parsed = netlist(tmp_path, nets, {"C1": "470u", "U1": "X"},
                     {"C1": ("Device", "C_Polarized")})
    vs, facts = found(parsed)
    assert vs == [] and facts["polarized_caps_checked"] == 0


def test_hifi_core_board_is_clean():
    """The pinned hifi-receiver-2 core: INA186 and OPA1678 inputs all have
    their DC paths; the bulk electrolytics sit on PVDD, whose volts only
    constraints name."""
    parsed = na.parse_netlist(ROOT / "pinned" / "core" / "kicad" / "core.net")
    vs, facts = found(parsed)
    assert vs == []
    assert facts["amp_inputs_dc_checked"] == 6
    vs, facts = found(parsed, {"voltages": [{"net": "PVDD", "voltage": 20}]})
    assert vs == [] and facts["polarized_caps_checked"] == 3
