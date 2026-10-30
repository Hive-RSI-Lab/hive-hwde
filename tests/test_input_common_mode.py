"""netlist_audit input_common_mode: an op-amp or comparator input whose DC
level, solved from the resistors to known rails, sits outside the part's
input common-mode range (reference/opamp_cm.yaml). Shaped on minuet v3's rain
sensor (errata corpus minuet-2): an LMV331 on 3.3 V whose inputs rest at
3.1-3.3 V, above its VCC-0.7 V."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import netlist_audit as na  # noqa: E402


def netlist(tmp_path, nets, values):
    """nets: {name: [(ref, pin, pintype, pinfunction)]}; values: {ref: value}."""
    refs = sorted({r for nodes in nets.values() for r, *_ in nodes})
    cs = " ".join(f'(comp (ref "{r}") (value "{values.get(r, r)}") (footprint "x"))'
                  for r in refs)
    ns = " ".join(
        f'(net (code "{i}") (name "{n}") ' + " ".join(
            f'(node (ref "{r}") (pin "{p}") (pinfunction "{f}") (pintype "{t}"))'
            for r, p, t, f in nodes) + ")"
        for i, (n, nodes) in enumerate(nets.items(), 1))
    path = tmp_path / "b.net"
    path.write_text(f'(export (version "E") (components {cs}) (nets {ns}))')
    return na.parse_netlist(path)


def cm(parsed, constraints=None):
    vios, facts = na.audit(parsed, constraints or {}, None)
    return ({v["net"]: v for v in vios if v["kind"] == "input_common_mode"},
            facts["common_mode_inputs_checked"])


def minuet(rthr_top="68K", rthr_bot="1M"):
    nets = {
        "+3.3V": [("U2", "5", "power_in", "V+"), ("R10", "2", "passive", ""),
                  ("R9", "1", "passive", ""), ("C10", "1", "passive", "")],
        "GND": [("U2", "2", "power_in", "V-"), ("R14", "1", "passive", ""),
                ("C7", "2", "passive", ""), ("C10", "2", "passive", "")],
        "/RSEN": [("U2", "1", "input", "+"), ("R9", "2", "passive", ""),
                  ("R11", "2", "passive", ""), ("C7", "1", "passive", ""),
                  ("TP1", "1", "passive", "1")],
        "Net-(J3-Pin_2)": [("J3", "2", "passive", "Pin_2"), ("R11", "1", "passive", "")],
        "/RTHR": [("U2", "3", "input", "-"), ("R10", "1", "passive", ""),
                  ("R14", "2", "passive", "")],
        "XIO8": [("U2", "4", "open_collector", ""), ("U3", "10", "bidirectional", "P10")],
    }
    values = {"U2": "LMV331IDCK", "R9": "100K", "R11": "100K", "R10": rthr_top,
              "R14": rthr_bot, "C7": "100n", "C10": "100n", "U3": "TCA9555RTW"}
    return nets, values


def test_minuet_v3_lmv331_inputs_above_vcc_minus_0v7(tmp_path):
    found, checked = cm(netlist(tmp_path, *minuet()))
    assert checked == 2
    thr = found["/RTHR"]
    assert thr["refs"] == ["U2"] and thr["severity"] == "warning"
    assert thr["level_v"] == pytest.approx(3.3 * 1e6 / 1.068e6, abs=1e-3)
    assert thr["vcm_v"] == [0.0, 2.6] and thr["opened"] == []
    sen = found["/RSEN"]
    assert sen["level_v"] == pytest.approx(3.3) and sen["opened"] == ["J3"]
    assert "above the top" in sen["msg"] and "J3 open" in sen["msg"]


def test_quiet_when_the_bias_sits_inside_the_range(tmp_path):
    nets, values = minuet(rthr_top="1M", rthr_bot="1M")
    values["R9"] = "1M"
    values["R11"] = "100K"
    # +: 1M up, 100K to a grounded sensor -> 0.3 V; -: 1.65 V
    nets["Net-(J3-Pin_2)"].append(("R99", "1", "passive", ""))
    nets["GND"].append(("R99", "2", "passive", ""))
    values["R99"] = "0R"
    found, checked = cm(netlist(tmp_path, nets, values))
    assert checked == 2 and not found


def test_a_driven_node_makes_the_level_unknown(tmp_path):
    # comparator hysteresis: output back to + through 1M, so + isn't fixed
    nets, values = minuet()
    nets["XIO8"].append(("R20", "1", "passive", ""))
    nets["/RSEN"].append(("R20", "2", "passive", ""))
    values["R20"] = "1M"
    found, checked = cm(netlist(tmp_path, nets, values))
    assert "/RSEN" not in found and "/RTHR" in found and checked == 1


def test_below_the_range_on_passive_pins_and_a_declared_rail(tmp_path):
    # TL072 on a 12 V rail named only in constraints, every pin passive
    # (Amplifier_Operational style): + held at ground through 10k is under V-+4
    nets = {"/VA": [("U1", "8", "passive", "V+")],
            "GND": [("U1", "4", "passive", "V-"), ("R1", "1", "passive", "")],
            "/IN": [("U1", "3", "passive", "+"), ("R1", "2", "passive", "")]}
    parsed = netlist(tmp_path, nets, {"U1": "TL072CDR", "R1": "10k"})
    found, _ = cm(parsed)
    assert not found  # no voltage for /VA: nothing to compare against
    found, _ = cm(parsed, {"voltages": [{"net": "/VA", "voltage": 12}]})
    assert found["/IN"]["level_v"] == 0 and found["/IN"]["vcm_v"] == [4.0, 8.0]
    assert "below the bottom" in found["/IN"]["msg"]


def test_a_part_not_in_the_table_is_not_judged(tmp_path):
    nets, values = minuet()
    values["U2"] = "XYZ123"
    assert cm(netlist(tmp_path, nets, values)) == ({}, 0)


def test_table_rows_parse_and_match_their_families():
    rows = na._cm_table()
    for r in rows:
        lo, hi = r["vcm"]
        assert r["kind"] in ("opamp", "comparator") and r["source"]
        assert lo <= 4.5 and hi >= -4.5
    hit = lambda v: (na._cm_row({"value": v}, rows) or {}).get("match")  # noqa: E731
    assert hit("LMV331IDCK") == hit("LMV393IDR") != hit("LM393DR")
    assert hit("LM358") and hit("OPA1678IDR") and not hit("LM3580")
