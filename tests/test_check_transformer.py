"""check_transformer (issue #644): a transformer's phasing, bridges and turns
against its maker's drawing, on synthetic netlists cut from hw-lab
mains-psu's flyback (the board cold review 1 caught wired as a forward)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import check_transformer  # noqa: E402
import checklib  # noqa: E402
import verify_all  # noqa: E402

from test_antenna_straps import _netlist  # noqa: E402


def _run(tmp_path, comps, nets, constraints=None):
    pcb = _netlist(tmp_path, comps, nets)
    argv = ["--pcb", str(pcb), "--netlist", str(tmp_path / "s.net")]
    if constraints is not None:
        c = tmp_path / "constraints.json"
        c.write_text(json.dumps(constraints), encoding="utf-8")
        argv += ["--constraints", str(c)]
    payload, _ = check_transformer.run(argv)
    return payload


def _kinds(payload):
    return sorted(v["kind"] for v in payload["violations"])


def _flyback(bulk="1", drain="3", t1={"8": "/T1_S", "10": "/T1_BR",
                                      "11": "/T1_BR", "13": "GND_S"},
             aux_anode="5", rect="D5", rect_value="STPS5H100"):
    """mains-psu's 750314727 flyback cut down: Q1's drain and HV_BULK on the
    primary, D4 off the aux through R11 4R7, D5 off the secondary, a TVS D6
    across the output (on the return end) and a snubber R14 on T1_S."""
    aux_end = "4" if aux_anode == "5" else "5"
    comps = {"T1": "750314727", "Q1": "IPD60R1K4C6", "C1": "47u",
             "D4": "BAV21W", "R11": "4R7", "R9": "90k9", "U1": "UCC28740",
             rect: rect_value, "D6": "SMBJ15A", "C2": "1000u", "C3": "100n",
             "R14": "47R", "C4": "1n"}
    nets = {
        "HV_BULK": [("T1", bulk, ""), ("C1", "1", "")],
        "/flyback/DRAIN": [("T1", drain, ""), ("Q1", "2", "D")],
        "PGND": [("Q1", "3", "S"), ("C1", "2", ""), ("T1", aux_end, ""),
                 ("U1", "4", "GND_4")],
        "/flyback/AUX": [("T1", aux_anode, ""), ("R11", "1", ""),
                         ("R9", "1", "")],
        "/flyback/AUX_R": [("R11", "2", ""), ("D4", "1", "A")],
        "VDD": [("D4", "2", "K"), ("U1", "1", "VDD_1")],
        "/+12V_RAW": [(rect, "2", "K"), ("D6", "2", "K"), ("C2", "1", ""),
                      ("C3", "1", "")],
        "/T1_S": [(rect, "1", "A"), ("R14", "1", "")],
        "/SNUB": [("R14", "2", ""), ("C4", "1", "")],
        "GND_S": [("D6", "1", "A"), ("C2", "2", ""), ("C3", "2", ""),
                  ("C4", "2", "")],
    }
    for pin, net in t1.items():
        nets.setdefault(net, []).append(("T1", pin, ""))
    return comps, nets


def test_mains_psu_as_fixed_passes(tmp_path):
    p = _run(tmp_path, *_flyback())
    assert p["status"] == "pass", p["violations"]
    fact = p["checked"][0]["transformers"][0]
    assert fact["switch"] == {"pin": "3", "at": "Q1.D", "dotted": True}
    sec = [w for w in fact["windings"] if w["windings"] == ["sec_a", "sec_b"]]
    assert sec[0]["turns"] == 7 and sec[0]["wired"] == "flyback"
    assert sec[0]["rectifier"] == ["D5"]
    aux = [w for w in fact["windings"] if w["windings"] == ["aux"]]
    assert aux[0]["rectifier"] == ["D4"] and aux[0]["anode_dotted"]


def test_mains_psu_as_reviewed_is_a_forward_with_a_loaded_bridge(tmp_path):
    """Cold review 1 finding 1: bulk on dotted 3, the drain on 1, the
    rectifier's anode on dotted 8, 10 and 11 on GND_S and 13 open."""
    p = _run(tmp_path, *_flyback(bulk="3", drain="1",
                                 t1={"8": "/T1_S", "10": "GND_S",
                                     "11": "GND_S"}))
    kinds = _kinds(p)
    assert kinds.count("xfmr_phase") == 2  # the 12 V winding and the aux
    assert "xfmr_bridge_loaded" in kinds
    assert "xfmr_winding_open" in kinds  # 13 open: sec_b out of circuit
    phase = [v for v in p["violations"] if v["kind"] == "xfmr_phase"]
    assert {r for v in phase for r in v["refs"]} == {"T1", "D4", "D5"}
    assert all(v["wired"] == "forward" for v in phase)


def test_swapping_the_secondary_alone_is_caught_too(tmp_path):
    """The anode on the undotted end with the drain on dotted 3."""
    p = _run(tmp_path, *_flyback(t1={"13": "/T1_S", "10": "/T1_BR",
                                     "11": "/T1_BR", "8": "GND_S"}))
    assert _kinds(p) == ["xfmr_phase"]
    assert sorted(p["violations"][0]["refs"]) == ["D5", "T1"]


def test_declared_forward_turns_the_verdict_round(tmp_path):
    p = _run(tmp_path, *_flyback(), constraints={
        "transformers": {"T1": {"topology": "forward"}}})
    assert _kinds(p) == ["xfmr_phase", "xfmr_phase"]


def test_turns_follow_the_wiring(tmp_path):
    """The review's second finding: the 12 V winding on 8-11 (5 turns),
    where the drawing's bracket is 8-13 with 10-11 bridged (7)."""
    cons = {"transformers": {"T1": {"turns": {"8-13": 7, "5-4": 11}}}}
    assert _kinds(_run(tmp_path, *_flyback(), constraints=cons)) == []
    p = _run(tmp_path, *_flyback(t1={"8": "/T1_S", "11": "GND_S"}),
             constraints=cons)
    assert _kinds(p) == ["xfmr_bridge_missing", "xfmr_turns"]
    turns = [v for v in p["violations"] if v["kind"] == "xfmr_turns"][0]
    assert "8-11 5 turns" in turns["msg"]
    p = _run(tmp_path, *_flyback(), constraints={
        "transformers": {"T1": {"turns": {"8-13": 5}}}})
    assert _kinds(p) == ["xfmr_turns"]
    assert p["violations"][0]["turns"] == 7


def test_bridge_missing_and_shorted_winding(tmp_path):
    comps, nets = _flyback(t1={"8": "/T1_S", "10": "/T1_BR", "13": "GND_S"})
    nets["/T1_BR"].append(("R99", "1", ""))
    nets["GND_S"].append(("R99", "2", ""))
    comps["R99"] = "1k"
    assert "xfmr_bridge_missing" in _kinds(_run(tmp_path, comps, nets))
    p = _run(tmp_path, *_flyback(t1={"8": "GND_S", "10": "/T1_BR",
                                     "11": "/T1_BR", "13": "GND_S"}))
    assert "xfmr_winding_shorted" in _kinds(p)


def test_synchronous_fet_and_unknown_part_are_not_judged(tmp_path):
    comps, nets = _flyback(rect="Q2", rect_value="BSC093N15")
    p = _run(tmp_path, comps, nets)
    assert p["status"] == "pass"
    assert any("synchronous FET" in s for s in p["not_judged"])
    comps, nets = _flyback()
    comps["T1"] = "XF-0001"
    p = _run(tmp_path, comps, nets)
    assert p["status"] == "pass"
    assert any("no row in reference/transformers.yaml" in s
               for s in p["not_judged"])
    assert p["nothing_to_judge"]


def test_no_switch_found_is_not_judged(tmp_path):
    comps, nets = _flyback()
    nets["/flyback/DRAIN"] = [("T1", "3", ""), ("Q1", "2", "2")]
    p = _run(tmp_path, comps, nets)
    assert p["status"] == "pass"
    assert any("no single switch" in s for s in p["not_judged"])


def test_bad_topology_in_constraints_is_an_error(tmp_path):
    with pytest.raises(checklib.CheckError, match="topology"):
        _run(tmp_path, *_flyback(), constraints={
            "transformers": {"T1": {"topology": "buck"}}})


def test_no_netlist_is_nothing_to_judge(tmp_path):
    pcb = _netlist(tmp_path, {}, {})
    (tmp_path / "s.net").unlink()
    payload, _ = check_transformer.run(["--pcb", str(pcb)])
    assert payload["status"] == "pass"
    assert "no netlist" in payload["nothing_to_judge"]


def test_joins_verify_all():
    assert "check_transformer" in {c["name"] for c in verify_all.CHECKS}


def test_every_row_is_self_consistent():
    for row in check_transformer.load_kb()["transformers"]:
        assert row.get("source"), row["match"]
        assert row.get("topology") in check_transformer.TOPOLOGIES
        pins = [p for w in row["windings"] for p in (w["dot"], w["end"])]
        assert len(pins) == len(set(pins)), row["match"]
        for a, b in row.get("bridges") or []:
            assert {a, b} <= set(pins)
