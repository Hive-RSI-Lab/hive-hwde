"""check_cap_load: an op-amp output into a cable or big cap, with its phase
margin (reference/ingest/analog-audio.md gap 2, rules A2/A6)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import analogcalc  # noqa: E402
import check_cap_load as ccl  # noqa: E402
import constraints_lint  # noqa: E402

PCB = REPO / "tests" / "golden" / "usbbuck4" / "usbbuck4.kicad_pcb"


def _netlist(tmp_path, comps: dict, nets: dict) -> Path:
    """comps {ref: value}; nets {name: [(ref, pin, pinfunction)]}."""
    cs = "".join(f'(comp (ref "{r}") (value "{v}"))\n' for r, v in comps.items())
    ns = "".join(
        f'(net (code "{i}") (name "{n}") ' + " ".join(
            f'(node (ref "{r}") (pin "{p}") (pinfunction "{f}") '
            '(pintype "passive"))' for r, p, f in members) + ")\n"
        for i, (n, members) in enumerate(nets.items(), 1))
    p = tmp_path / "t.net"
    p.write_text(f"(export (version \"E\")\n(components\n{cs})\n(nets\n{ns}))\n",
                 encoding="utf-8")
    return p


def _line_out(tmp_path, part="NE5532", riso=None, jack=True, cap_gnd=None,
              feedback="follower"):
    """An op-amp output A through 10 uF (and riso when given) to a jack."""
    comps = {"U1": part, "C1": "10uF"}
    out = [("U1", "1", "~"), ("C1", "1", "")]
    inv = [("U1", "2", "-")]
    nets = {"+12V": [("U1", "8", "V+")], "-12V": [("U1", "4", "V-")],
            "GND": [], "/IN": [("U1", "3", "+")]}
    if feedback == "follower":
        out += inv
    else:
        comps.update(R10="10k", R11="10k")
        out.append(("R10", "1", ""))
        nets["/FB"] = inv + [("R10", "2", ""), ("R11", "1", "")]
        nets["GND"].append(("R11", "2", ""))
    nets["/OUT"] = out
    load = "/AC"
    nets["/AC"] = [("C1", "2", "")]
    if riso is not None:
        comps["R1"] = riso
        nets["/AC"].append(("R1", "1", ""))
        nets["/JACK"] = [("R1", "2", "")]
        load = "/JACK"
    if jack:
        comps["J1"] = "AudioJack2"
        nets[load].append(("J1", "1", "T"))
        nets["GND"].append(("J1", "2", "S"))
    if cap_gnd:
        comps["C2"] = cap_gnd
        nets[load].append(("C2", "1", ""))
        nets["GND"].append(("C2", "2", ""))
    return _netlist(tmp_path, comps, nets)


def _kinds(viols):
    return [(v["kind"], v["severity"]) for v in viols]


def test_ne5532_into_cable_no_buildout_is_an_error(tmp_path):
    viols, seen = ccl.check(PCB, _line_out(tmp_path))
    assert _kinds(viols) == [("cap_load_pm", "error")]
    v = viols[0]
    want = analogcalc.cap_load_pm(10e6, 50, 1e-9)["phase_margin_deg"]
    assert v["phase_margin_deg"] == pytest.approx(want, abs=0.1)
    assert v["phase_margin_deg"] < 45
    assert "U1" in v["refs"] and "J1" in v["refs"]
    assert v["riso_for_60deg"] > 0 and not v["part_verified"]
    assert "Zo plot" in v["msg"] and "no build-out" in v["msg"]
    assert seen["outputs"][0]["noise_gain_from"] == "follower"


def test_buildout_from_the_calc_passes(tmp_path):
    need = analogcalc.riso_for_pm(10e6, 50, 1e-9, 1.0, 60.0)
    viols, seen = ccl.check(PCB, _line_out(tmp_path, riso="47R"))
    assert need < 47 and viols == []
    out = [o for o in seen["outputs"] if o["riso_ref"] == "R1"]
    assert out and out[0]["phase_margin_deg"] >= 60


def test_too_small_a_buildout_still_flags_with_its_value(tmp_path):
    viols, _ = ccl.check(PCB, _line_out(tmp_path, riso="4R7"))
    assert [v["kind"] for v in viols] == ["cap_load_pm"]
    assert viols[0]["riso_ohm"] == pytest.approx(4.7)
    assert "through R1" in viols[0]["msg"]


def test_noise_gain_from_the_feedback_network(tmp_path):
    viols, seen = ccl.check(PCB, _line_out(tmp_path, feedback="g2"))
    o = seen["outputs"][0]
    assert o["noise_gain"] == pytest.approx(2.0)
    want = analogcalc.cap_load_pm(10e6, 50, 1e-9, 2.0)["phase_margin_deg"]
    assert o["phase_margin_deg"] == pytest.approx(want, abs=0.1)
    # the feedback resistor is not taken for a build-out
    assert all(o["riso_ref"] != "R10" for o in seen["outputs"])


def test_big_cap_to_ground_no_jack(tmp_path):
    viols, _ = ccl.check(PCB, _line_out(tmp_path, jack=False, cap_gnd="2.2nF"))
    assert _kinds(viols) == [("cap_load_pm", "error")]
    assert viols[0]["cl_pf"] == pytest.approx(2200)


def test_constraints_cable_pf_sets_the_load(tmp_path):
    viols, seen = ccl.check(PCB, _line_out(tmp_path),
                            {"cable_pf": {"J1": 20}})
    assert viols == []
    assert seen["outputs"][0]["cl_f"] == pytest.approx(20e-12)


def test_unknown_opamp_warns_without_buildout(tmp_path):
    nl = _line_out(tmp_path, part="XYZ123")
    viols, _ = ccl.check(PCB, nl)
    assert _kinds(viols) == [("cap_load_unknown", "warning")]
    viols, _ = ccl.check(PCB, _line_out(tmp_path, part="XYZ123", riso="1k"))
    assert viols == []


def test_no_load_no_finding(tmp_path):
    viols, seen = ccl.check(PCB, _line_out(tmp_path, jack=False))
    assert viols == [] and seen["opamps"] == ["U1"]


def test_cable_pf_lints(tmp_path):
    def lint(doc):
        p = tmp_path / "constraints.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        return [v for v in constraints_lint.lint_file(p)[0]
                if "cable_pf" in v["msg"]]
    assert not lint({"cable_pf": {"J1": 1000}})
    assert lint({"cable_pf": {"J1": "1n"}})
