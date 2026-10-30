"""The real-errata corpus: its manifest, its matcher, and the three
netlist_audit rules its rev-A boards needed (ground_island, pin_net_swap,
sot23_pinout), each on a netlist shaped like the board it came from."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import errata_corpus as ec  # noqa: E402
import netlist_audit as na  # noqa: E402


def netlist(tmp_path, nets, comps):
    """nets: {name: [(ref, pin, pintype, pinfunction)]}; comps: {ref: fp}."""
    cs = " ".join(f'(comp (ref "{r}") (value "{r}") (footprint "{fp}"))'
                  for r, fp in comps.items())
    ns = " ".join(
        f'(net (code "{i}") (name "{n}") ' + " ".join(
            f'(node (ref "{r}") (pin "{p}") (pinfunction "{f}") (pintype "{t}"))'
            for r, p, t, f in nodes) + ")"
        for i, (n, nodes) in enumerate(nets.items(), 1))
    path = tmp_path / "b.net"
    path.write_text(f'(export (version "E") (components {cs}) (nets {ns}))')
    return na.parse_netlist(path)


def kinds(parsed):
    return [(v["kind"], v.get("net"), v.get("refs")) for v in na.audit(parsed, {}, None)[0]]


def test_manifest_has_three_boards_and_every_miss_a_follow_up():
    boards = ec.load()
    assert len(boards) >= 3
    for b in boards:
        assert b["licence"] and b["source"] and len(b["commit"]) == 40
        for d in b["defects"]:
            assert d["source_quote"]


def test_matcher_needs_kind_and_net_or_ref():
    c = {"script": "netlist_audit", "kind": "sot23_pinout", "refs": ["Q1", "Q3"]}
    assert ec.matches({"kind": "sot23_pinout", "refs": ["Q3"]}, c)
    assert not ec.matches({"kind": "sot23_pinout", "refs": ["Q2"]}, c)
    assert not ec.matches({"kind": "dangling_net", "refs": ["Q1"]}, c)
    n = {"script": "netlist_audit", "kind": "ground_island", "net": "/MIC_GND"}
    assert ec.matches({"kind": "ground_island", "net": "/MIC_GND"}, n)
    assert not ec.matches({"kind": "ground_island", "net": "/AGND"}, n)


def test_outcome_reads_fixed_and_regressed():
    d = {"verdict": "caught", "catch": {"script": "netlist_audit", "kind": "x"}}
    assert ec.outcome(d, {"ran": True, "rev_a": True, "rev_b": False}).startswith("caught")
    assert "REGRESSED" in ec.outcome(d, {"ran": True, "rev_a": True, "rev_b": True})
    assert "REGRESSED" in ec.outcome(d, {"ran": True, "rev_a": False, "rev_b": False})


def test_ground_island_fires_on_a_local_label_ground(tmp_path):
    # digits v1: MIC_GND joins C3, J8, J9 and nothing else
    nets = {"GND": [("U1", "3", "power_in", "GND"), ("C1", "2", "passive", "")],
            "/MIC_GND": [("C3", "2", "passive", ""), ("J8", "4", "passive", ""),
                         ("J9", "3", "passive", "")],
            "/MIC": [("C3", "1", "passive", ""), ("J8", "3", "passive", "")]}
    assert ("ground_island", "/MIC_GND", ["C3", "J8", "J9"]) in kinds(
        netlist(tmp_path, nets, {}))


def test_ground_island_quiet_when_a_net_tie_or_power_pin_joins_it(tmp_path):
    nets = {"GND": [("U1", "3", "power_in", "GND"), ("R9", "2", "passive", "")],
            "AGND": [("R9", "1", "passive", ""), ("C3", "2", "passive", "")],
            "GND_ISO": [("U2", "5", "power_in", "GND2"), ("C4", "2", "passive", "")],
            "CHASSIS_GND": [("H1", "1", "passive", ""), ("C5", "2", "passive", "")]}
    assert not [k for k in kinds(netlist(tmp_path, nets, {})) if k[0] == "ground_island"]


def test_pin_net_swap_sda_on_scl(tmp_path):
    # pandore rev A0: CTL.SDA lands on the CS4272's SCL pin
    nets = {"/Audio/CODEC/CTL.SDA": [("U24", "7", "bidirectional", "SDA2"),
                                     ("U11", "11", "input", "SCL")],
            "/Audio/CODEC/CTL.SCL": [("U24", "6", "bidirectional", "SCL2"),
                                     ("U11", "12", "bidirectional", "SDA")],
            "I2C_SDA": [("U3", "1", "bidirectional", "SDA")]}
    ks = [k for k in kinds(netlist(tmp_path, nets, {})) if k[0] == "pin_net_swap"]
    assert sorted(k[1] for k in ks) == ["/Audio/CODEC/CTL.SCL", "/Audio/CODEC/CTL.SDA"]


def test_sot23_pinout_gds_on_stock_footprint(tmp_path):
    # oasis v0.2: Q_NMOS_GDS (pinfunction G_1, D_2, S_3) on SOT-23-3
    def q(ref):
        return {f"{ref}g": [(ref, "1", "input", "G_1")],
                f"{ref}d": [(ref, "2", "passive", "D_2")],
                f"{ref}s": [(ref, "3", "passive", "S_3")]}
    nets = {**q("Q1"), **q("Q2"), **q("Q3")}
    comps = {"Q1": "Package_TO_SOT_SMD:SOT-23-3",
             "Q2": "lib:SOT-23-3-pinswapped-mosfet",  # oasis v0.3's fix
             "Q3": "Package_TO_SOT_SMD:SOT-23"}
    ks = [k[2] for k in kinds(netlist(tmp_path, nets, comps)) if k[0] == "sot23_pinout"]
    assert ks == [["Q1"], ["Q3"]]


def test_sot23_pinout_quiet_on_gsd_and_bec(tmp_path):
    nets = {"a": [("Q1", "1", "input", "G"), ("Q2", "1", "input", "B")],
            "b": [("Q1", "2", "passive", "S"), ("Q2", "2", "passive", "E")],
            "c": [("Q1", "3", "passive", "D"), ("Q2", "3", "passive", "C")]}
    comps = {"Q1": "Package_TO_SOT_SMD:SOT-23", "Q2": "Package_TO_SOT_SMD:SOT-23-3"}
    assert not [k for k in kinds(netlist(tmp_path, nets, comps)) if k[0] == "sot23_pinout"]


def test_every_catch_names_a_check_the_corpus_runs():
    # a catch for a script with no runner would read "not run here" forever
    for b in ec.load():
        for d in b["defects"]:
            if d.get("catch"):
                assert d["catch"]["script"] in ec.RUNNERS, d["id"]
