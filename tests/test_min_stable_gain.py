"""netlist_audit below_min_stable_gain: a decompensated op-amp
(reference/opamp_min_gain.yaml) whose noise gain - 1/beta, not the signal
gain - is under its minimum stable gain. A follower is noise gain 1, an
inverting -1 stage 2 (ADI MT-033): both oscillate around an OPA637 (G >= 5)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

import netlist_audit as na  # noqa: E402
from test_input_common_mode import netlist  # noqa: E402


def gain(parsed, constraints=None):
    vios, facts = na.audit(parsed, constraints or {}, None)
    return ({v["net"]: v for v in vios if v["kind"] == "below_min_stable_gain"},
            facts["min_gain_loops_checked"])


def single(part="OPA637AU", fb=None, values=None):
    """A single op-amp (out 6, -in 2, +in 3) on +-15 V, + from J1; fb adds
    the feedback parts: {ref: (net_a, net_b, value)}."""
    nets = {
        "+15V": [("U1", "7", "power_in", "V+")],
        "-15V": [("U1", "4", "power_in", "V-")],
        "GND": [("J1", "2", "passive", "Pin_2")],
        "/IN": [("U1", "3", "input", "+"), ("J1", "1", "passive", "Pin_1")],
        "/FB": [("U1", "2", "input", "-")],
        "/OUT": [("U1", "6", "output", "~"), ("J2", "1", "passive", "Pin_1")],
    }
    vals = {"U1": part}
    for ref, (a, b, val) in (fb or {}).items():
        nets.setdefault(a, []).append((ref, "1", "passive", ""))
        nets.setdefault(b, []).append((ref, "2", "passive", ""))
        vals[ref] = val
    vals.update(values or {})
    return nets, vals


def test_a_follower_round_an_opa637_is_noise_gain_1(tmp_path):
    nets, vals = single()
    nets["/OUT"] += nets.pop("/FB")
    found, checked = gain(netlist(tmp_path, nets, vals))
    assert checked == 1
    hit = found["/OUT"]
    assert hit["refs"] == ["U1"] and hit["severity"] == "warning"
    assert hit["noise_gain"] == 1 and hit["min_gain"] == 5
    assert "OPA627" in hit["msg"] and "not yet re-read" in hit["msg"]


def test_an_inverting_minus_1_stage_is_noise_gain_2_not_1(tmp_path):
    # + to ground, signal in through Rin from J1 (a source: AC ground)
    nets, vals = single(fb={"R1": ("/FB", "/OUT", "10k"),
                            "R2": ("/FB", "/SIG", "10k")})
    nets["/SIG"].append(("J3", "1", "passive", "Pin_1"))
    found, checked = gain(netlist(tmp_path, nets, vals))
    assert checked == 1
    assert found["/FB"]["noise_gain"] == pytest.approx(2.0)


def test_a_gain_of_10_is_quiet_and_5_is_on_the_line(tmp_path):
    for rg, quiet in (("1k", True), ("2.25k", True), ("4k", False)):
        nets, vals = single(fb={"R1": ("/FB", "/OUT", "9k"),
                                "R2": ("/FB", "GND", rg)})
        found, checked = gain(netlist(tmp_path, nets, vals))
        assert checked == 1 and (not found) == quiet, rg


def test_a_feedback_cap_drops_the_noise_gain_at_crossover(tmp_path):
    # G = +10 at DC, but 100 pF across Rf is ~100 ohm at 16 MHz
    nets, vals = single(fb={"R1": ("/FB", "/OUT", "9k"),
                            "R2": ("/FB", "GND", "1k"),
                            "C1": ("/FB", "/OUT", "100p")})
    found, _ = gain(netlist(tmp_path, nets, vals))
    assert found["/FB"]["noise_gain"] < 1.2


def test_an_ac_coupled_gain_leg_counts_at_crossover(tmp_path):
    # Rg to ground through a 10 uF cap: noise gain 1 at DC, 10 where it counts
    nets, vals = single(fb={"R1": ("/FB", "/OUT", "9k"),
                            "R2": ("/FB", "/AC", "1k"),
                            "C1": ("/AC", "GND", "10u")})
    found, checked = gain(netlist(tmp_path, nets, vals))
    assert checked == 1 and not found


def test_ne5534_compensated_by_hand_is_left_alone(tmp_path):
    nets, vals = single(part="NE5534AP")
    nets["/OUT"] += nets.pop("/FB")
    nets["/C8"] = [("U1", "8", "passive", "COMP"), ("C5", "1", "passive", "")]
    nets["/C5"] = [("U1", "5", "passive", "COMP/BAL"), ("C5", "2", "passive", "")]
    found, _ = gain(netlist(tmp_path, nets, {**vals, "C5": "22p"}))
    assert not found
    del nets["/C8"], nets["/C5"]
    found, _ = gain(netlist(tmp_path, nets, vals))
    assert found["/OUT"]["min_gain"] == 3


def test_dual_with_passive_pins_pairs_each_unit(tmp_path):
    # OPA2228, every pin passive (Amplifier_Operational style): unit A at
    # G = +10, unit B a follower
    nets = {
        "+15V": [("U1", "8", "passive", "V+")],
        "-15V": [("U1", "4", "passive", "V-")],
        "GND": [("R2", "2", "passive", ""), ("J1", "2", "passive", "")],
        "/INA": [("U1", "3", "passive", "+"), ("J1", "1", "passive", "")],
        "/FBA": [("U1", "2", "passive", "-"), ("R1", "1", "passive", ""),
                 ("R2", "1", "passive", "")],
        "/OUTA": [("U1", "1", "passive", "~"), ("R1", "2", "passive", ""),
                  ("U1", "5", "passive", "+")],
        "/OUTB": [("U1", "7", "passive", "~"), ("U1", "6", "passive", "-"),
                  ("J2", "1", "passive", "")],
    }
    parsed = netlist(tmp_path, nets, {"U1": "OPA2228PA", "R1": "9k", "R2": "1k"})
    found, checked = gain(parsed)
    assert checked == 2 and set(found) == {"/OUTB"}
    assert found["/OUTB"]["pin"] == "6" and found["/OUTB"]["out_pin"] == "7"


def test_unity_stable_parts_and_unknown_loops_are_not_judged(tmp_path):
    nets, vals = single(part="OPA627AU")
    nets["/OUT"] += nets.pop("/FB")
    assert gain(netlist(tmp_path, nets, vals)) == ({}, 0)
    # feedback through a diode: no passive solve, skipped
    nets, vals = single(fb={"R2": ("/FB", "GND", "1k")})
    nets["/FB"].append(("D1", "1", "passive", "K"))
    nets["/OUT"].append(("D1", "2", "passive", "A"))
    assert gain(netlist(tmp_path, nets, vals)) == ({}, 0)
    # open loop (no feedback at all)
    nets, vals = single()
    assert gain(netlist(tmp_path, nets, vals)) == ({}, 0)


def test_table_rows_parse_and_match_their_families():
    rows = na._min_gain_table()
    for r in rows:
        assert r["min_gain"] > 1 and r["gbw_hz"] > 1e6 and r["source"]
    hit = lambda v: (na._cm_row({"value": v}, rows) or {}).get("match")  # noqa: E731
    assert hit("OPA637AU") and hit("OPA2228PA") and hit("OP37GS")
    assert hit("NE5534AP") and not hit("NE5532AP")
    assert not hit("OP27GS") and not hit("OPA627AU") and not hit("OP370")
    assert hit("LT6200CS6-10#PBF") == hit("LT6200-10")
    assert hit("LT6200-10") and hit("LT6200-5") != hit("LT6200-10")


def test_dc_level_counts_a_resistor_between_two_solved_nodes_once(tmp_path):
    # 3.3V -R1 10k- A -R2 10k- B -R3 10k- GND, B on a comparator input
    nets = {"+3.3V": [("R1", "1", "passive", "")],
            "A": [("R1", "2", "passive", ""), ("R2", "1", "passive", "")],
            "B": [("R2", "2", "passive", ""), ("R3", "1", "passive", "")],
            "GND": [("R3", "2", "passive", "")]}
    parsed = netlist(tmp_path, nets, {"R1": "10k", "R2": "10k", "R3": "10k"})
    comp_pins = {}
    for n, ms in parsed["nets"].items():
        for m in ms:
            comp_pins.setdefault(m["ref"], {})[m["pin"]] = n
    level, _, why = na._dc_level("B", parsed["nets"], parsed["components"],
                                 comp_pins, {"+3.3V": 3.3, "GND": 0.0})
    assert why == "" and level == pytest.approx(1.1)
